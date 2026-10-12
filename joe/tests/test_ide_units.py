"""Testes do mini-IDE: ciclo completo de buffers, edição, busca e execução."""

import pytest

from joe.ide.editor import MiniIDE


@pytest.fixture()
def ide(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    return MiniIDE()


def test_new_insert_save_open_roundtrip(ide, tmp_path):
    assert "novo buffer" in ide.command(":new ola.txt")
    assert "inserido após linha 0" in ide.command(":insert 0 primeira linha")
    assert "linha 1 atualizada" in ide.command(":edit 1 segunda")
    assert "salvo" in ide.command(":save")
    (tmp_path / "ola.txt").exists()
    ide.command(":close")
    out = ide.command(":open ola.txt")
    assert "aberto" in out and "segunda" in ide.command(":lines")


def test_delete_e_fora_do_intervalo(ide):
    ide.command(":new a.txt")
    ide.command(":insert 0 l1")
    ide.command(":insert 1 l2")
    assert "apagada" in ide.command(":delete 1")
    assert "fora do intervalo" in ide.command(":delete 99")
    assert "fora do intervalo" in ide.command(":edit 99 x")


def test_find_e_grep_no_projeto(ide, tmp_path):
    (tmp_path / "proj.py").write_text("def foo():\n    return 42\n", encoding="utf-8")
    ide.command(":new b.txt")
    ide.command(":insert 0 procurei agulha aqui")
    assert "agulha" in ide.command(":find agulha")
    out = ide.command(":grep foo")
    assert "proj.py" in out


def test_run_executa_comando(ide):
    ide.command(":new r.txt")
    out = ide.command(":run echo oi-do-ide")
    assert "oi-do-ide" in out


def test_bufs_e_cd(ide, tmp_path):
    ide.command(":new um.txt")
    assert "um.txt" in ide.command(":bufs")
    sub = tmp_path / "sub"
    sub.mkdir()
    assert str(sub) in ide.command(f":cd {sub}") or "cd" in ide.command(f":cd {sub}").lower()


def test_sem_buffer_responde_graciosamente(ide):
    assert "nenhum buffer" in ide.command(":lines")
    assert "nenhum buffer" in ide.command(":save")
    assert "nenhum buffer" in ide.command(":edit 1 x")
    assert "uso:" in ide.command(":open")


def test_interactive_loop_sai_com_q(monkeypatch, tmp_path, capsys):
    from joe.ide.editor import run_ide
    monkeypatch.chdir(tmp_path)
    lines = iter([":new z.txt", ":insert 0 oi", ":q", "s"])  # :q pergunta salvar? -> s
    monkeypatch.setattr("builtins.input", lambda *a: next(lines))
    run_ide()  # não deve lançar exceção; :q encerra e salva os buffers sujos
    assert (tmp_path / "z.txt").read_text(encoding="utf-8").strip() == "oi"


def test_abre_arquivo_inexistente_marca_novo(ide):
    assert "ainda não salvo" in ide.command(":open nao_existe.txt")
