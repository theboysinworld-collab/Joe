"""Núcleo do agente Joe — pipeline de 5 papéis com loop LLM↔ferramentas.

FLUXO PADRÃO (visível no grafo da interface, sempre nesta ordem):
1. intencao   → agents.classify_intent: entende OBJETIVO, não palavra-chave.
2. planejador → monta passos; se houver LLM, ele planeja via prompt; senão,
                planner determinístico offline (a base age sem API).
3. executor   → loop real de tool-calls (OpenAI-format) até resposta final.
4. revisor    → confere resultado vs. plano; 1 retrabalho permitido.
5. conclusao  → síntese + memorização automática do aprendido.

REGRAS DE ARQUITETURA (não violar ao adicionar funções):
- Todo progresso emite evento no bus → a UI nunca pergunta nada, só escuta.
- Toda etapa é try/except com fallback → uma falha parcial nunca mata o turno.
- Memória: RAG do contexto relevante entra no system prompt automaticamente.
- Proatividade: previsões de hábito viram sugestões anexadas à resposta.
- Sub-agentes: Agent(subagent=...) herda o mesmo pipeline com orçamento menor.
"""

from __future__ import annotations

import json
from pathlib import Path

from .config import Config, HISTORY_PATH, AUDIT_LOG
from .llm import LLMError, chat_completion, provider_registry, bootstrap, auto_select
from .memory import Memory, _norm
from .tools.base import registry, bootstrap_tools
from .tools.shell import ShellTool
bootstrap_tools()  # garante que todas as ferramentas embutidas (incl. agent_tools)
# estejam no registry mesmo quando o Agent é usado sem o boot completo (run.py/cli)
from .agents import classify_intent, deterministic_plan, spawn_subagent
from .bus import bus

SYSTEM_PROMPT = """Você é o Joe: IDE completo, assistente pessoal autônomo e agente geral.

Diretrizes operacionais (playbook embutido):
- Responda no idioma do usuário (padrão pt-BR).
- Entenda a INTENÇÃO antes de agir: criar? abrir app? ler? enviar mensagem? planejar? ver pela câmera?
- Use ferramentas para agir de verdade; nunca finja execução.
- Ao editar código: leia antes (read_file), edite exato (edit_file), valide depois (bash).
- Ferramentas novas podem existir além das que você conhece; confira /tools.
- Memorize fatos duráveis sobre o usuário com `remember`; use `rag_search` antes de perguntar de novo.
- Para tarefas grandes, delegue: `spawn_subagent(name, goal)` executa um filho e traz o resultado.
- Precisa de capacidade que não existe? Crie: `create_extension(nome)` gera um módulo plug-in.
- Seja conciso na resposta final; os passos já aparecem no grafo para o usuário."""


