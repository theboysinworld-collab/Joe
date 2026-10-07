"""Unidades de joe/llm.py: registry plugável, benchmark e seleção (sem rede real)."""
import pytest

from joe.config import Config
from joe.llm import (LLMError, Provider, ProviderRegistry, OpenAICompatProvider,
                     apply_benchmark_choice, auto_select, bootstrap, chat_completion,
                     embed_texts, provider_registry)


class FakeProvider(Provider):
    id = "fake"

    def __init__(self, config, resp=None, vecs=None, avail=True):
        super().__init__(config)
        self._resp = resp or {"role": "assistant", "content": "oi"}
        self._vecs = vecs
        self._avail = avail

    def available(self):
        return self._avail

    def list_models(self):
        return ["fake-mini"]

    def chat(self, messages, tools):
        return dict(self._resp)

    def embed(self, texts):
        return self._vecs


def _cfg(**kw):
    c = Config(ollama_host="http://127.0.0.1:1", model="", **kw)
    c.save = lambda: None
    return c


# ---------------------------------------------------------------- registry
def test_registry_um_ativo_por_vez():
    reg = ProviderRegistry()
    cfg = _cfg()
    a, b = FakeProvider(cfg), FakeProvider(cfg)
    b.id = "outro"
    reg.register(a); reg.register(b)
    reg.select("fake")
    assert reg.active().id == "fake"
    reg.select("outro")
    assert reg.active().id == "outro"          # troca limpa, nunca dois ativos
    with pytest.raises(LLMError):
        reg.select("inesistente")


def test_available_filtra_indisponiveis():
    reg = ProviderRegistry()
    cfg = _cfg()
    ok = FakeProvider(cfg, avail=True)
    ko = FakeProvider(cfg, avail=False); ko.id = "ko"
    reg.register(ok); reg.register(ko)
    assert [p.id for p in reg.available()] == ["fake"]


# ---------------------------------------------------------------- fachadas estáveis
def test_chat_completion_usa_provider_ativo(monkeypatch):
    cfg = _cfg()
    fake = FakeProvider(cfg, resp={"role": "assistant", "content": "olá do fake"})
    monkeypatch.setattr(provider_registry, "_providers", {"fake": fake}, raising=False)
    monkeypatch.setattr(provider_registry, "_active_id", "fake", raising=False)
    msg = chat_completion([{"role": "user", "content": "oi"}], None, cfg)
    assert msg["content"] == "olá do fake"


def test_chat_completion_sem_provider_limpa_selecao(monkeypatch):
    cfg = _cfg()
    dead = FakeProvider(cfg, avail=False)
    monkeypatch.setattr(provider_registry, "_providers", {"fake": dead}, raising=False)
    monkeypatch.setattr(provider_registry, "_active_id", "fake", raising=False)
    with pytest.raises(LLMError):
        chat_completion([{"role": "user", "content": "oi"}], None, cfg)
    # auto-limpeza: seleção morta é descartada → próximo turno cai no planner offline
    assert provider_registry._active_id != "fake"


def test_embed_texts_fallback_none(monkeypatch):
    cfg = _cfg()
    vecs = [[0.1, 0.2]]
    fake = FakeProvider(cfg, vecs=vecs)
    monkeypatch.setattr(provider_registry, "_providers", {"fake": fake}, raising=False)
    monkeypatch.setattr(provider_registry, "_active_id", "fake", raising=False)
    assert embed_texts(["x"], cfg) == vecs
    # sem provedor nenhum → None (memória usa hashing determinístico); restaura estado p/ testes
    saved = dict(provider_registry._providers), provider_registry._active_id
    try:
        provider_registry._providers = {}
        provider_registry._active_id = None
        assert embed_texts(["x"], cfg) is None
    finally:
        provider_registry._providers, provider_registry._active_id = saved


# ---------------------------------------------------------------- openai-compatível
def test_openai_compat_disponivel_somente_com_chave():
    cfg = _cfg()
    p = OpenAICompatProvider(cfg, "or", "https://openrouter.ai/api/v1", key=None)
    assert not p.available()
    cfg.openrouter_api_key = "sk-or-123"
    p2 = OpenAICompatProvider(cfg, "or", "https://openrouter.ai/api/v1", key="sk-or-123")
    assert p2.available()


# ---------------------------------------------------------------- benchmark/auto-select
def test_auto_select_escolhe_por_prioridade(monkeypatch):
    """Ordem de custo-zero: ollama > openrouter > openai (requisito: achar ollama local)."""
    cfg = _cfg()
    orr = FakeProvider(cfg); orr.id = "openrouter"
    oll = FakeProvider(cfg); oll.id = "ollama"
    monkeypatch.setattr(provider_registry, "_providers",
                        {"openrouter": orr, "ollama": oll}, raising=False)
    monkeypatch.setattr(provider_registry, "_active_id", None, raising=False)
    assert auto_select(cfg) == "ollama"          # local vence mesmo registrado depois
    assert provider_registry.active().id == "ollama"


def test_auto_select_sem_disponiveis_retorna_none(monkeypatch):
    cfg = _cfg()
    ko = FakeProvider(cfg, avail=False); ko.id = "ollama"
    monkeypatch.setattr(provider_registry, "_providers", {"ollama": ko}, raising=False)
    monkeypatch.setattr(provider_registry, "_active_id", None, raising=False)
    assert auto_select(cfg) is None


def test_apply_benchmark_choice_persiste_vencedor(monkeypatch):
    cfg = _cfg()
    fake = FakeProvider(cfg); fake.id = "ollama"
    monkeypatch.setattr(provider_registry, "_providers", {"ollama": fake}, raising=False)
    res = [
        {"provider": "ollama", "model": "lento", "ok": True, "latency_s": 9.0, "score": 5},
        {"provider": "ollama", "model": "rapido", "ok": True, "latency_s": 0.4, "score": 9},
        {"provider": "openrouter", "model": "morto", "ok": False, "latency_s": 30, "score": 0},
    ]
    choice = apply_benchmark_choice(cfg, res)
    assert choice and "rapido" in choice
    assert cfg.model == "rapido"                 # escolha persistida p/ próximos turnos
    assert provider_registry._active_id == "ollama"


def test_bootstrap_registra_tres_provedores_base():
    reg = bootstrap(_cfg())
    ids = {p.id for p in reg.all()}
    assert {"ollama", "openai", "openrouter"} <= ids
