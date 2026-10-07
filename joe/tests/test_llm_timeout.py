"""Timeout configurável de LLM: modelos locais lentos (>120s) não devem quebrar o Joe."""
import time
import urllib.error

from joe import llm
from joe.config import Config


def test_config_expoe_llm_timeout_padrao_300():
    cfg = Config.load()
    assert cfg.llm_timeout == 300


def test_set_llm_timeout_propaga_para_post(monkeypatch):
    llm.set_llm_timeout(9)
    used = {}

    def fake(req, timeout=None):
        used["timeout"] = timeout
        raise urllib.error.URLError("timed out")

    monkeypatch.setattr(llm.urllib.request, "urlopen", fake)
    try:
        llm._post_json("http://x/api/chat", {"a": 1})
        assert False, "deveria lançar"
    except urllib.error.URLError:
        pass
    assert used["timeout"] == 9          # config mandou no timeout real
    # e é retryável: tentou mais de uma vez (backoff total ~1.5s mínimo)


def test_bootstrap_aplica_timeout_da_config():
    cfg = Config.load()
    cfg.llm_timeout = 456
    llm.bootstrap(cfg)
    assert llm._LLM_TIMEOUT[0] == 456


def test_agent_inicializa_timeout_sem_llm_disponivel(tmp_path, monkeypatch):
    monkeypatch.setenv("JOE_HOME", str(tmp_path))
    from joe.agent import Agent
    a = Agent(config=Config.load())
    assert llm._LLM_TIMEOUT[0] >= 5      # nunca zero/negativo


def test_slash_config_llm_timeout_aplica_imediato(tmp_path, monkeypatch):
    """/config llm_timeout 450 salva E aplica sem reiniciar o processo."""
    import joe.cli as cli
    from joe.config import Config

    monkeypatch.setenv("JOE_HOME", str(tmp_path))
    cfg = Config.load()
    agent = cli.Agent.__new__(cli.Agent)   # ramo /config não usa o agente
    ui = cli.UI()
    ok = cli._slash("/config llm_timeout 450", agent, cfg, ui, None)
    assert ok is True
    assert cfg.llm_timeout == 450                       # persistido no objeto
    assert Config.load().llm_timeout == 450             # salvo em ~/.joe/config.json
    assert llm._LLM_TIMEOUT[0] == 450                   # aplicado ao módulo llm na hora


def test_slash_config_llm_timeout_invalido_nao_corrompe(tmp_path, monkeypatch, capsys):
    import joe.cli as cli
    from joe.config import Config

    monkeypatch.setenv("JOE_HOME", str(tmp_path))
    cfg = Config.load()
    before = cfg.llm_timeout
    agent = cli.Agent.__new__(cli.Agent)
    ui = cli.UI()
    ok = cli._slash("/config llm_timeout abc", agent, cfg, ui, None)
    assert ok is True
    assert "número inteiro" in capsys.readouterr().out
    assert cfg.llm_timeout == before                    # valor inválido rejeitado
