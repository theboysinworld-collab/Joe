"""Infraestrutura de ferramentas do agente."""

from __future__ import annotations

import abc
from dataclasses import dataclass, field
from typing import Any, Callable


@dataclass
class ToolResult:
    ok: bool = True
    output: str = ""
    meta: dict = field(default_factory=dict)

    def as_text(self) -> str:
        prefix = "" if self.ok else "ERRO: "
        return f"{prefix}{self.output}".strip() or ("(sem saída)" if self.ok else "(erro sem mensagem)")


class Tool(abc.ABC):
    name: str = ""
    description: str = ""
    # JSON Schema das argumentos (estilo OpenAI function calling)
    parameters: dict = {"type": "object", "properties": {}}

    @abc.abstractmethod
    def run(self, **kwargs: Any) -> ToolResult:
        ...

    def schema(self) -> dict:
        return {
            "type": "function",
            "function": {
                "name": self.name,
                "description": self.description,
                "parameters": self.parameters,
            },
        }


class FunctionTool(Tool):
    """Tool simples envolvendo uma função Python."""

    def __init__(self, name: str, description: str, fn: Callable[..., ToolResult | str],
                 parameters: dict | None = None):
        self.name = name
        self.description = description
        self.fn = fn
        if parameters:
            self.parameters = parameters

    def run(self, **kwargs: Any) -> ToolResult:
        out = self.fn(**kwargs)
        if isinstance(out, ToolResult):
            return out
        return ToolResult(output=str(out))


class ToolRegistry:
    def __init__(self) -> None:
        self._tools: dict[str, Tool] = {}

    def register(self, tool: Tool) -> None:
        self._tools[tool.name] = tool

    def add(self, name: str, description: str, fn, parameters: dict | None = None) -> None:
        self.register(FunctionTool(name, description, fn, parameters))

    def get(self, name: str) -> Tool | None:
        return self._tools.get(name)

    def all(self) -> list[Tool]:
        return list(self._tools.values())

    def schemas(self) -> list[dict]:
        return [t.schema() for t in self._tools.values()]

    def execute(self, name: str, args: dict | None = None) -> ToolResult:
        tool = self.get(name)
        if tool is None:
            return ToolResult(ok=False, output=f"ferramenta desconhecida: {name!r}")
        try:
            return tool.run(**(args or {}))
        except TypeError as e:
            return ToolResult(ok=False, output=f"argumentos inválidos para {name}: {e}")
        except Exception as e:  # nunca derruba o agente
            return ToolResult(ok=False, output=f"{type(e).__name__}: {e}")


registry = ToolRegistry()
