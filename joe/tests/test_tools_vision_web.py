"""Testes das ferramentas de visão e web com dependências externas mockadas.

Cobrem os caminhos offline: OCR sem tesseract, analyze_image sem modelo de visão
(cascata p/ OCR), vector_search vazio, search_web sem rede e use_capability.
"""

import json

from joe.tools import vision as vision_mod
from joe.tools import web as web_mod
from joe.tools.base import registry
from joe.bus import EventBus


def test_ocr_arquivo_inexistente():
    tool = vision_mod.OcrTool()
    res = tool.run("/caminho/que/nao/existe.png")
    assert not res.ok and "não existe" in res.output


def test_analyze_image_sem_visao_usa_fallback(monkeypatch, tmp_path):
    img = tmp_path / "tela.png"
    img.write_bytes(b"\x89PNG fake")

    class FakeProv:
        id = "ollama"
        def list_models(self):
            return ["qwen2.5-coder:1.5b"]  # sem modelos de visão

    monkeypatch.setattr(vision_mod, "_vision_model", lambda p: False)
    from joe import deps
    monkeypatch.setattr(deps, "ensure_bin", lambda *a, **k: (False, "sem binário"))
    from joe.llm import provider_registry
    monkeypatch.setattr(provider_registry, "active", lambda: FakeProv())
    # fallback: tenta OCR local; sem tesseract no container → aviso claro, nunca quebra
    ocr_res = vision_mod.registry.execute("ocr_image", {"path": str(img)})
    res = vision_mod.AnalyzeImageTool().run(str(img))
    assert (res.ok and "OCR local" in res.output) or (not res.ok and "visão" in res.output.lower())
    assert isinstance(ocr_res.output, str)


def test_vector_search_indice_vazio(monkeypatch, tmp_path):
    class FakeMem:
        def __init__(self):
            pass
        def search(self, q, k=8):
            return []
    monkeypatch.setattr(vision_mod, "Memory", FakeMem, raising=False)
    # a tool importa Memory de ..memory dentro de run(); mockamos por sys.modules
    import sys, types
    fake_pkg = types.ModuleType("joe.memory")
    fake_pkg.Memory = FakeMem
    old = sys.modules.get("joe.memory")
    sys.modules["joe.memory"] = fake_pkg
    try:
        res = vision_mod.VectorSearchTool().run("nada")
        assert "índice vazio" in res.output
    finally:
        if old is not None:
            sys.modules["joe.memory"] = old


def test_use_capability_desconhecida():
    res = web_mod.UseCapabilityTool().run("capacidade_que_nao_existe")
    assert not res.ok and "desconhecida" in res.output


def test_capabilities_status_executa():
    res = web_mod.CapabilitiesStatusTool().run()
    assert res.ok and isinstance(res.output, str)


def test_search_web_sem_rede_nao_quebra(monkeypatch):
    class FakeCap:
        def perceive(self, query="", max_results=6):
            return "sem acesso à internet neste momento (modo offline)"
    monkeypatch.setattr(web_mod, "_caps", lambda: type("R", (), {"get": staticmethod(lambda n: FakeCap())})())
    res = web_mod.SearchWebTool().run("python 3.13 novidades")
    assert not res.ok and "sem acesso" in res.output


# ---- parte vision-2: caminhos com modelo de visão fake (mock do provider) ----

def test_analyze_image_usa_provider_multimodal_fake(tmp_path, monkeypatch):
    import joe.llm as llm_mod
    img = tmp_path / "tela.png"
    img.write_bytes(b"\x89PNG fake")

    class FakeVisionProvider:
        id = "ollama"
        def list_models(self): return ["llava:13b"]
        def chat(self, msg, tools): return {"content": "vejo um terminal aberto"}

    monkeypatch.setattr(llm_mod.provider_registry, "active", lambda: FakeVisionProvider())
    r = registry.execute("analyze_image", {"path": str(img)})
    assert r.ok and "terminal aberto" in r.output


def test_analyze_image_sem_provedor_cai_para_ocr_indisponivel(tmp_path, monkeypatch):
    # sem tesseract/pytesseract no ambiente → cascata termina em aviso claro, nunca exceção
    img = tmp_path / "x.png"; img.write_bytes(b"x")
    from joe import deps
    monkeypatch.setattr(deps, "ensure_bin", lambda *a, **k: (False, "sem binário"))
    monkeypatch.setattr(deps, "ensure", lambda *a, **k: False)
    r = registry.execute("analyze_image", {"path": str(img)})
    assert not r.ok  # aviso explicando OCR/visão indisponíveis
    assert "OCR" in r.output or "visão" in r.output or "llava" in r.output


def test_vision_model_helper_true_false():
    from joe.tools.vision import _vision_model
    class P:
        def __init__(self, m): self.m = m
        def list_models(self): return self.m
    assert _vision_model(P(["llava:7b"])) is True
    assert _vision_model(P(["qwen2.5-coder:1.5b"])) is False
    class Boom:
        def list_models(self): raise RuntimeError("sem conexão")
    assert _vision_model(Boom()) is False  # nunca propaga exceção


def test_ocr_tesseract_presente_monkeypatched(tmp_path, monkeypatch):
    import joe.tools.vision as V
    img = tmp_path / "a.png"; img.write_bytes(b"x")
    monkeypatch.setattr(V.shutil, "which", lambda n: "/usr/bin/tesseract")
    class R: returncode = 0; stdout = "texto extraído\n"
    monkeypatch.setattr(V.subprocess, "run", lambda *a, **k: R())
    r = registry.execute("ocr_image", {"path": str(img)})
    assert r.ok and r.output == "texto extraído"


def test_ocr_tesseract_falha_e_pytesseract_indisponivel(tmp_path, monkeypatch):
    import joe.tools.vision as V
    img = tmp_path / "a.png"; img.write_bytes(b"x")
    monkeypatch.setattr(V.shutil, "which", lambda n: None)  # força caminho pytesseract
    from joe import deps
    monkeypatch.setattr(deps, "ensure_bin", lambda *a, **k: (False, "sem binário"))
    monkeypatch.setattr(deps, "ensure", lambda *a, **k: False)
    import sys
    sys.modules.pop("pytesseract", None)
    r = registry.execute("ocr_image", {"path": str(img)})
    assert not r.ok and "tesseract" in r.output.lower()
