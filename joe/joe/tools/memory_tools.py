"""Ferramentas de memória: lembrar, esquecer, RAG, listar fatos."""

from __future__ import annotations

from .base import Tool, ToolResult, registry


def _mem():
    from ..memory import Memory  # late import (evita ciclo)
    return Memory()


class RememberTool(Tool):
    name = "remember"
    description = "Guarda um fato durável na memória persistente (SQLite+vetor). Args: text, tag?"
    parameters = {"type": "object", "properties": {
        "text": {"type": "string"}, "tag": {"type": "string"}}, "required": ["text"]}

    def run(self, text: str, tag: str = "geral") -> ToolResult:
        mid = _mem().remember(text, tag=tag, source="agent")
        return ToolResult(output=f"memorizado #{mid}: [{tag}] {text}")


class ForgetTool(Tool):
    name = "forget"
    description = "Remove fatos que contenham o trecho dado. Args: needle."
    parameters = {"type": "object", "properties": {"needle": {"type": "string"}}, "required": ["needle"]}

    def run(self, needle: str) -> ToolResult:
        n = _mem().forget(needle)
        return ToolResult(output=f"{n} fato(s) removidos")


class RecallTool(Tool):
    name = "recall"
    description = "Busca lexical simples na memória. Args: query."
    parameters = {"type": "object", "properties": {"query": {"type": "string"}}, "required": ["query"]}

    def run(self, query: str) -> ToolResult:
        hits = _mem().search(query, k=8)
        return ToolResult(output="\n".join(f"[{h['kind']}|{h['score']}] {h['text'][:200]}" for h in hits)
                          or "(nada na memória)")


class RagSearchTool(Tool):
    name = "rag_search"
    description = ("Busca SEMÂNTICO-VETORIAL (RAG) em fatos + documentos indexados. "
                   "Use antes de perguntar ao usuário. Args: query, k?")
    parameters = {"type": "object", "properties": {
        "query": {"type": "string"}, "k": {"type": "integer"}}, "required": ["query"]}

    def run(self, query: str, k: int = 6) -> ToolResult:
        hits = _mem().search(query, k=k)
        if not hits:
            return ToolResult(output="(nenhum trecho relevante — índice vazio ou tema novo)")
        out = []
        for h in hits:
            out.append(f"• ({h['kind']}, score {h['score']}) [{h['tag']}]\n{h['text']}")
        return ToolResult(output="\n\n".join(out))


for t in (RememberTool(), ForgetTool(), RecallTool(), RagSearchTool()):
    registry.register(t)
