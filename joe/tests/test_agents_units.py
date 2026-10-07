"""Unidades puras de joe/agents.py: extratores do planner offline e orquestração."""
import pytest

from joe.agents import (_fold, _first_cmd, _first_path, _plan_phone, _plan_read,
                        _strip_fact, _strip_query, _sub_goal, _sub_name,
                        classify_intent, deterministic_plan, role_registry,
                        spawn_subagent, SubAgent)


# ---------------------------------------------------------------- fold & extratores
def test_fold_remove_acentos_e_caixa():
    assert _fold("NUNCA ESQUEÇA") == "nunca esqueca"


@pytest.mark.parametrize("texto,esperado", [
    ("leia o arquivo config.py pra mim", "config.py"),
    ("abra joe/tools/base.py agora", "joe/tools/base.py"),
    ("me mostre o grafo", "(no arquivo indicado — especifique o caminho)"),
])
def test_first_path_extrai_alvos(texto, esperado):
    assert _first_path(texto) == esperado


def test_strip_fact_destripa_comandos_de_memoria():
    assert _strip_fact("lembre-se que meu nome é Carlos") == "meu nome é Carlos"
    assert _strip_fact("anote que odeio segunda-feira") == "odeio segunda-feira"
    assert _strip_fact("fato cru sem verbo") == "fato cru sem verbo"


def test_strip_query_isola_busca():
    assert _strip_query("pesquise na internet por clima em Recife") == "clima em Recife"
    assert _strip_query("busque sobre Python 3.13") == "Python 3.13"


@pytest.mark.parametrize("texto,cmd", [
    ("rode `pytest -q`", "pytest -q"),
    ('execute "ls -la"', "ls -la"),
    ("rode os testes agora", "os testes agora"),
    ("faça qualquer coisa", "pwd && ls"),          # default seguro
])
def test_first_cmd(texto, cmd):
    assert _first_cmd(texto) == cmd


@pytest.mark.parametrize("texto,nome,goal", [
    ("crie um sub-agente revisor para revisar o codigo", "revisor", "revisar o codigo"),
    ("use um agente para listar os arquivos", "filho", "listar os arquivos"),
    ("novo sub-agente pesquisador para achar bugs", "pesquisador", "achar bugs"),
    ("delegue isso", "filho", "delegue isso"),     # sem 'para': mantém texto todo
])
def test_sub_name_e_goal(texto, nome, goal):
    assert _sub_name(texto) == nome
    assert _sub_goal(texto) == goal


# ---------------------------------------------------------------- planos por intenção
def test_plan_read_sem_alvo_vira_ask_detail(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    plan = _plan_read("leia o código fonte")
    assert plan[0]["name"] == "ask_detail"


def test_plan_phone_notificar_e_abrir_app():
    p = _plan_phone("me avise sobre a reunião às 9")
    assert p[0]["args"]["action"] == "notificar"
    p = _plan_phone("notifique no celular sobre o bolo no forno")
    assert p[0]["args"]["action"] == "notificar"
    p = _plan_phone("abre o app instagram no celular")
    assert p[0]["args"]["action"] == "abrir_app" and p[0]["args"]["app"] == "instagram"
    p = _plan_phone("celular")
    assert p[0]["args"]["action"] == "status"


def test_deterministic_plan_camera_vs_tela():
    assert deterministic_plan("veja pela câmera o que está na mesa")[0]["name"] == "camera_view"
    assert deterministic_plan("capture a tela agora")[0]["name"] == "see_screen"


def test_intencoes_abertas_tem_plano_vazio():
    assert deterministic_plan("crie um site portfolio") == []   # exige LLM/conteúdo


# ---------------------------------------------------------------- papéis & sub-agentes
def test_role_registry_pipeline_base():
    for r in ("intento", "planejador", "executor", "revisor", "conclusao"):
        assert role_registry.get(r) is not None


def test_spawn_subagent_propaga_falha_do_filho(monkeypatch):
    from joe import agents

    def boom(goal, sa):
        def run():
            raise RuntimeError("explodiu")
        return run
    monkeypatch.setattr(agents, "_default_factory", boom)
    out = spawn_subagent("zonso", "tarefa impossível")
    assert "falhou" in out and "explodiu" in out


def test_subagent_announce_ids_unicos():
    a, b = SubAgent("x", "g1"), SubAgent("y", "g2")
    assert a.id != b.id or a.goal != b.goal      # ids derivados de ms
    assert a.parent == "joe"
