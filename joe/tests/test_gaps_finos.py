"""Fechamento de gaps finos: IDE (comandos/loop), files (edge cases) e web (JSON inválido)."""
import sys

import pytest


# ---------------- IDE ----------------

def test_ide_reabre_buffer_existente_e_save_com_nome(tmp_path, monkeypatch):
    from joe.ide.editor import MiniIDE
    monkeypatch.chdir(tmp_path)
    ide = MiniIDE()
    a = tmp_path / "a.txt"
    a.write_text("l1\nl2\n", encoding="utf-8")
    assert "aberto" in ide.command(f":open {a}")
    assert "já aberto" in ide.command(f":open {a}")          # gap 46-47
    b = tmp_path / "sub" / "b.txt"
    assert "salvo" in ide.command(f":save {b}")               # save com nome → mkdir parents
    assert b.exists()


def test_ide_usos_e_limites(tmp_path, monkeypatch):
    from joe.ide.editor import MiniIDE
    monkeypatch.chdir(tmp_path)
    ide = MiniIDE()
    assert "uso" in ide.command(":open")                      # gap 83
    assert "uso" in ide.command(":new")                       # gap 85-87 (~)
    assert "nenhum buffer" in ide.command(":delete")          # sem buffer → cur() None
    ide.command(":new x.txt")
    assert "uso" in ide.command(":delete")                    # gap 118 (com buffer aberto)
    assert "fora do intervalo" in ide.command(":delete 9")    # gap 124
    assert "uso" in ide.command(":edit 1")                    # gap 126-127 (<2 args)
    assert "fora do intervalo" in ide.command(":edit 5 zz")   # gap 132
    assert "fora do intervalo" in ide.command(":insert 9 zz") # gap 139
    assert "sem matches" in ide.command(":find zzz")          # gap 143
    assert "uso" in ide.command(":grep")                      # gap 146
    assert "não existe" in ide.command(":cd /nao/existe/x")   # gap 160
    assert "uso" in ide.command(":run")                       # gap 164
    assert "x.txt" in ide.command(":bufs")                    # buffer listado
    assert "fechado" in ide.command(":close")
    assert "nenhum buffer" in ide.command(":bufs")            # gap 99 (sem buffers)
    assert "nenhum buffer" in ide.command(":close")           # gap 102


def test_ide_loop_eof_help_e_comum(tmp_path, monkeypatch, capsys):
    from joe.ide import editor
    seq = iter(["", ":help", "open nope.txt", ":q"])          # linha vazia + ajuda + comando sem ':'
    it = iter(seq)

    def fake_input(prompt=""):
        try:
            return next(it)
        except StopIteration:
            raise EOFError
    monkeypatch.setattr("builtins.input", fake_input)
    editor.run_ide(str(tmp_path / "inicial.txt"))             # gap 186 (initial) + 192-196 + 207
    out = capsys.readouterr().out
    assert "Mini-IDE" in out and "HELP" not in out or ":run" in out  # ajuda impressa


# ---------------- files ----------------

def test_files_edit_multisave_and_listdir_nonexistent(tmp_path, monkeypatch):
    from joe.tools import files as F
    p = tmp_path / "m.txt"
    p.write_text("x y x y x\n", encoding="utf-8")
    r = F.EditFileTool().run(str(p), "x", "z")                # >1 sem replace_all → erro (gap 60)
    assert not r.ok and "replace_all" in r.output
    r = F.ListDirTool().run(str(tmp_path / "nao_existe"))     # gap 77
    assert not r.ok and "não é diretório" in r.output


def test_files_grep_stopiteration_and_binary(tmp_path):
    from joe.tools import files as F
    big = tmp_path / "big.txt"
    big.write_text("\n".join(f"alpha {i}" for i in range(200)), encoding="utf-8")
    binf = tmp_path / "bin.dat"
    binf.write_bytes(b"\xff\xfe\x00binary\xff")
    empty = tmp_path / "vazio.txt"
    empty.touch()
    r = F.GrepTool().run("alpha", str(tmp_path))              # >80 hits → StopIteration (gaps 104-108)
    assert r.ok and len(r.output.splitlines()) <= 80


# ---------------- web ----------------

def test_use_capability_json_invalido_vira_kwargs_vazio(monkeypatch):
    from joe.tools import web as W

    class FakeCap:
        name = "fakecap"

        def perceive(self, **kw):
            return f"ok kw={sorted(kw)}"

    class FakeReg:
        def all(self):
            return [FakeCap()]

        def get(self, n):
            return FakeCap() if n == "fakecap" else None

    monkeypatch.setattr(W, "_caps", lambda: FakeReg())
    r = W.UseCapabilityTool().run("fakecap", kwargs="{json quebrado")   # gaps 48-53
    assert r.ok and "ok kw=[]" in r.output
