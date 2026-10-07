"""Mini-IDE no terminal: buffers de arquivos, edição e busca em projeto.

Sem dependências externas — funciona em qualquer tty. Comandos:
  :open <arquivo>   abre buffer        :save [arquivo]  grava buffer atual
  :new <arquivo>    novo buffer vazio  :close           fecha buffer
  :lines            mostra c/ números  :edit N <txt>    substitui linha N
  :insert N <txt>   insere após linha N :delete N       apaga linha N
  :find <padrão>    busca no buffer    :grep <padrão>   busca no projeto
  :bufs             lista buffers      :cd <dir>        muda diretório
  :run <cmd>        executa shell      :q               sai do IDE
"""

from __future__ import annotations

import os
import subprocess
from pathlib import Path


class Buffer:
    def __init__(self, path: Path, lines: list[str], dirty: bool = False):
        self.path = path
        self.lines = lines
        self.dirty = dirty

    def text(self) -> str:
        return "\n".join(self.lines) + ("\n" if self.lines else "")


class MiniIDE:
    def __init__(self):
        self.buffers: dict[str, Buffer] = {}
        self.current: str | None = None
        self.root = Path.cwd()

    # ---------- helpers ----------
    def _key(self, path: Path) -> str:
        try:
            return str(path.resolve().relative_to(self.root))
        except ValueError:
            return str(path.resolve())

    def open_file(self, name: str) -> str:
        p = (self.root / name).expanduser()
        key = self._key(p)
        if key in self.buffers:
            self.current = key
            return f"buffer já aberto: {key}"
        if p.exists() and p.is_file():
            buf = Buffer(p, p.read_text(encoding="utf-8", errors="replace").splitlines())
        else:
            buf = Buffer(p, [])
            self.buffers[key] = buf
            self.current = key
            return f"[novo arquivo ainda não salvo] {key}"
        self.buffers[key] = buf
        self.current = key
        return f"aberto: {key} ({len(buf.lines)} linhas)"

    def cur(self) -> Buffer | None:
        return self.buffers.get(self.current) if self.current else None

    def save(self, name: str | None = None) -> str:
        buf = self.cur()
        if buf is None:
            return "nenhum buffer aberto (:open ou :new primeiro)"
        target = (self.root / name).expanduser() if name else buf.path
        target.parent.mkdir(parents=True, exist_ok=True)
        tmp = target.with_suffix(target.suffix + f".tmp.{os.getpid()}")
        tmp.write_text(buf.text(), encoding="utf-8")
        os.replace(tmp, target)  # escrita atômica: nunca corrompe o arquivo no meio
        buf.path = target
        buf.dirty = False
        self.buffers[self._key(target)] = buf
        self.current = self._key(target)
        return f"salvo: {target} ({len(buf.lines)} linhas)"

    # ---------- comando único entrypoint ----------
    def command(self, raw: str) -> str:
        raw = raw.strip()
        if not raw.startswith(":"):
            raw = ":" + raw
        parts = raw[1:].split(maxsplit=2)
        cmd, args = (parts[0].lower(), parts[1:]) if parts else ("", [])

        if cmd == "open":
            return self.open_file(args[0]) if args else "uso: :open <arquivo>"
        if cmd == "new":
            if not args:
                return "uso: :new <arquivo>"
            key = self._key((self.root / args[0]).expanduser())
            self.buffers[key] = Buffer((self.root / args[0]).expanduser(), [])
            self.current = key
            return f"novo buffer: {key}"
        if cmd == "save":
            return self.save(args[0] if args else None)
        if cmd == "close":
            if self.current in self.buffers:
                del self.buffers[self.current]
                self.current = next(iter(self.buffers), None)
                return "fechado"
            return "nenhum buffer"
        if cmd == "bufs":
            if not self.buffers:
                return "(nenhum buffer)"
            return "\n".join(
                f"{'*' if k == self.current else ' '} {k} [{len(b.lines)} linhas{', modificado' if b.dirty else ''}]"
                for k, b in self.buffers.items()
            )
        if cmd == "lines":
            buf = self.cur()
            if not buf:
                return "nenhum buffer"
            return "\n".join(f"{i:>4} | {l}" for i, l in enumerate(buf.lines, 1)) or "(vazio)"
        if cmd in ("edit", "insert", "delete"):
            buf = self.cur()
            if buf is None:
                return "nenhum buffer"
            if cmd == "delete":
                if not args:
                    return "uso: :delete <linha>"
                n = int(args[0])
                if 1 <= n <= len(buf.lines):
                    gone = buf.lines.pop(n - 1)
                    buf.dirty = True
                    return f"apagada linha {n}: {gone!r}"
                return "linha fora do intervalo"
            if len(args) < 2:
                return f"uso: :{cmd} <linha> <texto>"
            n, txt = int(args[0]), args[1]
            if cmd == "edit":
                if 1 <= n <= len(buf.lines):
                    buf.lines[n - 1] = txt
                    buf.dirty = True
                    return f"linha {n} atualizada"
                return "linha fora do intervalo"
            # insert
            if 0 <= n <= len(buf.lines):
                buf.lines.insert(n, txt)
                buf.dirty = True
                return f"inserido após linha {n} (agora {len(buf.lines)} linhas)"
            return "linha fora do intervalo"
        if cmd == "find":
            buf = self.cur()
            if buf is None or not args:
                return "uso: :find <padrão> (com buffer aberto)"
            hits = [f"{i}: {l}" for i, l in enumerate(buf.lines, 1) if args[0].lower() in l.lower()]
            return "\n".join(hits) if hits else "sem matches"
        if cmd == "grep":
            if not args:
                return "uso: :grep <padrão>"
            try:
                proc = subprocess.run(
                    ["grep", "-rnI", "--exclude-dir=.git", "--exclude-dir=node_modules",
                     "--exclude-dir=__pycache__", args[0], "."],
                    capture_output=True, text=True, cwd=self.root, timeout=20,
                )
                out = proc.stdout.strip()
                return out[:4000] or "sem matches"
            except Exception as e:
                return f"erro: {e}"
        if cmd == "cd":
            d = (self.root / (args[0] if args else "~")).expanduser()
            if d.is_dir():
                self.root = d.resolve()
                return f"cwd do IDE: {self.root}"
            return f"diretório não existe: {d}"
        if cmd == "run":
            if not args:
                return "uso: :run <comando>"
            proc = subprocess.run(["bash", "-lc", " ".join(args)],
                                  capture_output=True, text=True, cwd=self.root, timeout=120)
            return ((proc.stdout + proc.stderr).strip() or "(sem saída)")[:4000] + f"\n[exit {proc.returncode}]"
        return f"comando IDE desconhecido: :{cmd} (digite :help)"

    HELP = (
        "Mini-IDE Joe — comandos:\n"
        "  :open <f> | :new <f> | :save [f] | :close | :bufs | :lines\n"
        "  :edit <n> <txt> | :insert <n> <txt> | :delete <n>\n"
        "  :find <p> (no buffer) | :grep <p> (no projeto)\n"
        "  :cd <dir> | :run <cmd> | :q (sair)"
    )


def run_ide(initial: str | None = None) -> None:
    ide = MiniIDE()
    print("Joe Mini-IDE — digite :help para ajuda, :q para sair.")
    if initial:
        print(ide.open_file(initial))
    while True:
        try:
            buf = ide.cur()
            tag = f"({ide.current}{'*' if buf and buf.dirty else ''})" if ide.current else ""
            line = input(f"joe:{tag}> ").strip()
        except (EOFError, KeyboardInterrupt):
            print()
            break
        if not line:
            continue
        if line in (":q", ":quit", ":exit"):
            dirty = [k for k, b in ide.buffers.items() if b.dirty]
            if dirty:
                ans = input(f"buffers não salvos: {', '.join(dirty)} — salvar tudo? [s/N] ").lower()
                if ans in ("s", "sim", "y"):
                    for k in dirty:
                        ide.current = k
                        print(ide.save())
            break
        if line == ":help":
            print(ide.HELP)
        else:
            print(ide.command(line))
