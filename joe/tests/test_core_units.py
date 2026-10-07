"""Unidades de memória, config e tools base — contratos do núcleo plugável."""
import json

import pytest

from joe.config import Config
from joe.memory import Memory, _norm, cosine
from joe.tools.base import FunctionTool, Tool, ToolResult, bootstrap_tools, registry


@pytest.fixture()
def cfg(tmp_path):
    """Config isolado apontando para o tmp_path (não depende de env externo)."""
    c = Config()
    c.extra["joe_home"] = str(tmp_path)
    return c


# ---------------------------------------------------------------- ToolResult / registry
def test_toolresult_textos_canonicos():
    assert ToolResult(output="x").as_text() == "x"
    assert ToolResult(ok=False, output="y").as_text().startswith("ERRO: y")
    assert ToolResult().as_text() == "(sem saída)"           # ok=True vazio → placeholder ok
    assert ToolResult(ok=False).as_text() == "ERRO:"         # erro sem msg mantém sinal ERRO


def test_registry_ignora_excecoes_de_tools():
    class Bomb(Tool):
        name = "bomb"
        description = "explode"
        parameters = {"type": "object", "properties": {}}

        def run(self):
            raise ValueError("boom interno")
    reg_snapshot = dict(registry._tools)
    try:
        registry.register(Bomb())
        r = registry.execute("bomb", {})
        assert not r.ok and "boom interno" in r.output
        r2 = registry.execute("nao_existente", {})
        assert not r2.ok and "desconhecida" in r2.output
    finally:
        registry._tools.clear(); registry._tools.update(reg_snapshot)


def test_function_tool_aceita_str_ou_toolresult():
    reg_snapshot = dict(registry._tools)
    try:
        registry.add("t_str", "devolve str", lambda: "simples")
        registry.add("t_res", "devolve ToolResult",
                     lambda: ToolResult(ok=False, output="falhou de propósito"))
        assert registry.execute("t_str", {}).output == "simples"
        assert not registry.execute("t_res", {}).ok
    finally:
        registry._tools.clear(); registry._tools.update(reg_snapshot)


def test_bootstrap_tools_idempotente_e_carrega_tudo():
    before = set(registry.names())
    bootstrap_tools()
    after = set(registry.names())
    assert before <= after and {"bash", "read_file", "remember", "rag_search"} <= after


# ---------------------------------------------------------------- normalização / RAG offline
def test_norm_remove_stopwords_e_tokens_curtos():
    toks = _norm("a o eu quero café coado hoje")
    # stopword 'eu' sai; 'café' fica; monossílabos 'a','o','hoje'(stop) saem
    assert "café" in toks and "quero" in toks and "coado" in toks
    assert "a" not in toks and "o" not in toks and "eu" not in toks


def test_cosine_parcial_com_dimensoes_diferentes():
    assert cosine([1.0, 0.0, 5.0], [1.0, 0.0]) == pytest.approx(1.0)


# ---------------------------------------------------------------- SQLite: dedupe, docs, eventos
def test_index_file_e_busca_doc(tmp_path, cfg):
    f = tmp_path / "proj.txt"
    f.write_text("kubernetes orquestra contêineres" * 30, encoding="utf-8")
    m = Memory(db_path=tmp_path / "i.db", config=cfg)
    n = m.index_file(str(f))
    assert n >= 2                                        # chunking funcionando
    hits = m.search("orquestração de contêineres", k=3)
    assert hits and hits[0]["kind"] == "doc"
    assert m.index_file(str(tmp_path / "inexistente.py")) == 0
    m.close()


def test_log_event_alimenta_episodios(tmp_path, cfg):
    m = Memory(db_path=tmp_path / "e.db", config=cfg)
    m.log_event("agent.tool_result", {"tool": "bash", "ok": True})
    evs = m.recent_events(topic_prefix="agent.")
    assert len(evs) == 1 and evs[0]["payload"]["tool"] == "bash"
    assert m.recent_events(topic_prefix="zzz") == []
    m.close()


def test_facts_limit_e_forget(tmp_path, cfg):
    m = Memory(db_path=tmp_path / "f.db", config=cfg)
    m.remember("capim limão acalma", tag="cha")
    m.remember("carlos gosta de sertanejo", tag="usuario")
    assert m.forget("sertanejo") == 1
    assert all("sertanejo" not in f["text"] for f in m.facts())
    assert m.forget("nada a ver") == 0
    m.close()


# ---------------------------------------------------------------- migração legado + contexto
def test_migracao_legado_json(tmp_path, cfg, monkeypatch):
    legacy = tmp_path / "memory.json"
    legacy.write_text(json.dumps({"facts": [{"text": "moro em Olinda", "tag": "casa"}]}),
                      encoding="utf-8")
    monkeypatch.setattr("joe.memory.MEMORY_PATH", legacy)
    m = Memory(db_path=tmp_path / "mig.db", config=cfg)
    assert any("Olinda" in f["text"] for f in m.facts())
    assert legacy.with_suffix(".json.bak").exists()   # nunca apaga: renomeia
    m.close()


def test_context_block_formato(tmp_path, cfg):
    m = Memory(db_path=tmp_path / "c.db", config=cfg)
    assert m.context_block() == ""
    m.remember("tenho reunião às segundas", tag="agenda")
    block = m.context_block()
    assert "MEMÓRIA PERSISTENTE" in block and "[agenda]" in block
    m.close()
