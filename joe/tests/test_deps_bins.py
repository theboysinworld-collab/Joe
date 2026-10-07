"""Testes da camada ensure_bin (auto-baixa de binários) e integração com capacidades."""
import joe.deps as deps


def test_ensure_bin_presente_nao_instala(monkeypatch):
    monkeypatch.setattr(deps.shutil, "which", lambda n: "/usr/bin/python3" if n == "python3" else None)
    called = []
    monkeypatch.setattr(deps.subprocess, "run", lambda *a, **k: called.append(a))
    ok, d = deps.ensure_bin("python3")
    assert ok and not called  # já existe → não roda gerenciador


def test_ensure_bin_sem_mapa_gracioso():
    ok, d = deps.ensure_bin("binario-fantasma")
    assert not ok and "mapeamento" in d


def test_ensure_bin_apt_falha_nao_lanca(monkeypatch):
    # apt disponível mas instalação "falha" → retorna False com detalhe, sem exceção
    monkeypatch.setattr(deps.shutil, "which",
                        lambda n: "/usr/bin/apt-get" if n == "apt-get" else None)

    class R:
        returncode, stdout, stderr = 100, "", "Unable to locate package"
    monkeypatch.setattr(deps.subprocess, "run", lambda *a, **k: R())
    ok, d = deps.ensure_bin("espeak-ng")
    assert not ok and "apt" in d and "Unable" in d


def test_phone_fora_do_android_mensagem_honesta(monkeypatch):
    from joe.capabilities import PhoneCapability
    monkeypatch.delenv("PREFIX", raising=False)
    p = PhoneCapability()
    out = p.perceive(action="notificar", message="oi")
    assert "Termux" in out  # fora do celular: explica, não crasha


def test_phone_no_termux_tenta_instalar(monkeypatch):
    from joe.capabilities import PhoneCapability
    monkeypatch.setenv("PREFIX", "/data/data/com.termux/files/usr")
    calls = []
    monkeypatch.setattr(deps, "ensure_bin",
                        lambda name, timeout=180: (calls.append(name) or (False, f"{name}: nenhum gerenciador")))
    p = PhoneCapability()
    monkeypatch.setattr(p.__class__.__mro__[0], "__dict__", p.__class__.__dict__, raising=False) if False else None
    out = p.perceive(action="status")
    assert calls == ["termux-api"] and "falha ao baixar" in out
