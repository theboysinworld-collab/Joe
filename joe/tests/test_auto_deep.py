"""Testes profundos do sistema imunológico (auto.py): análise, destilação, cura, evolução."""

import json

import pytest

from joe.auto import SelfAnalysis
from joe.config import Config
from joe.memory import Memory


@pytest.fixture()
def mem(tmp_path, monkeypatch):
    monkeypatch.setenv("JOE_HOME", str(tmp_path))
    cfg = Config.load()
    return Memory(db=tmp_path / "joe.db", config=cfg)


def _fail_events(mem, tool, n):
    for _ in range(n):
        mem.log_event("agent.tool_result", {"tool": tool, "ok": False})


def test_analyze_detecta_falha_recorrente(mem):
    _fail_events(mem, "bash", 3)
    rep = SelfAnalysis(memory=mem).analyze()
    assert rep["events_seen"] >= 3
    assert any("bash" in p for p in rep["problems"])
    assert rep["failed_tools"]["bash"] == 3


def test_distill_grava_playbook_na_memoria(mem):
    _fail_events(mem, "grep", 2)
    sa = SelfAnalysis(memory=mem)
    saved = sa.distill()
    assert saved and any("grep" in s for s in saved)
    tags = [f["tag"] for f in mem.facts(limit=100)]
    assert "playbook" in tags          # lição entra no contexto dos próximos turnos


def test_heal_saudavel_nao_apaga_nada(mem):
    mem.remember("fato importante")
    actions = SelfAnalysis(memory=mem).heal()
    assert actions == ["nada a curar ✅"]
    assert len(mem.facts()) == 1       # princípio: nunca apaga


def test_heal_recupera_banco_corrompido(tmp_path, monkeypatch):
    """Cura real: SQLite ilegível → backup .bak + recriação limpa."""
    monkeypatch.setenv("JOE_HOME", str(tmp_path))
    db = tmp_path / "corrompido.db"
    m = Memory(db=db)
    m.remember("antes da corrupção")
    m.conn.close()
    db.write_bytes(b"nao eh sqlite valido!!!")   # corrompe o arquivo
    m2 = Memory(db=db)                            # reabre (sqlite tolera?) força query
    sa = SelfAnalysis(memory=m2)
    # garante que a consulta interna vai falhar: tabela some
    try:
        m2.conn.execute("DROP TABLE facts")
    except Exception:
        pass
    actions = sa.heal()
    assert any("banco corrompido" in a or "restaurado" in a for a in actions), actions
    assert db.with_suffix(".db.bak").exists()     # backup preservado, nada perdido


def test_evolve_report_mostra_aprendizados(mem):
    mem.remember("PLAYBOOK: teste de aprendizado", tag="playbook")
    txt = SelfAnalysis(memory=mem).evolve_report()
    assert "playbooks próprios" in txt
    assert "teste de aprendizado" in txt
