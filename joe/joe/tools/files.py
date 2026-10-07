"""Ferramentas de arquivos: ler, escrever, editar exato, listar, buscar, indexar p/ RAG."""

from __future__ import annotations

import re
from pathlib import Path

from .base import Tool, ToolResult, registry

IGNORE_DIRS = {".git", "node_modules", "__pycache__", ".venv", "venv", "dist", "build", ".next"}


class ReadFileTool(Tool):
    name = "read_file"
    description = "Lê um arquivo de texto (com números de linha). Args: path, start?, count?"
    parameters = {"type": "object", "properties": {
        "path": {"type": "string"}, "start": {"type": "integer"}, "count": {"type": "integer"}},
        "required": ["path"]}

    def run(self, path: str, start: int = 1, count: int = 400) -> ToolResult:
        p = Path(path)
        if not p.is_file():
            return ToolResult(ok=False, output=f"não existe: {path}")
        lines = p.read_text(encoding="utf-8", errors="replace").splitlines()
        chunk = lines[start - 1:start - 1 + count]
        body = "\n".join(f"{i+start:5d}| {l}" for i, l in enumerate(chunk))
        return ToolResult(output=body or "(vazio)", meta={"total_lines": len(lines)})


class WriteFileTool(Tool):
    name = "write_file"
    description = "Cria/sobrescreve um arquivo com o conteúdo dado. Args: path, content."
    parameters = {"type": "object", "properties": {
        "path": {"type": "string"}, "content": {"type": "string"}}, "required": ["path", "content"]}

    def run(self, path: str, content: str) -> ToolResult:
        p = Path(path)
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(content, encoding="utf-8")
        return ToolResult(output=f"escrito {p} ({len(content)} chars)")


class EditFileTool(Tool):
    name = "edit_file"
    description = ("Substituição EXATA old→new em um arquivo (falha se ambíguo/não achado). "
                   "Args: path, old, new, replace_all?")
    parameters = {"type": "object", "properties": {
        "path": {"type": "string"}, "old": {"type": "string"}, "new": {"type": "string"},
        "replace_all": {"type": "boolean"}}, "required": ["path", "old", "new"]}

    def run(self, path: str, old: str, new: str, replace_all: bool = False) -> ToolResult:
        p = Path(path)
        if not p.is_file():
            return ToolResult(ok=False, output=f"não existe: {path}")
        text = p.read_text(encoding="utf-8")
        n = text.count(old)
        if n == 0:
            return ToolResult(ok=False, output="texto old não encontrado (leia o arquivo antes)")
        if n > 1 and not replace_all:
            return ToolResult(ok=False, output=f"old aparece {n}x — refine ou use replace_all")
        p.write_text(text.replace(old, new), encoding="utf-8")
        return ToolResult(output=f"editado {path} ({n} substituições)")


class ListDirTool(Tool):
    name = "list_dir"
    description = "Lista um diretório (2 níveis). Args: path?"
    parameters = {"type": "object", "properties": {"path": {"type": "string"}}}

    def run(self, path: str = ".") -> ToolResult:
        root = Path(path)
        if not root.is_dir():
            return ToolResult(ok=False, output=f"não é diretório: {path}")
        out = []
        for item in sorted(root.iterdir()):
            if item.name.startswith(".") and item.name != ".":
                continue
            out.append(item.name + ("/" if item.is_dir() else ""))
            if item.is_dir() and item.name not in IGNORE_DIRS:
                for sub in sorted(item.iterdir())[:12]:
                    if not sub.name.startswith("."):
                        out.append(f"  {sub.name}{'/' if sub.is_dir() else ''}")
        return ToolResult(output="\n".join(out) or "(vazio)")


class GrepTool(Tool):
    name = "grep"
    description = "Busca regex em arquivos do projeto. Args: pattern, path?, glob?"
    parameters = {"type": "object", "properties": {
        "pattern": {"type": "string"}, "path": {"type": "string"}, "glob": {"type": "string"}},
        "required": ["pattern"]}

    def run(self, pattern: str, path: str = ".", glob: str = "**/*") -> ToolResult:
        rx = re.compile(pattern)
        hits, root = [], Path(path)
        for f in root.glob(glob):
            if not f.is_file() or any(d in f.parts for d in IGNORE_DIRS):
                continue
            try:
                for i, line in enumerate(f.read_text(encoding="utf-8", errors="replace").splitlines(), 1):
                    if rx.search(line):
                        hits.append(f"{f}:{i}: {line.strip()[:160]}")
                        if len(hits) >= 80:
                            raise StopIteration
            except (UnicodeDecodeError, PermissionError, IsADirectoryError):
                continue
            except StopIteration:
                break
        return ToolResult(output="\n".join(hits) or "(nada encontrado)")


class IndexTool(Tool):
    name = "index_for_rag"
    description = "Indexa um arquivo na memória vetorial (RAG). Args: path."
    parameters = {"type": "object", "properties": {"path": {"type": "string"}}, "required": ["path"]}

    def run(self, path: str) -> ToolResult:
        from ..memory import Memory
        m = Memory()
        n = m.index_file(path)
        return ToolResult(output=f"{n} chunks de {path} indexados para RAG" if n
                          else f"nada indexado ({path})")


for t in (ReadFileTool(), WriteFileTool(), EditFileTool(), ListDirTool(), GrepTool(), IndexTool()):
    registry.register(t)
