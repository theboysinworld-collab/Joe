"""Testes estruturais da arquitetura plug-in do Joe (sem rede, sem LLM)."""

import json

import pytest

from joe import cli
from joe.agent import Agent
from joe.agents import classify_intent, deterministic_plan, spawn_subagent, SubAgent
from joe.auto import SelfAnalysis
from joe.bus import EventBus, bus
from joe.capabilities import bootstrap_capabilities, capability_registry
from joe.config import Config
from joe.extensions import create_extension_stub, load_all
from joe.llm import ProviderRegistry, OpenAICompatProvider, benchmark
from joe.memory import Memory, _hash_vector, cosine
from joe.tools.base import FunctionTool, ToolResult, registry
from joe.ui import render_graph


@pytest.fixture()
def cfg(tmp_path, monkeypatch):
    c = Config(model="", base_url="", ollama_host="http://127.0.0.1:1")  # nada disponível
    c.save = lambda: None
    return c


# ---------------------------------------------------------------- EventBus
def test_bus_prefix_subscribe_and_isolation():
    b = EventBus()
    got = []
    b.subscribe("agent", lambda e: got.append(e))
    boom = lambda e: (_ for _ in ()).throw(RuntimeError("x"))
    b.subscribe("", boom)          # consumidor quebrado NUNCA derruba o emissor
    b.emit("agent.step", role="executor")
    b.emit("ui.noise")
    assert len(got) == 1 and got[0]["role"] == "executor"
    assert b.replay[-1]["topic"] == "ui.noise"


# ---------------------------------------------------------------- memória SQLite/RAG/hábitos
def test_memory_sqlite_rag_and_habits(tmp_path, cfg):
    m = Memory(db_path=tmp_path / "j.db", config=cfg)
    mid = m.remember("o usuário gosta de café coado", tag="preferencia")
    assert m.remember("o usuário gosta de café coado") == mid  # dedupe
    hits = m.search("qual bebida o usuário prefere?", k=3)
    assert hits and "café" in hits[0]["text"]                  # RAG semântico-offline
    m.log_event("intent.buscar_web", {})
    m.conn.execute("UPDATE habits SET last_ts=? WHERE key='intent.buscar_web'",
                   (m.conn.execute("SELECT last_ts-4000 FROM habits").fetchone()[0],))
    m.conn.commit()
    preds = m.predict()
    assert preds and preds[0]["intent"] == "buscar_web"        # previsão proativa
    m.close()


def test_hash_vector_deterministic():
    a, b = _hash_vector("olá mundo"), _hash_vector("olá mundo")
    assert a == b and cosine(a, b) > 0.99 and abs(sum(x * x for x in a) - 1) < 1e-6


# ---------------------------------------------------------------- intenção robusta + planner offline
@pytest.mark.parametrize("texto,esperada", [
    ("leia o arquivo config.py pra mim", "ler_arquivo"),
    ("rode os testes agora", "executar"),
    ("nunca esqueça que moro em Recife", "memoria"),
    ("me diga as notícias de hoje na internet", "buscar_web"),
    ("abra o whatsapp no celular", "celular"),
    ("preciso planejar minha semana de estudos", "planejar"),
])
def test_classify_intent_sem_palavra_chave_exata(texto, esperada):
    intent, score = classify_intent(texto)
    assert intent == esperada and score >= 1.0


def test_offline_plan_executes_without_llm(cfg, tmp_path, monkeypatch):
    prov = tmp_path / "oi.txt"
    prov.write_text("conteúdo secreto 42")
    monkeypatch.chdir(tmp_path)
    calls = []
    mark = len(bus.replay)  # snapshot ANTES do chat: isola dos eventos de outros testes
    real_exec = registry.execute
    monkeypatch.setattr(registry, "execute",
                        lambda name, args=None: calls.append((name, args)) or ToolResult(output="ok"))
    agent = Agent(cfg, Memory(db_path=tmp_path / "m.db", config=cfg))
    reply = agent.chat(f"leia o arquivo {prov.name}")
    assert calls and calls[0][0] == "read_file"     # agiu SEM nenhum LLM
    assert "planner determinístico" in reply
    # grafo registrou o fluxo completo: intenção→...→conclusão.
    # snapshot no início do chat: isolado de eventos globais (bus.replay é um anel
    # compartilhado entre instâncias/turnos e pode ser preenchido antes deste teste).
    roles = [e.get("role") for e in bus.replay[mark:] if e["topic"] == "agent.step"]
    assert ["intencao", "planejador", "revisor", "conclusao"] == \
        [r for r in roles if r in ("intencao", "planejador", "revisor", "conclusao")]


# ---------------------------------------------------------------- provedores: um por vez
def test_provider_registry_single_active(cfg):
    reg = ProviderRegistry().build_default(cfg)
    assert reg.active() is None
    reg.register(OpenAICompatProvider(cfg, "openrouter", "https://x/api/v1", "sk-test"))
    assert reg.select("openrouter") is None and reg.active().id == "openrouter"
    with pytest.raises(Exception):
        reg.select("nao-existe")


