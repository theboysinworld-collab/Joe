"""Busca em conteúdo (grep-like) no projeto."""

from __future__ import annotations

import fnmatch
import re
from pathlib import Path

from .base import Tool, ToolResult, registry

IGNORE_DIRS = {".git", "node_modules", "__pycache__", ".venv", "venv", "dist", "build", ".next"}
BINARY_EXTS = {".png", ".jpg", ".jpeg", ".gif", ".pdf", ".zip", ".gz", ".tar", ".ico", ".woff", ".woff2", ".mp3", ".mp4"}


class GrepTool(Tool):
    name = "grep"
    description = "Busca um padrão (regex ou texto fixo) nos arquivos do projeto; retorna arquivo:linha:conteúdo."
    parameters = {
        "type": "object",
        "properties": {
            "pattern": {"type": "string", "description": "expressão regular ou texto"},
            "path": {"type": "string", "description": "diretório raiz da busca (padrão: .)"},
            "glob": {"type": "string", "description": "filtro de nomes, ex.: *.py"},
            "ignore_case": {"type": "boolean"},
            "max_matches": {"type": "integer", "description": "máximo de linhas retornadas (padrão 80)"},
        },
        "required": ["pattern"],
    }

    def run(self, pattern: str, path: str = ".", glob: str | None = None,
            ignore_case: bool = False, max_matches: int = 80) -> ToolResult:
        flags = re.IGNORECASE if ignore_case else 0
        try:
            rx = re.compile(pattern, flags)
        except re.error as e:
            return ToolResult(ok=False, output=f"regex inválida: {e}")
        root = Path(path).expanduser().resolve()
        if not root.exists():
            return ToolResult(ok=False, output=f"caminho não encontrado: {root}")
        matches = []
        for f in sorted(root.rglob("*")):
            parts = f.relative_to(root).parts
            if any(p in IGNORE_DIRS for p in parts[:-1]) or f.suffix.lower() in BINARY_EXTS:
                continue
            if glob and not fnmatch.fnmatch(f.name, glob):
                continue
            if not f.is_file():
                continue
            try:
                text = f.read_text(encoding="utf-8")
            except (UnicodeDecodeError, OSError):
                continue
            for i, line in enumerate(text.splitlines(), 1):
                if rx.search(line):
                    matches.append(f"{f.relative_to(root)}:{i}: {line.strip()[:200]}")
                    if len(matches) >= max_matches:
                        return ToolResult(output="\n".join(matches), meta={"truncated": True})
        return ToolResult(
            ok=bool(matches),
            output="\n".join(matches) if matches else f"nenhum match para {pattern!r}",
            meta={"count": len(matches)},
        )


registry.register(GrepTool())
