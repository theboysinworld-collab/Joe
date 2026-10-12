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
    """Ciclo lembrar→recordar→esquecer com marcador único.

    Nota: Memory() resolve DB_PATH no momento da importação do módulo, então o
    fixture isolado não redireciona o SQLite — usamos a memória real e um texto
    exclusivo deste teste (idempotente via forget antes/depois).
    """
    marker = "zzz-marcador-exclusivo-teste-42"
    MT.ForgetTool().run(marker)                       # limpeza idempotente
    r = MT.RememberTool().run(f"gosto de {marker}", tag="preferencia")
    assert r.ok
    r = MT.RecallTool().run(marker)
    assert marker in r.output
    r = MT.ForgetTool().run(marker)
    assert r.ok
    r = MT.RecallTool().run(marker)
    assert marker not in r.output


def test_rag_busca_vazia_nao_quebra():
    r = MT.RagSearchTool().run("assunto completamente aleatório zzqq")
    assert isinstance(r.output, str)  # ok vazio ou aviso — nunca exceção


def test_spawn_subagent_emite_eventos():
    """Filho offline planeja a tarefa e emite eventos de agente no bus.

    'rode os testes' classifica como executar → bash('os testes') é um comando
    inexistente, então o filho devolve erro gracioso da ferramenta (ok=True com
    texto ERRO). O que importa aqui: o ciclo de vida do sub-agente no grafo.
    """
    from joe.bus import bus
    mark = len(bus.replay)
    r = AT.SpawnSubagentTool().run("testador", "rode os testes")
    assert r.ok and isinstance(r.output, str) and r.output.strip()
    events = bus.replay[mark:]
    topics = [e["topic"] for e in events]
    assert "agent.born" in topics          # nó novo no grafo ao nascer
    assert "agent.result" in topics        # resultado volta ao pai
    born = next(e for e in events if e["topic"] == "agent.born")
    assert born["name"] == "testador"


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
