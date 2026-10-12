"""Pente fino das respostas às perguntas do usuário:
1) run.py aceita pedido posicional (forma natural, sem --once).
2) auto_falar: fala ao concluir quando ativado; desliga sozinho sem motor de voz.
3) /config auto_falar no REPL trata booleanos."""
import importlib
import os
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture()
def joe_env(tmp_path, monkeypatch):
    """JOE_HOME isolado + reimport dos módulos de config/memória."""
    monkeypatch.setenv("JOE_HOME", str(tmp_path))
    for mod in ("joe.config", "joe.memory"):
        importlib.reload(importlib.import_module(mod))
    yield tmp_path
    for mod in ("joe.config", "joe.memory"):
        importlib.reload(importlib.import_module(mod))


def test_run_positional_pedido(tmp_path):
    env = dict(os.environ, JOE_HOME=str(tmp_path))
    r = subprocess.run([sys.executable, str(ROOT / "run.py"), "me mostre os arquivos"],
                       capture_output=True, text=True, timeout=90, env=env, cwd=ROOT)
    assert r.returncode == 0, r.stderr[-400:]
    assert "list_dir" in r.stdout or "README" in r.stdout


def _fake_speak(monkeypatch, output):
    from joe.tools.base import ToolResult, registry
    called = []

    class FakeSpeak:
        def run(self, text):
            called.append(text)
            return ToolResult(output=output)

    real_get = registry.get
    monkeypatch.setattr(registry, "get",
                        lambda name: FakeSpeak() if name == "speak" else real_get(name))
    return called


def test_auto_falar_desliga_sem_motor(joe_env, monkeypatch):
    from joe.config import Config
    from joe.memory import Memory
    called = _fake_speak(monkeypatch, "[sem motor de voz — texto para fala]: ok")
    cfg = Config(); cfg.auto_falar = True
    from joe.agent import Agent
    Agent(cfg, Memory(config=cfg)).chat("oi joe")
    assert called, "deveria ter tentado falar ao concluir"
    assert cfg.auto_falar is False, "sem motor de voz deve desligar auto_falar (não insistir)"


def test_auto_falar_liga_quando_ha_motor(joe_env, monkeypatch):
    from joe.config import Config
    from joe.memory import Memory
    called = _fake_speak(monkeypatch, "falei (via espeak): oi")
    cfg = Config(); cfg.auto_falar = True
    from joe.agent import Agent
    Agent(cfg, Memory(config=cfg)).chat("oi joe")
    assert called and cfg.auto_falar is True


def test_auto_falar_off_nao_fala(joe_env, monkeypatch):
    from joe.config import Config
    from joe.memory import Memory
    called = _fake_speak(monkeypatch, "falei (via espeak): oi")
    cfg = Config(); cfg.auto_falar = False
    from joe.agent import Agent
    Agent(cfg, Memory(config=cfg)).chat("oi joe")
    assert not called


def test_config_auto_falar_bool_no_repl(joe_env):
    from joe.cli import _slash
    from joe.config import Config
    from joe.memory import Memory
    cfg = Config(); mem = Memory(config=cfg)

    class StubUI:
        def graph(self): pass
    _slash("/config auto_falar sim", agent=None, cfg=cfg, ui=StubUI(), mem=mem)
    assert cfg.auto_falar is True
    _slash("/config auto_falar nao", agent=None, cfg=cfg, ui=StubUI(), mem=mem)
    assert cfg.auto_falar is False
