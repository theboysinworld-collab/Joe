"""Testes de provedores LLM com HTTP mockado (sem rede real).

Cobrem: OllamaProvider (tags/chat/embed/_pick_chat_model), OpenAICompatProvider
(chat/erros empacotados em LLMError) e fachadas chat_completion/embed_texts.
"""
import json

import pytest

from joe.config import Config
from joe.llm import (LLMError, OllamaProvider, OpenAICompatProvider,
                     ProviderRegistry, chat_completion, embed_texts)


class FakeResp:
    def __init__(self, payload): self.payload = payload
    def read(self): return json.dumps(self.payload).encode()


def make_provider():
    c = Config(ollama_host="http://fake:1", model="qwen2.5-coder:1.5b",
               embed_model="nomic-embed-text")
    return OllamaProvider(c)


# ------------------------------------------------------------- Ollama
def test_ollama_list_models_e_available(monkeypatch):
    p = make_provider()
    monkeypatch.setattr("joe.llm._get_json",
                        lambda url, headers=None, timeout=10: {"models": [{"name": "llama3"}, {"name": "qwen2.5-coder:1.5b"}]})
    assert p.list_models() == ["llama3", "qwen2.5-coder:1.5b"]
    assert p.available() is True


def test_ollama_available_false_em_falha_de_rede(monkeypatch):
    p = make_provider()
    def boom(*a, **k): raise OSError("connection refused")
    monkeypatch.setattr("joe.llm._get_json", boom)
    assert p.available() is False


def test_ollama_chat_normaliza_tool_calls(monkeypatch):
    p = make_provider()
    def fake_post(url, payload, headers=None, timeout=120):
        assert payload["model"] == "qwen2.5-coder:1.5b"
        assert payload["stream"] is False
        return {"message": {"content": "ok", "tool_calls": [
            {"function": {"name": "bash", "arguments": {"command": "ls"}}}]}}
    monkeypatch.setattr("joe.llm._post_json", fake_post)
    out = p.chat([{"role": "user", "content": "oi"}],
                 [{"type": "function", "function": {"name": "bash"}}])
    assert out["role"] == "assistant" and out["content"] == "ok"
    tc = out["tool_calls"][0]
    assert tc["function"]["name"] == "bash"
    assert json.loads(tc["function"]["arguments"]) == {"command": "ls"}


def test_ollama_embed_ok_e_falha_vira_none(monkeypatch):
    p = make_provider()
    monkeypatch.setattr("joe.llm._post_json",
                        lambda url, payload, headers=None, timeout=30: {"embedding": [0.1, 0.2]})
    assert p.embed(["a", "b"]) == [[0.1, 0.2], [0.1, 0.2]]
    def boom(*a, **k): raise OSError
    monkeypatch.setattr("joe.llm._post_json", boom)
    assert p.embed(["a"]) is None


def test_pick_chat_model_por_hint_preferencia_e_vazio(monkeypatch):
    p = make_provider()
    monkeypatch.setattr("joe.llm._get_json",
                        lambda *a, **k: {"models": [{"name": "llava:7b"}, {"name": "deepseek-coder:latest"}]})
    # hint não bate → preferência "coder" vence antes de llama/mistral
    assert p._pick_chat_model() == "deepseek-coder:latest"
    p.config.model = ""  # força seleção automática no chat
    p.config.chat_hint = "llava"
    assert p._pick_chat_model() == "llava:7b"
    monkeypatch.setattr("joe.llm._get_json", lambda *a, **k: {"models": []})
    assert p._pick_chat_model() == ""


# ------------------------------------------------------------- OpenAI-compatível
def _compat(key="sk-test"):
    c = Config(api_key_env="", model="gpt-4o-mini")
    c.extra["api_key"] = key  # api_key do provider vem do arg ou config.api_key
    p = OpenAICompatProvider(c, "openai", "https://api.fake/v1", key)
    return p


def test_openai_compat_available_depende_somente_da_chave():
    assert _compat().available() is True
    assert _compat(key="").available() is False


def test_openai_compat_chat_envia_autorizacao_e_tools(monkeypatch):
    p = _compat()
    captured = {}
    def fake_post(url, payload, headers=None, timeout=120):
        captured.update(url=url, headers=headers, payload=payload)
        return {"choices": [{"message": {"role": "assistant", "content": "olá"}}]}
    monkeypatch.setattr("joe.llm._post_json", fake_post)
    out = p.chat([{"role": "user", "content": "oi"}], [{"type": "function"}])
    assert out["content"] == "olá"
    assert captured["headers"]["Authorization"] == "Bearer sk-test"
    assert captured["payload"]["tools"] == [{"type": "function"}]
    assert captured["payload"]["tool_choice"] == "auto"


def test_openai_compat_erros_http_viram_llmerror(monkeypatch):
    import urllib.error
    p = _compat()
    def boom(url, payload, headers=None, timeout=120):
        raise urllib.error.HTTPError(url, 401, "unauthorized", {}, None)
    monkeypatch.setattr("joe.llm._post_json", boom)
    with pytest.raises(LLMError, match="HTTP 401"):
        p.chat([{"role": "user", "content": "x"}], None)
    def netfail(url, payload, headers=None, timeout=120):
        raise urllib.error.URLError("no route")
    monkeypatch.setattr("joe.llm._post_json", netfail)
    with pytest.raises(LLMError, match="rede"):
        p.chat([{"role": "user", "content": "x"}], None)


def test_openai_compat_list_models_falha_graciosa(monkeypatch):
    p = _compat()
    monkeypatch.setattr("joe.llm._get_json",
                        lambda *a, **k: {"data": [{"id": f"m{i}"} for i in range(60)]})
    assert len(p.list_models()) == 50  # truncado
    def boom(*a, **k): raise OSError
    monkeypatch.setattr("joe.llm._get_json", boom)
    assert p.list_models() == []


# ------------------------------------------------------------- registry + fachadas
def test_registry_selecao_um_por_vez_e_erro_desconhecido():
    r = ProviderRegistry()
    p = make_provider()
    r.register(p)
    assert r.active() is None
    r.select("ollama")
    assert r.active().id == "ollama"
    with pytest.raises(LLMError, match="provider desconhecido"):
        r.select("groq")


def test_chat_completion_auto_cura_selecao_morta(monkeypatch):
    from joe import llm
    cfg = Config()
    r = llm.provider_registry
    old = dict(r._providers), r._active_id
    try:
        dead = make_provider()
        monkeypatch.setattr(dead, "available", lambda: False)
        r.register(dead); r.select("ollama")
        monkeypatch.setattr(llm, "bootstrap", lambda c: r)
        monkeypatch.setattr(llm, "auto_select", lambda c: None)
        with pytest.raises(LLMError, match="nenhum provedor"):
            chat_completion([{"role": "user", "content": "oi"}], None, cfg)
        assert r._active_id is None  # a seleção morta foi descartada
    finally:
        r._providers, r._active_id = old


def test_embed_texts_usa_primeiro_com_suporte(monkeypatch):
    from joe import llm
    cfg = Config()
    class Emb:
        id = "emb"; 
        def embed(self, texts): return [[1.0] for _ in texts]
    old = dict(llm.provider_registry._providers), llm.provider_registry._active_id
    try:
        llm.provider_registry._providers["emb"] = Emb()
        llm.provider_registry._active_id = None
        monkeypatch.setattr(llm.provider_registry, "available", lambda: [Emb()])
        assert embed_texts(["oi"], cfg) == [[1.0]]
    finally:
        llm.provider_registry._providers, llm.provider_registry._active_id = old
