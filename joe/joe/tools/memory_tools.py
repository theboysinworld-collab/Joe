"""Ferramentas de memória longa do agente."""

from __future__ import annotations

from .base import Tool, ToolResult, registry
from ..memory import Memory


class RememberTool(Tool):
    name = "remember"
    description = "Salva um fato durável na memória persistente (preferências do usuário, decisões de projeto etc.)."
    parameters = {
        "type": "object",
        "properties": {
            "text": {"type": "string", "description": "fato a memorizar (curto, uma frase)"},
            "tag": {"type": "string", "description": "categoria: preferencia, projeto, pessoa, tarefa..."},
        },
        "required": ["text"],
    }

    def __init__(self, memory: Memory):
        self.memory = memory

    def run(self, text: str, tag: str = "geral") -> ToolResult:
        self.memory.remember(text, tag)
        return ToolResult(output=f"memorizado: [{tag}] {text}")


class RecallTool(Tool):
    name = "recall"
    description = "Busca fatos na memória persistente por palavra-chave. Vazio = lista tudo."
    parameters = {
        "type": "object",
        "properties": {"query": {"type": "string"}},
    }

    def __init__(self, memory: Memory):
        self.memory = memory

    def run(self, query: str = "") -> ToolResult:
        facts = self.memory.search(query) if query else self.memory.facts
        if not facts:
            return ToolResult(ok=False, output="nada na memória sobre isso")
        return ToolResult(output="\n".join(f"- [{f['tag']}] {f['text']}" for f in facts))


class ForgetTool(Tool):
    name = "forget"
    description = "Remove da memória persistente todos os fatos que contenham a palavra-chave."
    parameters = {
        "type": "object",
        "properties": {"query": {"type": "string"}},
        "required": ["query"],
    }

    def __init__(self, memory: Memory):
        self.memory = memory

    def run(self, query: str) -> ToolResult:
        n = self.memory.forget(query)
        return ToolResult(output=f"removido(s) {n} fato(s) contendo {query!r}")


_mem = Memory()
registry.register(RememberTool(_mem))
registry.register(RecallTool(_mem))
registry.register(ForgetTool(_mem))
