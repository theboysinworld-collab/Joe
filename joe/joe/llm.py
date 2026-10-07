"""Cliente LLM: OpenAI-compatível via urllib + fallback offline determinístico.

Assim o Joe funciona como agente de verdade quando há API key, e continua
útil (modo assistente local) mesmo sem rede/chave.
"""

from __future__ import annotations

import json
import urllib.error
import urllib.request
from datetime import datetime

from .config import Config


class LLMError(Exception):
    pass


def chat_completion(messages: list[dict], tools: list[dict] | None, config: Config) -> dict:
    """Retorna um dict estilo OpenAI: {'role':'assistant', 'content':..., 'tool_calls':[...]}."""
    if not config.api_key:
        raise LLMError("sem API key — defina OPENAI_API_KEY ou use o modo offline")
    payload = {
        "model": config.model,
        "messages": messages,
        "temperature": config.temperature,
        "max_tokens": config.max_tokens,
    }
    if tools:
        payload["tools"] = tools
        payload["tool_choice"] = "auto"
    req = urllib.request.Request(
        f"{config.base_url.rstrip('/')}/chat/completions",
        data=json.dumps(payload).encode(),
        headers={
            "Content-Type": "application/json",
            "Authorization": f"Bearer {config.api_key}",
        },
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=120) as resp:
            data = json.loads(resp.read().decode())
    except urllib.error.HTTPError as e:
        body = e.read().decode(errors="replace")[:500]
        raise LLMError(f"HTTP {e.code} da API: {body}") from e
    except urllib.error.URLError as e:
        raise LLMError(f"falha de rede: {e.reason}") from e
    return data["choices"][0]["message"]


def offline_reply(user_text: str, tools_available: list[str]) -> str:
    """Fallback sem LLM: interpreta intenções comuns e orienta o usuário.

    Em produção com chave de API, o modelo real assume o raciocínio.
    """
    t = user_text.lower().strip()
    now = datetime.now().strftime("%Y-%m-%d %H:%M")
    if any(w in t for w in ("hora", "horas", "que horas")):
        return f"Agora são {now} (relógio local)."
    if any(w in t for w in ("oi", "olá", "ola", "hey", "hello", "bom dia", "boa tarde", "boa noite")):
        return (
            "Olá! Sou o **Joe** — IDE, assistente pessoal e agente geral.\n"
            "Estou em modo offline (sem OPENAI_API_KEY), então não faço raciocínio de LLM,\n"
            "mas você pode usar comandos diretos: `/bash <cmd>`, `/read <arquivo>`, `/grep <padrão>`,\n"
            "/remember, /recall, /ide, /help. Configure uma chave para o modo agente completo."
        )
    if "ferramenta" in t or "tool" in t:
        return "Ferramentas disponíveis nesta sessão: " + ", ".join(sorted(tools_available))
    return (
        "Modo offline: entendi sua mensagem mas preciso de um LLM para agir como agente.\n"
        "Opções: (1) export OPENAI_API_KEY=... (ou JOE_BASE_URL para endpoint compatível);\n"
        "(2) usar comandos slash diretos (/help lista tudo); (3) `joe ide` para editar arquivos."
    )
