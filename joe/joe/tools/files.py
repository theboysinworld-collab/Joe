"""Ferramentas de arquivos: ler, escrever, listar."""

from __future__ import annotations

from pathlib import Path

from .base import Tool, ToolResult, registry

IGNORE_DIRS = {".git", "node_modules", "__pycache__", ".venv", "venv", "dist", "build", ".next"}


def _resolve(path: str) -> Path:
    p = Path(path).expanduser()
    return p if p.is_absolute() else Path.cwd() / p


class ReadFileTool(Tool):
    name = "read_file"
    description = "Lê um arquivo de texto e retorna seu conteúdo (com números de linha)."
    parameters = {
        "type": "object",
        "properties": {
            "path": {"type": "string"},
            "start_line": {"type": "integer", "description": "linha inicial (1-based, opcional)"},
            "end_line": {"type": "integer", "description": "linha final (opcional)"},
        },
        "required": ["path"],
    }

    def run(self, path: str, start_line: int | None = None, end_line: int | None = None) -> ToolResult:
        p = _resolve(path)
        if not p.exists():
            return ToolResult(ok=False, output=f"arquivo não encontrado: {p}")
        if p.is_dir():
            return ToolResult(ok=False, output=f"{p} é um diretório; use list_dir")
        lines = p.read_text(encoding="utf-8", errors="replace").splitlines()
        s = max(1, start_line or 1)
        e = min(len(lines), end_line or len(lines))
        body = "\n".join(f"{i:>5} | {lines[i - 1]}" for i in range(s, e + 1))
        return ToolResult(output=body or "(arquivo vazio)", meta={"total_lines": len(lines)})


class WriteFileTool(Tool):
    name = "write_file"
    description = "Cria ou sobrescreve um arquivo de texto com o conteúdo dado (cria diretórios se preciso)."
    parameters = {
        "type": "object",
        "properties": {
            "path": {"type": "string"},
            "content": {"type": "string"},
        },
        "required": ["path", "content"],
    }

    def run(self, path: str, content: str) -> ToolResult:
        p = _resolve(path)
        p.parent.mkdir(parents=True, exist_ok=True)
        existed = p.exists()
        p.write_text(content, encoding="utf-8")
        n = len(content.splitlines())
        return ToolResult(output=f"{'atualizado' if existed else 'criado'}: {p} ({n} linhas)")


class EditFileTool(Tool):
    name = "edit_file"
    description = "Substitui exatamente uma ocorrência de `old_text` por `new_text` em um arquivo."
    parameters = {
        "type": "object",
        "properties": {
            "path": {"type": "string"},
            "old_text": {"type": "string"},
            "new_text": {"type": "string"},
        },
        "required": ["path", "old_text", "new_text"],
    }

    def run(self, path: str, old_text: str, new_text: str) -> ToolResult:
        p = _resolve(path)
        if not p.exists():
            return ToolResult(ok=False, output=f"arquivo não encontrado: {p}")
        text = p.read_text(encoding="utf-8")
        count = text.count(old_text)
        if count == 0:
            return ToolResult(ok=False, output="texto antigo não encontrado no arquivo")
        if count > 1:
            return ToolResult(ok=False, output=f"texto antigo aparece {count}x; seja mais específico")
        p.write_text(text.replace(old_text, new_text, 1), encoding="utf-8")
        return ToolResult(output=f"editado: {p}")


class ListDirTool(Tool):
    name = "list_dir"
    description = "Lista arquivos e subdiretórios (até 2 níveis) ignorando pastas como .git/node_modules."
    parameters = {
        "type": "object",
        "properties": {"path": {"type": "string", "description": "diretório (padrão: cwd)"}},
    }

    def run(self, path: str = ".") -> ToolResult:
        root = _resolve(path)
        if not root.is_dir():
            return ToolResult(ok=False, output=f"diretório não encontrado: {root}")
        out = []
        for item in sorted(root.rglob("*")):
            rel_parts = item.relative_to(root).parts
            if any(part in IGNORE_DIRS for part in rel_parts):
                continue
            if len(rel_parts) > 2:
                continue
            suffix = "/" if item.is_dir() else ""
            out.append(f"{item.relative_to(root)}{suffix}")
        return ToolResult(output="\n".join(out) or "(vazio)", meta={"count": len(out)})


for t in (ReadFileTool(), WriteFileTool(), EditFileTool(), ListDirTool()):
    registry.register(t)