class Agent:
    def __init__(self, config: Config | None = None, memory: Memory | None = None,
                 subagent=None):
        self.config = config or Config.load()
        self.memory = memory or Memory(config=self.config)
        self.subagent = subagent          # None = agente raiz ("joe")
        self.agent_id = subagent.id if subagent else "joe"
        self.agent_name = subagent.name if subagent else "Joe"
        self.messages: list[dict] = []
        # auto-análise bruta: assina o bus UMA vez por processo (sem duplicar auditoria)
        if not getattr(bus, "_joe_audit_hook", False):
            bus.subscribe("", self._audit_sink)
            bus._joe_audit_hook = True
        sh = registry.get("bash")
        if isinstance(sh, ShellTool):
            sh.config = self.config
        if provider_registry.active() is None:
            from .llm import bootstrap, auto_select
            bootstrap(self.config)
            auto_select(self.config)
        # config sempre manda no timeout (bootstrap só roda quando ainda não há ativo)
        from .llm import set_llm_timeout
        set_llm_timeout(getattr(self.config, "llm_timeout", 300))
        self._rebuild_system()

    def child(self, sa) -> "Agent":
        """Fábrica plugável de sub-agentes: herdar daqui é o único hook para
        trocar/compor o motor do filho sem tocar em agents.py nem no pipeline."""
        return Agent(config=self.config, memory=self.memory, subagent=sa)

    # ------------------------------------------------------------ contexto
    def _rebuild_system(self, extra_ctx: str = "") -> None:
        parts = [SYSTEM_PROMPT, f"cwd: {Path.cwd()}", f"agente: {self.agent_name} ({self.agent_id})"]
        mem = self.memory.context_block(limit=20)
        if mem:
            parts.append(mem)
        if extra_ctx:
            parts.append(extra_ctx)
        system = "\n\n".join(parts)
        if self.messages and self.messages[0]["role"] == "system":
            self.messages[0]["content"] = system
        else:
            self.messages.insert(0, {"role": "system", "content": system})

    def _audit_sink(self, event: dict) -> None:
        """Auto-análise bruta: tudo passa pelo bus vira memória episódica + arquivo."""
        try:
            self.memory.log_event(event["topic"], {k: v for k, v in event.items() if k != "ts"})
            with open(AUDIT_LOG, "a", encoding="utf-8") as f:
                f.write(json.dumps({k: v for k, v in event.items()}, default=str) + "\n")
        except Exception:
            pass

    # ------------------------------------------------------------ pipeline
    def _echo(self, s: str) -> None:
        getattr(self, "_echo_fn", lambda _: None)(s)

    def _step(self, role: str, note: str = "", **data) -> None:
        # nota legível também no stdout (eco do turno), não só no grafo/bus —
        # assim o usuário vê "quem pensou/decidiu" sem abrir /graph.
        if note:
            self._echo(f"· [{role}] {note}")
        bus.emit("agent.step", agent=self.agent_id, name=self.agent_name, role=role, note=note, **data)

    def chat(self, user_text: str, echo=None) -> str:
        echo = echo or (lambda s: None)
        self._echo_fn = echo          # disponível p/ _step ecoar decisões no turno
        from . import llm as _llm  # indireção p/ monkeypatch em testes

        # 1) INTENÇÃO
        intent, score = classify_intent(user_text)
        self._step("intencao", intent=intent, score=score, text=user_text[:120])
        self.memory.observe_habit(intent)

        # RAG: injeta contexto semanticamente relevante ANTES de pensar
        hits = self.memory.search(user_text, k=4)
        rag_ctx = ("\n".join(f"[{h['kind']}|{h['tag']}] {h['text'][:160]}" for h in hits)
                   if hits else "")
        if rag_ctx:
            self._step("planejador", note=f"RAG trouxe {len(hits)} trechos relevantes")
        self._rebuild_system(rag_ctx)
        self.messages.append({"role": "user", "content": user_text})

        # 2+3) PLANEJADOR + EXECUTOR
        self._tool_results_i = {}     # turno novo: limpa registro de ferramentas p/ revisor
        has_llm = provider_registry.active() is not None
        reply = ""
        if has_llm:
            reply = self._run_tool_loop(echo)
        else:
            reply = self._run_offline_plan(user_text, intent, echo)

        # 4) REVISOR (determinístico: plausibilidade + falhas estruturadas de ferramenta)
        review = self._review(reply, intent)
        self._step("revisor", verdict=review["verdict"], note=review["note"])
        if review["verdict"] == "refazer":
            if has_llm:
                self.messages.append({"role": "user",
                                      "content": f"[revisão] {review['note']}. Refaça corrigindo."})
                self._tool_results_i = {}
                reply = self._run_tool_loop(echo)
            elif any(not ok for ok in self._tool_results.values()):
                # offline: retrabalho honesto — re-plano com o erro visível na intenção
                err_note = "; ".join(f"{t} falhou" for t, ok in self._tool_results.items() if not ok)
                self._step("planejador", note=f"retrabalho após falha: {err_note}")
                retry = self._run_offline_plan(user_text, intent, echo)
                if not any(not ok for ok in self._tool_results.values()):
                    reply = retry
                else:
                    reply += f"\n\n⚠ revisão: ainda há falhas ({err_note}) — refine o pedido."

        # 5) CONCLUSÃO + proatividade
        self.messages.append({"role": "assistant", "content": reply})
        suggestions = self.memory.predict()
        if suggestions and not self.subagent:
            sug = ", ".join(s["intent"].replace("_", " ") for s in suggestions)
            reply += f"\n\n💡 Com base nos seus hábitos desta hora, talvez você queira: {sug}. Faço?"
        self._step("conclusao", done=True, text_len=len(reply))
        self._save_history(user_text, reply)
        self._maybe_speak(reply)          # fala ao concluir se auto_falar=true (e houver voz)
        return reply

    def _maybe_speak(self, reply: str) -> None:
        """Voz automática ao concluir (config auto_falar). Silencioso e nunca fatal:
        sem TTS no sistema ou erro de áudio, segue apenas texto."""
        try:
            if not getattr(self.config, "auto_falar", False) or self.subagent:
                return
            tool = registry.get("speak")
            if tool is None:
                return
            short = reply[:400]
            res = tool.run(text=short)
            txt = getattr(res, "output", "") or ""
            if "[sem motor de voz" in txt.lower():
                self.config.auto_falar = False   # descobre uma vez, não insiste
        except Exception:
            pass

    # ------------------------------------------------------------ loop com LLM
    def _run_tool_loop(self, echo=None) -> str:
        echo = echo or (lambda s: None)
        tools = registry.schemas()
        budget = self.config.max_tool_iterations // 2 if self.subagent else self.config.max_tool_iterations
        try:
            for _ in range(budget):
                msg = chat_completion(self.messages, tools, self.config)
                calls = msg.get("tool_calls")
                content = msg.get("content") or ""
                if content:
                    bus.emit("agent.thought", agent=self.agent_id, thought=content[:300])
                if not calls:
                    return content
                self.messages.append(msg)
                for tc in calls:
                    fn = tc["function"]["name"]
                    try:
                        args = json.loads(tc["function"].get("arguments") or "{}")
                    except json.JSONDecodeError:
                        args = {}
                    echo(f"⚙ {fn}({_short(args)})")
                    bus.emit("agent.tool", agent=self.agent_id, tool=fn, args=_short(args))
                    result = registry.execute(fn, args)
                    self._tool_results[fn] = result.ok
                    if fn == "remember":
                        bus.emit("agent.memory", agent=self.agent_id, saved=str(args)[:120])
                    bus.emit("agent.tool_result", agent=self.agent_id, tool=fn,
                             ok=result.ok, out=result.as_text()[:200])
                    echo(f"  ↳ {_short(result.as_text(), 200)}")
                    self.messages.append({"role": "tool", "tool_call_id": tc.get("id", fn),
                                          "name": fn, "content": result.as_text()})
            return "(pausei no limite de iterações — me dê instruções mais específicas)"
        except LLMError as e:
            return f"[LLM indisponível: {e}] — alternando para plano offline:\n" + \
                   self._run_offline_plan(self.messages[-1]["content"], "executar", echo)

    # ------------------------------------------------------------ plano offline (base forte sem API)
    def _run_offline_plan(self, user_text: str, intent: str, echo) -> str:
        self._step("planejador", mode="determinístico-offline", intent=intent)
        plan = deterministic_plan(user_text)
        if not plan:
            # auto-expansão: se a intenção não tem playbook mas existe ferramenta
            # cujo NOME aparece no texto do usuário (ex.: 'piada' → piada_hello,
            # criada por extensão drop-in), usa-a. Assim o sistema ganha
            # capacidades novas SEM editar o núcleo — no mesmo turno da criação.
            from .tools.base import registry as _reg
            norm = _norm(user_text)   # lista de tokens normalizados (sem acentos)
            guesses = []
            for cand in (intent.replace("-", "_"), *norm):
                if len(cand) < 3 or cand in ("conta", "uma", "meu", "com"):
                    continue
                guesses += [t for t in _reg.names()
                            if (t == cand or t.startswith(cand + "_")) and t not in guesses]
            if guesses:
                # casa os args com a assinatura real da ferramenta (stub usa 'who')
                import inspect as _insp
                try:
                    params = set(_insp.signature(_reg.get(guesses[0]).fn).parameters)
                except Exception:
                    params = {"text"}
                arg_name = "text" if "text" in params else next(iter(params), "text")
                plan = [{"name": guesses[0], "args": {arg_name: user_text}}]
                self._step("executor", note=f"auto-expansão → {guesses[0]}")
        if not plan:
            return (
                f"Entendi sua intenção como **{intent}** (score da análise semântica).\n"
                "Sem LLM disponível eu executo ações diretas; para esta tarefa eu precisaria de:\n"
                "- ollama local (`ollama serve` + `ollama pull qwen2.5-coder:1.5b`), ou\n"
                "- `/config openrouter <chave>`.\n"
                "Enquanto isso: `/help` mostra comandos, `/bench` testa modelos, `/graph` abre o grafo."
            )
        outs = []
        for step in plan:
            echo(f"▶ plano offline: {step['name']}({_short(step['args'])})")
            bus.emit("agent.plan_step", agent=self.agent_id, tool=step["name"],
                     args=_short(step["args"]))
            r = registry.execute(step["name"], step["args"])
            self._tool_results[step["name"]] = r.ok
            # executor reporta a decisão/resultado ao grafo (o que criou/executou)
            self._step("executor", note=f"{step['name']} → {r.as_text()[:60]}")
            outs.append(f"{step['name']}: {r.as_text()[:600]}")
        return "Executei seu pedido sem LLM (planner determinístico):\n\n" + "\n\n".join(outs)

    # ------------------------------------------------------------ revisor
    def _review(self, reply: str, intent: str) -> dict:
        if not reply.strip():
            return {"verdict": "refazer", "note": "resposta vazia"}
        fail_markers = ("ERRO:", "Traceback", "falhou")
        if intent in ("editar_codigo", "criar_arquivo") and any(m in reply for m in fail_markers):
            return {"verdict": "refazer", "note": "erros visíveis no resultado da tarefa de código"}
        # revisão de FERRAMENTA (não só do texto): se o turno executou ferramentas e
        # alguma falhou, o revisor detecta nos marcadores estruturados e exige retrabalho.
        failed_tools = [t for t, ok in self._tool_results.items() if not ok]
        if failed_tools:
            return {"verdict": "refazer",
                    "note": f"ferramenta(s) com falha não reportada: {', '.join(failed_tools[:3])}"}
        return {"verdict": "ok", "note": "resultado coerente com a intenção"}

    # registro por-turno de quais ferramentas rodaram e se passaram (limpo em chat())
    # por instância (não classe): dois Agent simultâneos (ex.: sub-agente filho +
    # REPL) não podem contaminar o registro de ferramentas um do outro
    @property
    def _tool_results(self) -> dict[str, bool]:
        if not hasattr(self, "_tool_results_i"):
            self._tool_results_i: dict[str, bool] = {}
        return self._tool_results_i

    @_tool_results.setter
    def _tool_results(self, value: dict[str, bool]) -> None:
        self._tool_results_i = value

    # ------------------------------------------------------------ histórico
    def _save_history(self, user_text: str, reply: str) -> None:
        try:
            HISTORY_PATH.mkdir(parents=True, exist_ok=True)
            with (HISTORY_PATH / "log.jsonl").open("a", encoding="utf-8") as f:
                f.write(json.dumps({"agent": self.agent_id, "user": user_text,
                                    "reply": reply[:2000]}, ensure_ascii=False) + "\n")
        except Exception:
            pass

    def reset(self) -> None:
        self.messages = []
        self._rebuild_system()


def _short(x, n=80) -> str:
    s = x if isinstance(x, str) else json.dumps(x, ensure_ascii=False, default=str)
    return s[:n] + ("…" if len(s) > n else "")
