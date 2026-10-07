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


# ---------------------------------------------------------------- loop ao vivo (TUI grafo)
def test_live_graph_stops_on_key():
    """live_graph para quando input_fn devolve tecla de parada; desenha ≥1 frame."""
    from joe.ui import live_graph
    calls = {"n": 0}

    def inp():
        calls["n"] += 1
        return "q" if calls["n"] >= 2 else ""   # 1ª leitura: segue; 2ª: sai

    frames = live_graph(max_secs=5, input_fn=inp)
    assert frames >= 2
    assert calls["n"] == 2


def test_live_graph_stops_on_timeout():
    from joe.ui import live_graph
    frames = live_graph(max_secs=0.2, input_fn=lambda: " ")  # espaço não é tecla de parada
    assert frames >= 1


def test_cli_graph_live_command(monkeypatch):
    """'/graph live' no REPL usa o loop ao vivo e termina sem crashar."""
    import io as _io
    from rich.console import Console
    import joe.cli as cli
    import joe.ui as ui_mod

    # live_graph determinístico: 2 frames e sai (não depende de TTY)
    monkeypatch.setattr(ui_mod, "live_graph", lambda console=None, **kw: 2)
    # mas o cli importa dentro da função → patch no módulo de origem já basta
    captured = []
    fake_in = iter(["/graph live"])
    monkeypatch.setattr("builtins.input", lambda *a: next(fake_in))

    agent = cli.Agent.__new__(cli.Agent)  # não usado pelo ramo /graph
    cfg = cli.load_config() if hasattr(cli, "load_config") else None
    ui = cli.UI()
    ui.console = Console(file=_io.StringIO(), force_terminal=False, width=80)
    ok = cli._slash("/graph live", agent, cfg, ui, None)
    assert ok is True
    out = ui.console.file.getvalue()
    assert "grafo ao vivo" in out and "loop encerrado após 2 frames" in out


def test_live_graph_text_fallback_loops(tmp_path, monkeypatch):
    """Fallback sem rich.live: deve redesenhar múltiplos frames e parar só em 'q'."""
    import joe.ui as ui

    # grafo isolado: limpa estado global (outros testes podem ter populado STATE)
    ui.STATE.nodes.clear(); ui.STATE.edges.clear(); ui.STATE.suggestions.clear()
    ui.STATE.touch("usuário", "teste")

    orig_panel = ui.graph_panel

    def _boom():
        raise RuntimeError("sem rich.live neste ambiente")

    monkeypatch.setattr(ui, "graph_panel", _boom)
    calls = {"n": 0}

    def inp():
        calls["n"] += 1
        return "q" if calls["n"] >= 3 else ""

    frames = ui.live_graph(console=ui.Console(file=open(tmp_path / "out.txt", "w")),
                           max_secs=5, input_fn=inp)
    assert frames == 3 and calls["n"] == 3
    out = (tmp_path / "out.txt").read_text()
    assert "usuário" in out  # desenhou o grafo em texto
    monkeypatch.setattr(ui, "graph_panel", orig_panel)


def test_live_graph_expires_by_time(tmp_path):
    """max_secs expirado → loop termina por tempo (não por tecla), mesmo com ''."""
    import joe.ui as ui
    calls = {"n": 0}

    def inp():
        calls["n"] += 1
        return ""  # nunca é tecla de parada agora; só o tempo encerra

    frames = ui.live_graph(console=ui.Console(file=open(tmp_path / "o.txt", "w")),
                           max_secs=0.05, input_fn=inp)
    assert 1 <= frames <= 3 and calls["n"] < 200  # terminou rápido, sem spin infinito