def test_benchmark_scores_and_picks_best(cfg, monkeypatch):
    class Fake(OpenAICompatProvider):
        def available(self): return True
        def list_models(self): return ["fast/1", "slow/9"]
        def chat(self, messages, tools):
            import time
            time.sleep(0.05 if "fast" in self.config.model else 0.3)
            return {"content": "OK"}
    reg = ProviderRegistry()
    fake = Fake(cfg, "fake", "http://x", "k")
    reg.register(fake)
    from joe import llm
    monkeypatch.setattr(llm, "provider_registry", reg)
    res = benchmark(cfg)
    assert res[0]["model"] == "fast/1" and res[0]["score"] > res[-1]["score"]
    chosen = llm.apply_benchmark_choice(cfg, res)
    assert "fast/1" in chosen and cfg.model == "fast/1"   # escolha persistida


# ---------------------------------------------------------------- extensões drop-in
def test_extension_created_and_tool_live(cfg, tmp_path, monkeypatch):
    ext_home = tmp_path / "exts"
    monkeypatch.setattr("joe.config.EXTENSIONS_DIR", ext_home, raising=False)
    monkeypatch.setattr("joe.extensions.EXTENSIONS_DIR", ext_home)
    path = create_extension_stub("clima", "consulta clima")
    assert (path / "extension.py").exists()
    tool = registry.get("clima_hello")
    assert tool is not None                                  # ferramenta JÁ viva
    r = registry.execute("clima_hello", {"who": "joe"})
    assert r.ok and "olá, joe" in r.output
    meta = load_all()
    assert "clima" in meta and "error" not in meta["clima"]


# ---------------------------------------------------------------- auto-análise / cura
def test_auto_analysis_creates_playbook(tmp_path, cfg):
    m = Memory(db_path=tmp_path / "a.db", config=cfg)
    for _ in range(3):
        m.log_event("agent.tool_result", {"tool": "grep", "ok": False})
    sa = SelfAnalysis(cfg, m)
    rep = sa.analyze()
    assert any("grep" in p for p in rep["problems"])
    saved = sa.distill()
    assert saved and any("PLAYBOOK" in s for s in saved)
    assert "grep" in m.context_block(limit=100)              # vira contexto futuro
    actions = sa.heal()
    assert actions                                           # nunca devolve vazio


# ---------------------------------------------------------------- sub-agentes no grafo
def test_spawn_subagent_emits_graph_events(monkeypatch):
    events = []
    bus.subscribe("agent", lambda e: events.append(e))
    from joe import agents
    monkeypatch.setattr(agents, "_default_factory",
                        lambda goal, sa: (lambda: f"feito: {goal}"))
    out = spawn_subagent("pesquisador", "resumir RAG")
    topics = [e["topic"] for e in events]
    assert "agent.born" in topics and "agent.handoff" in topics
    assert "feito" in out


# ---------------------------------------------------------------- UI grafo pura
def test_render_graph_shows_pipeline_nodes():
    bus.emit("agent.step", agent="joe", name="Joe", role="intencao", intent="criar_arquivo")
    txt = render_graph(width=60, opacity_pct=35).plain
    assert "planejador" in txt and "executor" in txt and "rede:" in txt


# ---------------------------------------------------------------- CLI
def test_cli_ask_offline_no_crash(cfg, tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(cli, "_boot", lambda c: None)
    monkeypatch.setattr(Config, "load", staticmethod(lambda: cfg))
    rc = cli.main(["ask", "lembre que meu nome é Ana"])
    out = capsys.readouterr().out
    assert rc == 0 and out.strip()


# ---------------------------------------------------------------- lacunas corrigidas (sessão atual)
@pytest.mark.parametrize("texto,esperada", [
    ("me mostre o grafo", "mostrar_grafo"),
    ("mostre os agentes em ação", "mostrar_grafo"),
    ("crie um sub agente pesquisador para achar bugs", "sub_agente"),
    ("delegue essa revisão para um filho", "sub_agente"),
])
def test_intencoes_de_ui_e_orquestracao(texto, esperada):
    intent, score = classify_intent(texto)
    assert intent == esperada and score >= 2.6


def test_planner_offline_usa_ferramentas_internas():
    plan = deterministic_plan("me mostre o grafo")
    assert plan and plan[0]["name"] == "show_graph"
    plan = deterministic_plan("crie um sub agente escoteiro para vigiar logs")
    assert plan and plan[0]["name"] == "spawn_subagent"
    assert plan[0]["args"]["name"] == "escoteiro"


def test_first_path_nunca_retorna_ponto_sozinho():
    from joe.agents import _first_path
    assert _first_path("leia o arquivo config.py") == "config.py"
    r = _first_path("leia o código fonte")
    assert r != "." and "especifique" in r


def test_show_graph_tool_registrada_e_executa(cfg):
    from joe.tools.base import registry, bootstrap_tools
    bootstrap_tools()
    assert registry.get("show_graph") is not None
    res = registry.execute("show_graph", {})
    assert res.ok and len(res.output) > 10


def test_repl_rota_linha_natural_pelo_pipeline(monkeypatch, cfg):
    """Regressão: '/sub pesquisador...' em linguagem natural NÃO pode cair em conversar."""
    seen = []
    monkeypatch.setattr(cli.provider_registry, "active", lambda: None)
    agent = Agent(cfg, Memory(config=cfg))
    orig = agent._run_offline_plan
    def spy(text, intent, echo):
        seen.append(intent)
        return orig(text, intent, echo)
    monkeypatch.setattr(agent, "_run_offline_plan", spy)
    out = agent.chat("crie um sub agente revisor para checar erros")
    assert seen == ["sub_agente"]
    assert "spawn_subagent" in out or "revisor" in out
