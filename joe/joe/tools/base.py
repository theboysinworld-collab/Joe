"""Registry de ferramentas do Joe — o CONTRATO que todo módulo novo deve seguir.

COMO ADICIONAR UMA FUNÇÃO SEM TOCAR NO NÚCLEO (playbook passo a passo):
1. Crie um arquivo em joe/tools/<area>.py (ou uma extensão em ~/.joe/extensions/).
2. Nele, defina `class MinhaTool(Tool)` com name/description/parameters (JSON Schema)
   e método run(**kwargs) -> ToolResult.
3. No fim do arquivo: `registry.register(MinhaTool())` — basta.
4. O agente passa a enxergar a ferramenta automaticamente (schemas() → LLM;
   execute() → executor). Nenhuma outra mudança é necessária.
5. Ferramentas que dependem de outras camadas devem fazer late import dentro de
   run() (evita ciclos) e NUNCA lançar exceção para fora: devolva ToolResult(ok=False).

Segurança transversal: toda execução emite evento no bus e é auditada pela memória.
"""

from __future__ import annotations

import abc
import json
import threading
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
    parameters: dict = {"type": "object", "properties": {}}

    @abc.abstractmethod
    def run(self, **kwargs: Any) -> ToolResult: ...

    def schema(self) -> dict:
        return {"type": "function", "function": {
            "name": self.name, "description": self.description, "parameters": self.parameters}}


class FunctionTool(Tool):
    """Tool simples envolvendo uma função Python (usada por extensões)."""

    def __init__(self, name, description, fn: Callable[..., ToolResult | str],
                 parameters: dict | None = None):
        self.name, self.description, self.fn = name, description, fn
        if parameters:
            self.parameters = parameters

    def run(self, **kwargs):
        out = self.fn(**kwargs)
        return out if isinstance(out, ToolResult) else ToolResult(output=str(out))


class ToolRegistry:
    def __init__(self) -> None:
        self._tools: dict[str, Tool] = {}
        self._lock = threading.RLock()

    def register(self, tool: Tool) -> None:
        with self._lock:
            self._tools[tool.name] = tool

    def add(self, name, description, fn, parameters=None) -> None:
        self.register(FunctionTool(name, description, fn, parameters))

    def get(self, name: str) -> Tool | None:
        with self._lock:
            return self._tools.get(name)

    def all(self) -> list[Tool]:
        with self._lock:
            return list(self._tools.values())

    def names(self) -> list[str]:
        with self._lock:
            return sorted(self._tools)

    def schemas(self) -> list[dict]:
        return [t.schema() for t in self.all()]

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

# módulos de tools embutidas — cada um se auto-registra ao ser importado
_BUILTIN_MODULES = ("shell", "files", "memory_tools", "web", "vision", "agent_tools")


def bootstrap_tools(config=None) -> ToolRegistry:
    """Importa todos os módulos de tools; idempotente (pode chamar de novo após criar extensão)."""
    import importlib
    from .. import bus as _bus
    for mod in _BUILTIN_MODULES:
        try:
            importlib.import_module(f".{mod}", __package__)
        except Exception as e:  # uma tool quebrada não derruba o resto
            _bus.bus.emit("tool.load_error", module=mod, error=str(e)[:200])
    return registry

