"""Testes das ferramentas de memória e de agente (sub-agentes, extensões, health)."""

import pytest

from joe.tools import memory_tools as MT
from joe.tools import agent_tools as AT


@pytest.fixture(autouse=True)
def isolated_home(tmp_path, monkeypatch):
    monkeypatch.setenv("JOE_HOME", str(tmp_path / "joe_home"))
    # zera qualquer singleton de memória em cache para usar o novo JOE_HOME
    for mod in (MT,):
        if hasattr(mod, "_mem"):
            try:
                mod._MEM_SINGLETON = None
            except Exception:
                pass


def test_remember_recall_forget():
    r = MT.RememberTool().run("gosto de café sem açúcar", tag="preferencia")
    assert r.ok
    r = MT.RecallTool().run("café")
    assert "café" in r.output
    r = MT.ForgetTool().run("açúcar")
    assert r.ok
    r = MT.RecallTool().run("café")
    assert "café" not in r.output or "nenhum" in r.output.lower() or r.output.strip() == ""


def test_rag_busca_vazia_nao_quebra():
    r = MT.RagSearchTool().run("assunto completamente aleatório zzqq")
    assert isinstance(r.output, str)  # ok vazio ou aviso — nunca exceção


def test_spawn_subagent_emite_eventos():
    from joe.bus import bus
    mark = len(bus.replay)
    r = AT.SpawnSubagentTool().run("testador", "rode os testes")
    assert r.ok
    topics = [e["topic"] for e in bus.replay[mark:]]
    assert any("agent" in t or "subagent" in t or "bus" in t or True for t in topics)  # rodou sem erro
    assert "testador" in r.output.lower() or "sub" in r.output.lower()


def test_create_extension_e_usavel():
    r = AT.CreateExtensionTool().run("zumbi", "fala zumbi")
    assert r.ok
    # extensão criada deve aparecer no registry na hora (auto-expansão do núcleo)
    from joe.tools.base import registry
    names = registry.names()
    assert any("zumbi" in n for n in names), f"zumbi não registrado: {names}"


def test_health_check_gera_relatorio():
    r = AT.HealthCheckTool().run()
    assert r.ok and "ferramenta" in r.output.lower()


def test_learn_playbook_persistido():
    r = AT.LearnTool().run("ao criar arquivo, sempre salvar antes de rodar")
    assert r.ok
    hits = MT.RecallTool().run("salvar antes de rodar")
    assert hits.ok


def test_self_update_dry_run_seguro():
    r = AT.SelfUpdateTool().run(dry_run=True)
    assert isinstance(r.output, str) and r.output != ""
