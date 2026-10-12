"""Ferramentas de visão/vetoriais: OCR offline, análise de imagem por LLM, busca vetorial.

Camadas (sempre com fallback):
- OCR: tesseract CLI → pytesseract → aviso claro (nunca quebra).
- Analisar imagem: se o provedor ativo for multimodal, envia base64 via extensão do
  provider; senão devolve instrução clara do que falta.
- Busca vetorial pura na memória (exposta como tool p/ o LLM usar direto).
"""

from __future__ import annotations

import base64
import shutil
import subprocess
from pathlib import Path

from .base import Tool, ToolResult, registry


class OcrTool(Tool):
    name = "ocr_image"
    description = "Extrai texto de uma imagem (offline, tesseract). Args: path."
    parameters = {"type": "object", "properties": {"path": {"type": "string"}}, "required": ["path"]}

    def run(self, path: str) -> ToolResult:
        if not Path(path).exists():
            return ToolResult(ok=False, output=f"não existe: {path}")
        from ..deps import ensure, ensure_bin
        if not shutil.which("tesseract"):
            ensure_bin("tesseract", timeout=180)
        if shutil.which("tesseract"):
            r = subprocess.run(["tesseract", path, "-"], capture_output=True, text=True, check=False)
            if r.returncode == 0 and r.stdout.strip():
                return ToolResult(output=r.stdout.strip()[:4000])
        try:
            if not ensure("pytesseract", "pytesseract") or not ensure("PIL", "pillow"):
                raise ImportError("dependências Python de OCR indisponíveis")
            import pytesseract
            from PIL import Image
            return ToolResult(output=pytesseract.image_to_string(Image.open(path))[:4000])
        except Exception:
            return ToolResult(ok=False, output="OCR indisponível (instale tesseract-ocr); "
                              "a imagem ainda pode ser analisada por um modelo de visão.")


class AnalyzeImageTool(Tool):
    name = "analyze_image"
    description = ("Descreve/analisa uma imagem (screenshot ou foto da câmera) usando o modelo de "
                   "visão ativo. Args: path, question?")
    parameters = {"type": "object", "properties": {
        "path": {"type": "string"}, "question": {"type": "string"}}, "required": ["path"]}

    def run(self, path: str, question: str = "Descreva o que está nesta imagem em detalhes.") -> ToolResult:
        p = Path(path)
        if not p.exists():
            return ToolResult(ok=False, output=f"não existe: {path}")
        from ..llm import provider_registry
        prov = provider_registry.active()
        if prov is None or prov.id == "ollama" and not _vision_model(prov):
            # tenta OCR local antes de desistir — a base continua útil sem visão
            ocr = registry.execute("ocr_image", {"path": path})
            if ocr.ok:
                return ToolResult(output=f"[visão LLM indisponível; usei OCR local]\n{ocr.output}")
            return ToolResult(ok=False, output=(
                "nenhum modelo de visão disponível. Rode `joe bench` para ver candidatos; "
                "modelos que servem: llava, minicpm-v (ollama), gpt-4o, claude (openrouter)."))
        b64 = base64.b64encode(p.read_bytes()).decode()
        msg = [{"role": "user", "content": [
            {"type": "text", "text": question},
            {"type": "image_url", "image_url": {"url": f"data:image/png;base64,{b64}"}}]}]
        try:
            out = prov.chat(msg, None)
            return ToolResult(output=out.get("content") or "(modelo não retornou texto)")
        except Exception as e:
            return ToolResult(ok=False, output=f"falha na análise de visão: {e}")


def _vision_model(prov) -> bool:
    try:
        models = " ".join(prov.list_models()).lower()
        return any(v in models for v in ("llava", "minicpm", "vision", "moondream"))
    except Exception:
        return False


class VectorSearchTool(Tool):
    name = "vector_search"
    description = "Busca por similaridade de cosseno no índice vetorial da memória. Args: query, k?"
    parameters = {"type": "object", "properties": {
        "query": {"type": "string"}, "k": {"type": "integer"}}, "required": ["query"]}

    def run(self, query: str, k: int = 8) -> ToolResult:
        from ..memory import Memory
        hits = Memory().search(query, k=k)
        return ToolResult(output="\n".join(
            f"{h['score']:.3f} [{h['kind']}:{h['tag']}] {h['text'][:220]}" for h in hits)
            or "(índice vazio — use index_for_rag em arquivos relevantes)")


for t in (OcrTool(), AnalyzeImageTool(), VectorSearchTool()):
    registry.register(t)
