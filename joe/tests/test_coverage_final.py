"""Ramos finais: confirmação interativa do shell, saída do IDE com salvamento, main() da CLI."""
import io, sys


def test_shell_confirma_input_s_executa(tmp_path, monkeypatch):
    from joe.tools.shell import ShellTool
    from joe.config import Config
    cfg = Config(); cfg.confirm_shell = True
    sh = ShellTool(cfg)
    monkeypatch.setattr(sys, "stdin", io.StringIO("s\n"))
    res = sh.run("echo confirmado")
    assert res.ok and "confirmado" in res.output


def test_shell_confirma_input_n_cancela(tmp_path, monkeypatch):
    from joe.tools.shell import ShellTool
    from joe.config import Config
    cfg = Config(); cfg.confirm_shell = True
    sh = ShellTool(cfg)
    monkeypatch.setattr(sys, "stdin", io.StringIO("n\n"))
    res = sh.run("touch nunca.txt")
    assert not res.ok and "cancelou" in res.output
    assert not (tmp_path / "nunca.txt").exists()


def test_ide_quit_salva_buffers(tmp_path, monkeypatch, capsys):
    from joe.ide import editor
    p = tmp_path / "b.txt"
    seq = iter([f":new {p}", ":insert 0 linha-salva", ":q", "s"])
    monkeypatch.setattr("builtins.input", lambda *a: next(seq))
    editor.run_ide()  # initial=None → sem abrir arquivo
    assert p.exists() and "linha-salva" in p.read_text()


def test_cli_main_sem_args_repl(monkeypatch):
    from joe import cli
    monkeypatch.setattr(cli, "repl", lambda *a, **k: 0)
    monkeypatch.setattr(sys, "argv", ["joe"])
    assert cli.main() == 0
