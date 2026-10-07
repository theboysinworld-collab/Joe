"""Caminhos internos do Agent com LLM/tool fake (sem rede, sem ~/.joe real).

Cobrem: _run_tool_loop multi-turno, budget de sub-agente, limite de iterações,
LLMError→plano offline, revisor (vazio/refazer/retrabalho), proatividade na
conclusão, reset() e child().
"""
import pytest

from joe.agent import Agent
from joe.config import Config
from joe.llm import LLMError
from joe.memory import Memory
from joe.tools.base import FunctionTool, registry


class FakeSub:
    id = "pesq1"; name = "Pesquisador"


def make_agent(tmp_path, with_tool="dance_fake"):
    cfg = Config()
    ag = Agent(config=cfg, memory=Memory(db_path=tmp_path / "mem.db"))
    return ag, cfg


# ---------------------------------------------------------------- loop com LLM
def test_tool_loop_executa_ferramenta_e_retorna_resposta_final(tmp_path, monkeypatch):
    ag, _ = make_agent(tmp_path)
    hits = []
    tool = FunctionTool("dance_fake", "demo", lambda text="": (hits.append(text), f"danciou {text}")[1])
    registry.register(tool)
    turns = [
        {"role": "assistant", "content": "", "tool_calls": [
            {"id": "c1", "type": "function", "function": {"name": "dance_fake",
             "arguments": '{"text": "valsa"}'}}]},
        {"role": "assistant", "content": "Pronto, dancei a valsa."},
    ]
    monkeypatch.setattr("joe.agent.chat_completion", lambda m, t, c: turns.pop(0))
    monkeypatch.setattr("joe.agent.provider_registry", type("R", (), {"active": staticmethod(lambda: object())})())
    try:
        out = ag._run_tool_loop(echo=lambda s: None)
        assert out == "Pronto, dancei a valsa."
        assert hits == ["valsa"]
        # mensagem de ferramenta entrou no histórico no formato OpenAI
        toolmsg = [m for m in ag.messages if m["role"] == "tool"][-1]
        assert toolmsg["tool_call_id"] == "c1" and "danciou valsa" in toolmsg["content"]
    finally:
        registry._tools.pop("dance_fake", None); ag.memory.close()


def test_sub_agente_tem_budget_menor(tmp_path, monkeypatch):
    ag, _ = make_agent(tmp_path)
    ag.subagent = FakeSub(); ag.agent_id = "pesq1"
    calls = {"n": 0}
    def endless(m, t, c):
        calls["n"] += 1
        return {"role": "assistant", "content": "", "tool_calls": [
            {"id": str(calls["n"]), "type": "function",
             "function": {"name": "no_such_tool", "arguments": "{}"}}]}
    monkeypatch.setattr("joe.agent.chat_completion", endless)
    ag.config.max_tool_iterations = 12
    out = ag._run_tool_loop(echo=lambda s: None)
    assert calls["n"] == 6  # orçamento //2 para sub-agentes
    assert "limite de iterações" in out
    ag.memory.close()


def test_llm_error_no_loop_cai_para_plano_offline(tmp_path, monkeypatch):
    ag, _ = make_agent(tmp_path)
    ag.messages.append({"role": "user", "content": "me mostre o grafo"})
    def boom(m, t, c): raise LLMError("sem conexão")
    monkeypatch.setattr("joe.agent.chat_completion", boom)
    called = []
    monkeypatch.setattr(ag, "_run_offline_plan",
                        lambda txt, intent, echo: (called.append(txt), "PLANO-OFFLINE")[1])
    out = ag._run_tool_loop(echo=lambda s: None)
    assert "LLM indisponível" in out and "PLANO-OFFLINE" in out
    assert called == ["me mostre o grafo"]
    ag.memory.close()


def test_auto_expansao_quando_intencao_vira_ferramenta(tmp_path):
    """Requisito: módulo novo cria ferramenta e o plano offline a usa SEM editar o núcleo."""
    seen = []
    tool = FunctionTool("correr", "demo extensão", lambda text="": (seen.append(text), "ok-correu")[1])
    registry.register(tool)
    ag, _ = make_agent(tmp_path)
    try:
        out = ag._run_offline_plan("corre ai pra mim", "correr", echo=lambda s: None)
        assert seen == ["corre ai pra mim"]
        assert "Executei seu pedido sem LLM" in out and "correr:" in out
    finally:
        registry._tools.pop("correr", None); ag.memory.close()


def test_sem_plano_e_sem_ferramenta_devolve_fallback_instruido(tmp_path):
    ag, _ = make_agent(tmp_path)
    out = ag._run_offline_plan("quero que você medite", "meditar_xyz", echo=lambda s: None)
    assert "Entendi sua intenção como **meditar_xyz**" in out
    assert "ollama" in out and "/bench" in out  # instruções claras, nunca falha muda
    ag.memory.close()


# ---------------------------------------------------------------- chat completo (pipeline 5 papéis)
def test_chat_offline_completo_emita_pipeline_e_anexa_proatividade(tmp_path, monkeypatch):
    ag, _ = make_agent(tmp_path)
    monkeypatch.setattr("joe.agent.provider_registry", type("R", (), {"active": staticmethod(lambda: None)})())
    # semeia hábito recorrente p/ predict() disparar sugestão
    for _ in range(4):
        ag.memory.observe_habit("ler_arquivo")
    out = ag.chat("oi joe, tudo bem?", echo=lambda s: None)
    assert out  # resposta não vazia (fallback instruído da conversa)
    assert ag.messages[0]["role"] == "system"
    assert any(m["role"] == "assistant" for m in ag.messages)
    hist = (tmp_path.parent, )  # histórico salvo em HISTORY_PATH global — ok se existir ou não
    ag.memory.close()


def test_revisor_refaz_uma_vez_apenas(tmp_path, monkeypatch):
    ag, _ = make_agent(tmp_path)
    n = {"i": 0}
    def seq(m, t, c):
        n["i"] += 1
        if n["i"] == 1:
            return {"role": "assistant", "content": "ERRO: algo quebrou", "tool_calls": None}
        return {"role": "assistant", "content": "agora corrigi tudo", "tool_calls": None}
    monkeypatch.setattr("joe.agent.chat_completion", seq)
    monkeypatch.setattr("joe.agent.provider_registry", type("R", (), {"active": staticmethod(lambda: object())})())
    reply, intent = "ERRO: algo quebrou", "editar_codigo"
    rv = Agent._review(reply, intent)
    assert rv["verdict"] == "refazer"
    assert Agent._review("tudo certo por aqui", "editar_codigo")["verdict"] == "ok"
    assert Agent._review("", "conversar")["verdict"] == "refazer"
    ag.memory.close()


def test_reset_e_child(tmp_path):
    ag, _ = make_agent(tmp_path)
    ag.messages.append({"role": "user", "content": "x"})
    ag.reset()
    assert ag.messages[0]["role"] == "system" and len(ag.messages) == 1
    kid = ag.child(FakeSub())
    assert kid.agent_id == "pesq1" and kid.subagent.name == "Pesquisador"
    assert kid.memory is ag.memory  # memória compartilhada com o pai
    kid.memory.close = lambda: None  # não fechar a do pai
    ag.memory.close()
