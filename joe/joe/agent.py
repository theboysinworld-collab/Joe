"""Núcleo do agente Joe: loop de conversa + execução de ferramentas."""

from __future__ import annotations

import json
from pathlib import Path

from .config import Config, HISTORY_PATH
from .llm import LLMError, chat_completion, offline_reply
from .memory import Memory
from .tools.base import registry
from .tools.shell import ShellTool

SYSTEM_PROMPT = """Você é o Joe, um agente autônomo que é simultaneamente IDE, assistente pessoal e agente geral.

Diretrizes:
- Responda sempre no idioma do usuário (padrão: português do Brasil).
- Use ferramentas para agir, não apenas descrever: leia arquivos antes de editá-los, rode comandos para verificar resultados.
- Ao editar código, prefira edit_file (substituição exata) a reescrever arquivos inteiros.
- Depois de criar/mudar código, valide (rode testes/interpretação) com bash quando possível.
- Memorize via ferramenta `remember` fatos duráveis sobre o usuário/projeto (preferências, decisões).
- Seja conciso; mostre o resultado final, não o barulho das ferramentas.
- Nunca finja ter executado algo: execute de fato com ferramentas.
"""


class Agent:
    def __init__(self, config: Config | None = None, memory: Memory | None = None):
        self.config = config or Config.load()
        self.memory = memory or Memory()
        self.messages: list[dict] = []
        # reflete o config da sessão na ferramenta de shell (confirmação/limite)
        sh = registry.get("bash")
        if isinstance(sh, ShellTool):
            sh.config = self.config
        self._rebuild_system()

    # ---------- contexto ----------
    def _rebuild_system(self) -> None:
        ctx = [SYSTEM_PROMPT, f"Diretório de trabalho atual: {Path.cwd()}"]
        mem = self.memory.context_block()
        if mem:
            ctx.append(mem)
        system = "\n\n".join(ctx)
        if self.messages and self.messages[0]["role"] == "system":
            self.messages[0]["content"] = system
        else:
            self.messages.insert(0, {"role": "system", "content": system})

    def refresh_context(self) -> None:
        self._rebuild_system()

    # ---------- loop principal ----------
    def chat(self, user_text: str, echo=None) -> str:
        """Processa um turno do usuário; retorna a resposta final em texto."""
        echo = echo or (lambda s: None)
        self.messages.append({"role": "user", "content": user_text})
        tools = registry.schemas()

        if not self.config.api_key:
            reply = offline_reply(user_text, [t["function"]["name"] for t in tools])
            self.messages.append({"role": "assistant", "content": reply})
            self._save_history(user_text, reply)
            return reply

        try:
            for _ in range(self.config.max_tool_iterations):
                # indireção via módulo permite monkeypatch em testes
                from . import llm as _llm
                msg = _llm.chat_completion(self.messages, tools, self.config)
                tool_calls = msg.get("tool_calls")
                if not tool_calls:
                    content = msg.get("content") or ""
                    self.messages.append({"role": "assistant", "content": content})
                    self._save_history(user_text, content)
                    return content
                # registra a chamada do assistente e executa cada ferramenta
                self.messages.append(msg)
                for tc in tool_calls:
                    fn = tc["function"]["name"]
                    try:
                        args = json.loads(tc["function"].get("arguments") or "{}")
                    except json.JSONDecodeError:
                        args = {}
                    echo(f"⚙ {fn}({', '.join(f'{k}={str(v)[:60]!r}' for k, v in args.items())})")
                    result = registry.execute(fn, args)
                    echo(f"  ↳ {result.as_text()[:200]}{'…' if len(result.as_text()) > 200 else ''}")
                    self.messages.append({
                        "role": "tool",
                        "tool_call_id": tc.get("id", fn),
                        "name": fn,
                        "content": result.as_text(),
                    })
            reply = "(pausei: atingi o limite de iterações de ferramentas — me dê instruções mais específicas)"
            self.messages.append({"role": "assistant", "content": reply})
            return reply
        except LLMError as e:
            reply = f"[erro de LLM] {e}\nUse comandos slash (/help) ou configure a API key."
            self.messages.append({"role": "assistant", "content": reply})
            return reply

    # ---------- histórico ----------
    def _save_history(self, user_text: str, reply: str) -> None:
        try:
            HISTORY_PATH.mkdir(parents=True, exist_ok=True)
            day = Path(HISTORY_PATH) / f"session-{json.dumps({'cwd': str(Path.cwd())})[:0] or 'log'}.jsonl"
            with day.open("a", encoding="utf-8") as f:
                f.write(json.dumps({"user": user_text, "reply": reply}, ensure_ascii=False) + "\n")
        except Exception:
            pass

    def reset(self) -> None:
        self.messages = []
        self._rebuild_system()
