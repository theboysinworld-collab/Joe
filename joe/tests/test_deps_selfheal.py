"""Camada de dependências: detecção, auto-baixa via pip e fallbacks."""
import pytest
from joe import deps


@pytest.fixture(autouse=True)
def _clear_cache():
    deps.clear_caches()
    yield
    deps.clear_caches()


def test_have_e_status_nao_instalam(monkeypatch):
    monkeypatch.setattr(deps, "_pip_install", lambda *a, **k: (_ for _ in ()).throw(AssertionError("não pode instalar")))
    assert deps.have("json") is True          # stdlib sempre importa
    st = deps.status()                        # passivo: não toca pip
    assert set(st) == {"PIL", "pyttsx3", "numpy"}


def test_ensure_modulo_falso_falha_gracioso(monkeypatch):
    monkeypatch.setattr(deps, "_pip_install", lambda pkg, timeout=60: (False, "simulada"))
    assert deps.ensure("modulo_xyz_inexistente", "pacote-xyz-inexistente") is False
    log = deps.deps_log()                     # falha agora é auditável, não silenciosa
    assert log and log[-1]["package"] == "pacote-xyz-inexistente"
    assert log[-1]["ok"] is False and "simulada" in log[-1]["reason"]


def test_ensure_baixa_dependencia_quebrada(monkeypatch):
    """Dependência ausente/quebrada → Joe tenta baixar e passa a funcionar."""
    chamado = {}

    def fake_pip(pkg, timeout=60):
        chamado["pkg"] = pkg
        return True, "instalado"

    monkeypatch.setattr(deps, "_pip_install", fake_pip)
    monkeypatch.setattr(deps.importlib, "import_module",
                        lambda name: None if name == "modulo_alvo" and chamado else (_ for _ in ()).throw(ImportError()))
    # primeira chamada falha o import -> dispara install -> segunda 'importa'
    ok = deps.ensure("modulo_alvo", "pacote-alvo")
    assert chamado["pkg"] == "pacote-alvo" and ok is True
    log = deps.deps_log()
    assert log[-1] == {"module": "modulo_alvo", "package": "pacote-alvo",
                       "ok": True, "reason": "instalado e importa"}


def test_ensure_invalida_cache_apos_instalacao(monkeypatch):
    """Fluxo completo da auto-cura de dependência: 1º import falha → pip →
    invalida caches (invalidate_caches + path_importer_cache.clear) → 2º
    import succeeds. Se a ordem dos caches quebrar, o módulo novo nunca
    apareceria na sessão — este teste trava esse contrato."""
    calls = []

    class SpyDict(dict):           # dict tem .clear read-only → usamos substituto
        def clear(self):
            calls.append("path")

    state = {"n": 0}

    def fake_imp(name):
        state["n"] += 1
        calls.append(f"imp{state['n']}")
        # simula site-packages novo: só 'existe' depois do install
        if name == "mod_novo" and state["n"] > 1:
            return None
        raise ImportError(name)

    def fake_pip(pkg, timeout=60):
        calls.append("pip")
        return True, "ok"

    monkeypatch.setattr(deps, "_pip_install", fake_pip)
    # IMPORTANTE: deps.py usa `import importlib` → o atributo real é deps.importlib
    il = deps.importlib
    monkeypatch.setattr(il, "invalidate_caches", lambda: calls.append("inv"))
    monkeypatch.setattr(deps.sys, "path_importer_cache", SpyDict())
    monkeypatch.setattr(il, "import_module", fake_imp)
    assert deps.ensure("mod_novo", "pac-novo") is True
    # ordem exata: tentativa → instalação → INVALIDAÇÃO DOS DOIS CACHES → re-import
    assert calls == ["imp1", "pip", "inv", "path", "imp2"]


def test_cache_evita_repetir_install(monkeypatch):
    n = {"c": 0}

    def fake_pip(pkg, timeout=60):
        n["c"] += 1
        return False, "sem rede"

    monkeypatch.setattr(deps, "_pip_install", fake_pip)
    deps.ensure("nao_existe_a", "pac-a")
    deps.ensure("nao_existe_a", "pac-a")   # segunda vez: cache, sem novo pip
    assert n["c"] == 1


def test_capacidades_usam_auto_download(monkeypatch):
    """SpeechCapability usa deps.ensure para oferecer pyttsx3 (baixando se preciso)."""
    from joe import capabilities
    from joe.capabilities import SpeechCapability
    called = {}
    def fake_ensure(module, package=None, *, allow_install=True):
        called["m"] = module
        return True
    class NoBins:
        @staticmethod
        def which(b): return None
    monkeypatch.setattr(capabilities, "shutil", NoBins())
    import joe.deps as d2
    monkeypatch.setattr(d2, "ensure", fake_ensure)
    assert SpeechCapability()._engine() == "pyttsx3" and called["m"] == "pyttsx3"
    # sem nenhum motor e sem conseguir baixar -> texto puro, nunca explode
    monkeypatch.setattr(d2, "ensure", lambda *a, **k: False)
    assert SpeechCapability()._engine() == ""


def test_health_reporta_dependencias_e_fallbacks():
    from joe.tools.base import bootstrap_tools
    from joe.tools.base import registry
    bootstrap_tools()   # registra health_check etc.
    r = registry.execute("health_check", {})
    out = r.output
    assert "Dependências opcionais" in out
    assert "fallback" in out or "ok" in out
    assert "Binários externos" in out
