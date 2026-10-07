"""Testes da superfície Etapa 4: grafo↔scheduler, /suggest e auto-expansão do planner."""
import time

from joe.agent import Agent
from joe.bus import bus
from joe.config import Config
from joe.memory import Memory
from joe.scheduler import Scheduler
from joe.tools.base import FunctionTool, registry
from joe.ui import STATE, render_graph


# ---------------------------------------------------------------- grafo ↔ scheduler
def test_suggest_evento_alimenta_grafo():
    STATE.suggestions.clear()
    bus.emit("suggest", items=["lembrete", "clima"])
    assert "lembrete" in STATE.suggestions and "clima" in STATE.suggestions
    # dedupe + limite de recentes
    STATE.add_suggestion("lembrete")
    assert STATE.suggestions.count("lembrete") == 1
    txt = render_graph().plain
    assert "sugerido" in txt.lower()          # linha ✦ visível no grafo


def test_sensor_vira_no_vivo_no_grafo():
    STATE.nodes.pop("bateria", None)
    bus.emit("sense.bateria", observation="92%")
    assert "bateria" in STATE.nodes
    assert "👁" in STATE.nodes["bateria"]["note"] or "92%" in STATE.nodes["bateria"]["note"]


# ---------------------------------------------------------------- /suggest (REPL)
def _make_scheduler(tmp_path):
    mem = Memory(db_path=tmp_path / "m.db")
    mem.observe_habit("lembrete")
    mem.conn.execute("UPDATE habits SET count=5, last_ts=?", (time.time() - 7200,))
    mem.conn.commit()
    s = Scheduler()
    s.set_memory(mem)
    s._last_suggest_ts = 0.0
    return s, mem


def test_maybe_suggest_emite_e_chega_ao_grafo(tmp_path):
    """Mesmo caminho do comando /suggest: _maybe_suggest → bus → STATE.suggestions."""
    STATE.suggestions.clear()
    s, mem = _make_scheduler(tmp_path)
    try:
        s._maybe_suggest()
        assert "lembrete" in STATE.suggestions      # grafo viu o evento
        n = len(STATE.suggestions)
        s._maybe_suggest()                          # cooldown: nada novo
        assert len(STATE.suggestions) == n
    finally:
        mem.close()


# ---------------------------------------------------------------- auto-expansão
def test_auto_expansao_usa_ferramenta_de_extensao_sem_editar_nucleo(tmp_path):
    """Intenção sem playbook + ferramenta registrada (ex.: criada por extensão)
    → executor a usa automaticamente, sem tocar em deterministic_plan."""
    cfg = Config()
    cfg.extra["joe_home"] = str(tmp_path)
    calls = []

    def _miolo(text: str) -> str:
        calls.append(text)
        return f"[extensão respondeu] {text[:20]}"

    tool = FunctionTool("dançar", "demo de extensão drop-in", _miolo)
    registry.register(tool)
    try:
        ag = Agent(config=cfg, memory=Memory(db_path=tmp_path / "mem.db"))
        out = ag._run_offline_plan("por favor dance agora", "dançar", echo=lambda m: None)
        assert calls, "ferramenta da extensão não foi invocada pelo plano offline"
        assert "dançar" in out.lower() or "extensão respondeu" in out
    finally:
        registry._tools.pop("dançar", None)
        ag.memory.close()
