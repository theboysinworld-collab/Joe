"""Sistema de extensões do Joe — plug-ins drop-in que NÃO tocam o núcleo.

TODOs (fluxo padrão de integração):
1. Cada extensão vive em ~/.joe/extensions/<nome>/ com um arquivo `extension.py`.
2. `extension.py` define `meta()` (nome, versão, descrição) e `setup(ctx)` que devolve
   uma lista de objetos: Tool | Provider | Capability | AgentRole.
3. `load_all()` importa as pastas via importlib, valida os tipos e REGISTRA tudo nos
   registries centrais. Ferramentas novas aparecem no agente sem editar código base.
4. `create_extension_stub()` permite ao PRÓPRIO Joe gerar um novo módulo sob demanda
   (auto-evolução controlada): cria a pasta, o esqueleto e recarrega o registry.
5. A UI mostra cada extensão carregada no painel `/ext`.
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

from .config import EXTENSIONS_DIR
from .tools.base import Tool, registry

_loaded: dict[str, dict] = {}


def discover() -> list[Path]:
    """Encontra todas as extensões instaladas (pastas com extension.py)."""
    if not EXTENSIONS_DIR.exists():
        return []
    return sorted(p.parent for p in EXTENSIONS_DIR.glob("*/extension.py"))


def load_one(ext_dir: Path) -> list[object]:
    """Importa uma extensão e registra seus componentes. Retorna objetos criados."""
    name = ext_dir.name
    mod_path = ext_dir / "extension.py"
    spec = importlib.util.spec_from_file_location(f"joe_ext_{name}", mod_path)
    if spec is None or spec.loader is None:
        raise ImportError(f"não consegui carregar {mod_path}")
    mod = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = mod
    spec.loader.exec_module(mod)

    objects: list[object] = []
    ctx = {"registry": registry, "emit_tool_added": _announce}
    if hasattr(mod, "setup"):
        objects = list(mod.setup(ctx) or [])
    meta = getattr(mod, "meta", lambda: {})()
    meta.setdefault("name", name)
    meta["path"] = str(ext_dir)
    meta["components"] = [type(o).__name__ for o in objects]

    # registro automático conforme o tipo do objeto devolvido
    from .llm import provider_registry
    from .capabilities import capability_registry
    from .agents import role_registry
    for obj in objects:
        if isinstance(obj, Tool):
            registry.register(obj)
        elif hasattr(obj, "id") and hasattr(obj, "chat"):          # Provider
            provider_registry.register(obj)
        elif hasattr(obj, "name") and hasattr(obj, "perceive"):    # Capability
            capability_registry.register(obj)
        elif hasattr(obj, "role_name"):                            # AgentRole
            role_registry.register(obj)
    _loaded[name] = meta
    return objects


def _announce(tool: Tool) -> None:
    from .bus import bus
    bus.emit("ext.tool_added", tool=tool.name)


def load_all() -> dict[str, dict]:
    """Carrega todas as extensões; falha de uma nunca derruba as outras."""
    for ext_dir in discover():
        try:
            load_one(ext_dir)
        except Exception as e:  # auto-cura nível 1: reporta e segue
            _loaded[ext_dir.name] = {"name": ext_dir.name, "error": f"{type(e).__name__}: {e}",
                                     "path": str(ext_dir)}
    return _loaded


def loaded() -> dict[str, dict]:
    return dict(_loaded)


STUB_TEMPLATE = '''"""Extensão "{name}" — criada pelo próprio Joe em {date}.

Para adicionar comportamento sem tocar no núcleo: edite APENAS este arquivo.
"""


def meta():
    return {{"name": "{name}", "version": "0.1.0", "description": "{desc}"}}


def setup(ctx):
    """Devolva aqui suas Tools / Providers / Capabilities / AgentRoles."""
    from joe.tools.base import FunctionTool, ToolResult

    def _say_hello(who: str = "mundo") -> ToolResult:
        return ToolResult(output=f"olá, {{who}}! (extensão {name})")

    hello = FunctionTool(
        name="{slug}_hello",
        description="Cumprimento de exemplo da extensão {name}. Args: who (string).",
        fn=_say_hello,
        parameters={{"type": "object", "properties": {{"who": {{"type": "string"}}}}}},
    )
    return [hello]
'''


def create_extension_stub(name: str, description: str = "") -> Path:
    """Gera o esqueleto de uma nova extensão e a carrega imediatamente."""
    import re
    from datetime import datetime
    slug = re.sub(r"[^a-z0-9_]", "_", name.lower())
    ext_dir = EXTENSIONS_DIR / slug
    ext_dir.mkdir(parents=True, exist_ok=True)
    target = ext_dir / "extension.py"
    if not target.exists():
        target.write_text(
            STUB_TEMPLATE.format(name=name, slug=slug, desc=description or "(sem descrição)",
                                 date=datetime.now().strftime("%Y-%m-%d %H:%M")),
            encoding="utf-8",
        )
    load_one(ext_dir)
    return ext_dir
