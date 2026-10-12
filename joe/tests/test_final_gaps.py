"""Últimos gaps: has_any_llm, proatividade no chat, subagente sem sugestões, histórico."""
import json


def test_has_any_llm_true_com_api_key(tmp_path, monkeypatch):
    from joe.config import Config
    monkeypatch.setenv("JOE_HOME", str(tmp_path))
    monkeypatch.setenv("OPENAI_API_KEY", "sk-test")
    assert Config().has_any_llm is True


def test_has_any_llm_false_sem_rota(tmp_path, monkeypatch):
    from joe.config import Config
    monkeypatch.setenv("JOE_HOME", str(tmp_path))
    for v in ("OPENAI_API_KEY", "OPENROUTER_API_KEY"):
        monkeypatch.delenv(v, raising=False)
    c = Config(); c.ollama_host = "http://127.0.0.1:1"; c.openrouter_api_key = None
    assert c.has_any_llm is False  # porta fechada → sem rota


def test_proatividade_injeta_sugestao_na_resposta(tmp_path, monkeypatch):
    """memory.predict() com sugestão → reply ganha o 💡 (ramo 140-141)."""
    monkeypatch.setenv("JOE_HOME", str(tmp_path))
    from joe.agent import Agent
    ag = Agent()
    ag.memory.predict = lambda: [{"intent": "ler_codigo", "count": 5}]
    out = ag.chat("oi joe")
    assert "💡" in out and "ler codigo" in out


def test_subagente_nao_recebe_sugestoes(tmp_path, monkeypatch):
    """objeto subagente (não-None) pula a proatividade (ramo negativo 140)."""
    monkeypatch.setenv("JOE_HOME", str(tmp_path))
    from joe.agent import Agent

    class FakeSub:
        id = "sub-1"; name = "pesquisador"

    ag = Agent(subagent=FakeSub())
    ag.memory.predict = lambda: [{"intent": "x", "count": 9}]
    out = ag.chat("tarefa curta")
    assert "💡" not in out


def test_historio_e_salvo_por_turno(tmp_path, monkeypatch):
    """HISTORY_PATH é módulo-level; recarrega config para apontar ao tmp_path."""
    monkeypatch.setenv("JOE_HOME", str(tmp_path))
    import importlib
    import joe.config as cfg
    importlib.reload(cfg)
    import joe.agent as agmod
    importlib.reload(agmod)
    ag = agmod.Agent()
    ag.chat("lembre-se que meu nome é Zeta")
    log = tmp_path / "history" / "log.jsonl"
    assert log.exists(), "histórico JSONL não gravado"
    rec = json.loads(log.read_text().splitlines()[-1])
    assert "Zeta" in rec["user"]
