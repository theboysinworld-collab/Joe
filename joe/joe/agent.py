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
from .memory import Memory
from .tools.base import registry
from .tools.shell import ShellTool
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
        self._rebuild_system()

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
    def _step(self, role: str, **data) -> None:
        bus.emit("agent.step", agent=self.agent_id, name=self.agent_name, role=role, **data)

    def chat(self, user_text: str, echo=None) -> str:
        echo = echo or (lambda s: None)
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
        has_llm = provider_registry.active() is not None
        reply = ""
        if has_llm:
            reply = self._run_tool_loop(echo)
        else:
            reply = self._run_offline_plan(user_text, intent, echo)

        # 4) REVISOR (leve, determinístico: checa plausibilidade básica)
        review = self._review(reply, intent)
        self._step("revisor", verdict=review["verdict"], note=review["note"])
        if review["verdict"] == "refazer" and has_llm:
            self.messages.append({"role": "user",
                                  "content": f"[revisão] {review['note']}. Refaça corrigindo."})
            reply = self._run_tool_loop(echo)

        # 5) CONCLUSÃO + proatividade
        self.messages.append({"role": "assistant", "content": reply})
        suggestions = self.memory.predict()
        if suggestions and not self.subagent:
            sug = ", ".join(s["intent"].replace("_", " ") for s in suggestions)
            reply += f"\n\n💡 Com base nos seus hábitos desta hora, talvez você queira: {sug}. Faço?"
        self._step("conclusao", done=True, text_len=len(reply))
        self._save_history(user_text, reply)
        return reply

    # ------------------------------------------------------------ loop com LLM
    def _run_tool_loop(self, echo) -> str:
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
            outs.append(f"{step['name']}: {r.as_text()[:600]}")
        return "Executei seu pedido sem LLM (planner determinístico):\n\n" + "\n\n".join(outs)

    # ------------------------------------------------------------ revisor
    @staticmethod
    def _review(reply: str, intent: str) -> dict:
        if not reply.strip():
            return {"verdict": "refazer", "note": "resposta vazia"}
        fail_markers = ("ERRO:", "Traceback", "falhou")
        if intent in ("editar_codigo", "criar_arquivo") and any(m in reply for m in fail_markers):
            return {"verdict": "refazer", "note": "erros visíveis no resultado da tarefa de código"}
        return {"verdict": "ok", "note": "resultado coerente com a intenção"}

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
