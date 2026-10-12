"""Unidades de IDE, ferramentas de arquivos e capacidades sensoriais (fallbacks offline)."""
import pytest

from joe.capabilities import (Capability, CapabilityRegistry, ImageGenCapability,
                              SpeechCapability, WebSearchCapability)
from joe.ide.editor import MiniIDE
from joe.tools.base import registry, bootstrap_tools
from joe.tools.files import ReadFileTool, WriteFileTool


# ---------------------------------------------------------------- MiniIDE (logica testavel sem tty)
@pytest.fixture()
def ide(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    return MiniIDE()


def test_ide_edit_cycle(ide, tmp_path):
    assert "novo buffer" in ide.command(":new ola.txt")
    assert "inserido" in ide.command(":insert 0 print('oi')")
    assert "atualizada" in ide.command(":edit 1 print('tchau')")
    assert "apagada" in ide.command(":delete 1")
    out = ide.command(":save")
    assert "salvo" in out and (tmp_path / "ola.txt").exists()


def test_ide_find_lines_bufs_close(ide):
    ide.command(":new a.py")
    ide.command(":insert 0 linha alpha")
    ide.command(":insert 1 linha beta")
    assert "alpha" in ide.command(":find alpha")
    assert "2 | linha beta" in ide.command(":lines")
    assert "a.py" in ide.command(":bufs")
    assert ide.command(":close") == "fechado"
    assert "nenhum buffer" in ide.command(":lines")


def test_ide_run_grep_e_comando_desconhecido(ide):
    assert "[exit 0]" in ide.command(":run echo oi")
    ide.command(":new x.txt"); ide.command(":insert 0 AGULHA123"); ide.command(":save")
    assert "AGULHA123" in ide.command(":grep AGULHA123")
    assert "desconhecido" in ide.command(":banana")


def test_ide_abre_arquivo_existente(ide, tmp_path):
    (tmp_path / "existente.txt").write_text("l1\nl2\n", encoding="utf-8")
    msg = ide.open_file("existente.txt")
    assert "aberto" in msg and "(2 linhas)" in msg


# ---------------------------------------------------------------- tools de arquivos
def test_read_write_roundtrip(tmp_path, monkeypatch):
    bootstrap_tools()
    monkeypatch.chdir(tmp_path)
    w = registry.execute("write_file", {"path": "d/t.txt", "content": "linha1\nlinha2"})
    assert w.ok
    r = registry.execute("read_file", {"path": "d/t.txt"})
    assert r.ok and "1| linha1" in r.output and r.meta["total_lines"] == 2
    # janela start/count
    r2 = registry.execute("read_file", {"path": "d/t.txt", "start": 2, "count": 1})
    assert "linha2" in r2.output and "linha1" not in r2.output
    bad = registry.execute("read_file", {"path": "nao_existe"})
    assert not bad.ok and "não existe" in bad.output


def test_edit_file_tool_preciso(tmp_path, monkeypatch):
    bootstrap_tools()
    monkeypatch.chdir(tmp_path)
    registry.execute("write_file", {"path": "c.txt", "content": "a=1\nb=2\n"})
    r = registry.execute("edit_file", {"path": "c.txt", "old": "a=1", "new": "a=99"})
    assert r.ok
    assert "a=99" in (tmp_path / "c.txt").read_text()
    miss = registry.execute("edit_file", {"path": "c.txt", "old": "zzz", "new": "x"})
    assert not miss.ok


# ---------------------------------------------------------------- capacidades: cascatas de fallback
class Dummy(SpeechCapability):
    def __init__(self, engines=(), run_result=None):
        self._engines = list(engines)
        self.calls = []

        def _run(cmd, **kw):
            self.calls.append(cmd)
            return run_result
        import subprocess
        self._real_run = subprocess.run
    def available(self):
        return bool(self._engines)
    def _engine(self):
        return self._engines[0] if self._engines else ""


def test_speak_sem_motor_devolve_texto_de_graca():
    cap = SpeechCapability.__new__(SpeechCapability)   # sem engines no ambiente de teste
    out = cap.perceive(text="olá mundo")
    assert isinstance(out, str) and out               # nunca lança; sempre informa


def test_web_search_cascata_offline_sem_rede(monkeypatch):
    cap = WebSearchCapability.__new__(WebSearchCapability)
    import urllib.request as ur

    def boom(*a, **k):
        raise OSError("sem rede (teste)")
    monkeypatch.setattr(ur, "urlopen", boom)
    out = cap.perceive(query="python 3.13 novidades")
    assert isinstance(out, str) and out               # fallback claro, sem exceção


def test_image_gen_fallback_svg(tmp_path, monkeypatch):
    cap = ImageGenCapability.__new__(ImageGenCapability)
    monkeypatch.setattr(cap, "available", lambda: False)
    out_path = tmp_path / "img.png"
    res = cap.perceive(prompt="gato quântico", out_path=str(out_path))
    assert isinstance(res, str) and out_path.parent.exists() or res  # não-crash garantido


# ---------------------------------------------------------------- registry de capacidades
def test_capability_registry_status_e_get():
    reg = CapabilityRegistry()
    class Echo(Capability):
        name = "eco"
        def available(self): return True
        def perceive(self, **kw): return "eco!"
    reg.register(Echo())
    assert reg.get("eco") is not None and reg.get("nope") is None
    st = reg.status()
    assert any(s["capacidade"] == "eco" and s["disponivel"] for s in st)
