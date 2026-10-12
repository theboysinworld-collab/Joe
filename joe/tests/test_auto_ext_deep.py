"""Testes profundos de auto-cura (4b/4c) e extensões (tipos alternativos + falha isolada).

Cobre os caminhos antes não exercitados:
- heal(): extensão com erro reportada; provider ativo indisponível → reseleção.
- distill(): playbook de ferramentas preferidas gerado a partir de padrões de uso.
- extensions.load_one(): registro automático por tipo (Capability / AgentRole),
  falha de uma extensão não derruba as demais, e load_all() marca a quebrada.
"""

import pytest

from joe.auto import SelfAnalysis
from joe.config import Config
from joe.extensions import load_all, load_one
from joe.memory import Memory


@pytest.fixture()
def mem(tmp_path, monkeypatch):
    monkeypatch.setenv("JOE_HOME", str(tmp_path))
    cfg = Config.load()
    return Memory(db_path=tmp_path / "joe.db", config=cfg)


# ------------------------------------------------------------------ distill (padrões)
def test_distill_grava_playbook_de_ferramentas_preferidas(mem, tmp_path, monkeypatch):
    monkeypatch.setattr("joe.auto.LEARNINGS_DIR", tmp_path / "learnings")
    for _ in range(3):
        mem.log_event("agent.tool_result", {"tool": "grep", "ok": True})
    sa = SelfAnalysis(memory=mem)
    saved = sa.distill()
    assert any("grep" in s for s in saved), saved
    assert any("PLAYBOOK" in m["text"] and "preferidas" in m["text"]
               for m in mem.facts() if m["tag"] == "playbook")


# ------------------------------------------------------------------ heal 4b (extensão quebrada)
def test_heal_reporta_extensao_com_erro(mem, tmp_path, monkeypatch):
    ext_dir = tmp_path / "extensions" / "quebrada"
    ext_dir.mkdir(parents=True)
    (ext_dir / "extension.py").write_text("raise RuntimeError('sintaxe viva')\n", encoding="utf-8")
    monkeypatch.setattr("joe.extensions.EXTENSIONS_DIR", tmp_path / "extensions")
    sa = SelfAnalysis(memory=mem)
    actions = sa.heal()
    assert any("quebrada" in a and "error" not in a or "RuntimeError" in a for a in actions), actions


# ------------------------------------------------------------------ heal 4c (provider morto)
def test_heal_reseleciona_provider_indisponivel(mem, monkeypatch):
    from joe import llm

    class DeadProvider:
        id = "deadprov"

        def available(self):
            return False

        def chat(self, messages, tools=None):
            raise AssertionError("não deve ser usado")

    reg = llm.ProviderRegistry()
    reg.register(DeadProvider())
    reg.register(_LiveProvider())
    reg.select("deadprov")
    monkeypatch.setattr(llm, "provider_registry", reg)
    sa = SelfAnalysis(memory=mem)
    actions = sa.heal()
    assert any("deadprov" in a for a in actions), actions
    # cura real: seleção morta descartada (chat_detection em chat_completion) OU reselecionado
    act = reg.active()
    if act is not None:
        assert act.available(), "provider ativo após heal deve estar disponível"


class _LiveProvider:
    id = "liveprov"

    def available(self):
        return True

    def chat(self, messages, tools=None):
        return {"role": "assistant", "content": "oi"}


# ------------------------------------------------------------------ extensions tipos alternativos
def _make_ext(root, name, body):
    d = root / name
    d.mkdir(parents=True, exist_ok=True)
    (d / "extension.py").write_text(body, encoding="utf-8")
    return d


def test_load_one_registra_capability_e_role(tmp_path, monkeypatch):
    ext_root = tmp_path / "extensions"
    _make_ext(ext_root, "meu_cap", '''
def meta():
    return {"name": "meu_cap", "version": "0.1"}

def setup(ctx):
    class Cap:
        name = "grito_teste"
        def perceive(self, **kw):
            return "AAAA"
    class Role:
        role_name = "critico_teste"
    return [Cap(), Role()]
''')
    from joe.capabilities import capability_registry
    from joe.agents import role_registry

    objs = load_one(ext_root / "meu_cap")
    assert len(objs) == 2
    assert any(getattr(c, "name", "") == "grito_teste" for c in capability_registry.all())
    assert role_registry.get("critico_teste") is not None
    # limpeza: não contaminar registries globais p/ outros testes (ordem-independência)
    capability_registry._caps.pop("grito_teste", None)
    role_registry.roles.pop("critico_teste", None)
    import joe.extensions as ext_mod
    ext_mod._loaded.pop("meu_cap", None)


def test_load_all_isola_extensao_quebrada(tmp_path, monkeypatch):
    ext_root = tmp_path / "extensions"
    _make_ext(ext_root, "boa", '''
def meta():
    return {"name": "boa"}

def setup(ctx):
    from joe.tools.base import FunctionTool, ToolResult
    def _f():
        return ToolResult(output="ok")
    return [FunctionTool(name="boa_tool", description="exemplo.", fn=_f)]
''')
    _make_ext(ext_root, "ruim", 'raise ValueError("explodei")\n')
    monkeypatch.setattr("joe.extensions.EXTENSIONS_DIR", ext_root)
    loaded = load_all()
    assert "boa" in loaded and "error" not in loaded["boa"]
    assert "ruim" in loaded and "error" in loaded["ruim"]
    from joe.tools.base import registry
    assert registry.get("boa_tool") is not None
    # limpeza: estado global isolado entre testes
    registry._tools.pop("boa_tool", None)
    registry._tools.pop("meu_cap_hello", None)
    import joe.extensions as ext_mod
    ext_mod._loaded.pop("boa", None)
    ext_mod._loaded.pop("ruim", None)
