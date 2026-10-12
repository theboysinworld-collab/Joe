"""Voz pt-BR + barge-in: ouvir/falar simultâneos com interrupção ao digitar."""
import threading
import time

from joe.agent import Agent
from joe.config import Config
from joe.memory import Memory


class _FakeResult:
    def __init__(self, output): self.output = output


def _make_agent(tmp_path, monkeypatch, speak_out="falei (via espeak, pt-BR): oi"):
    monkeypatch.setenv("JOE_HOME", str(tmp_path / "home"))
    cfg = Config.load()
    cfg.auto_falar = True
    mem = Memory(config=cfg)
    ag = Agent(cfg, mem)
    calls = []
    class FakeTool:
        def run(self, text=""):
            calls.append(text)
            time.sleep(0.3)           # fala 'longa' p/ dar tempo do barge
            return _FakeResult(speak_out)
    from joe import agent as A
    monkeypatch.setattr(A.registry, "get", lambda name: FakeTool() if name == "speak" else None)
    return ag, calls


def test_barge_in_interrompe_fala_em_background(tmp_path, monkeypatch):
    ag, calls = _make_agent(tmp_path, monkeypatch)
    killed = []
    from joe import agent as A
    monkeypatch.setattr(A.subprocess, "run",
                        lambda cmd, **k: killed.append(cmd))
    ag._barge_event = threading.Event()
    t = threading.Thread(target=lambda: ag._maybe_speak("resposta longa qualquer"))
    t.start()
    time.sleep(0.1)                   # deixa a fala começar
    ag._barge_event.set()             # usuário digitou → corta
    t.join(timeout=5)
    assert not t.is_alive()
    assert calls and "resposta" in calls[0]      # falou em thread separada
    assert any("espeak" in " ".join(c) or "say" in " ".join(c) for c in killed)  # pkill chamado


def test_sem_barge_event_continua_sincrono(tmp_path, monkeypatch):
    ag, calls = _make_agent(tmp_path, monkeypatch)
    assert getattr(ag, "_barge_event", None) is None
    ag._maybe_speak("oi")
    assert calls == ["oi"]                       # caminho antigo intacto


def test_auto_desliga_sem_motor_de_voz(tmp_path, monkeypatch):
    ag, _ = _make_agent(tmp_path, monkeypatch,
                       speak_out="[sem motor de voz — texto para fala]: oi")
    ag._maybe_speak("oi")
    assert ag.config.auto_falar is False         # descobre uma vez, não insiste


def test_comando_voz_on_off_no_repl(tmp_path, monkeypatch):
    monkeypatch.setenv("JOE_HOME", str(tmp_path / "home"))
    from joe.cli import _slash
    cfg = Config.load(); cfg.auto_falar = False
    mem = Memory(config=cfg); ag = Agent(cfg, mem)
    out = _slash("/voz on", ag, cfg, None, mem)
    assert isinstance(ag._barge_event, threading.Event)
    assert ag.config.auto_falar is True
    out = _slash("/voz off", ag, cfg, None, mem)
    assert ag._barge_event is None and ag.config.auto_falar is False


def test_speech_capability_usa_idioma_da_config():
    from joe.capabilities import SpeechCapability
    s = SpeechCapability(language="en-US")
    assert s.language == "en-US"
    assert SpeechCapability().language == "pt-BR"   # default
