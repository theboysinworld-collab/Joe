"""Ferramentas do mundo: web (DuckDuckGo), capacidades sensoriais e celular.

Todas delegam para joe/capabilities.py — aqui só o adaptador Tool↔Capability.
Adicionar um novo sentido = criar a Capability; nenhuma Tool extra é necessária
(poema: `use_capability` já expõe todas genericamente).
"""

from __future__ import annotations

import json

from .base import Tool, ToolResult, registry


def _caps():
    from ..capabilities import capability_registry, bootstrap_capabilities
    from ..config import Config
    if not capability_registry.all():
        bootstrap_capabilities(Config.load())
    return capability_registry


class SearchWebTool(Tool):
    name = "search_web"
    description = "Pesquisa na internet via DuckDuckGo (sem chave/API) e traz resultados filtrados. Args: query, max_results?"
    parameters = {"type": "object", "properties": {
        "query": {"type": "string"}, "max_results": {"type": "integer"}}, "required": ["query"]}

    def run(self, query: str, max_results: int = 6) -> ToolResult:
        cap = _caps().get("pesquisar_web")
        out = cap.perceive(query=query, max_results=max_results)
        return ToolResult(ok=not out.startswith("sem acesso"), output=out)


class UseCapabilityTool(Tool):
    name = "use_capability"
    description = ("Aciona uma capacidade sensorial por nome: falar, ouvir, ver_tela, ver_camera, "
                   "celular, criar_imagem, editar_video, pesquisar_web. Args: capability, kwargs (JSON string).")
    parameters = {"type": "object", "properties": {
        "capability": {"type": "string"}, "kwargs": {"type": "string"}},
        "required": ["capability"]}

    def run(self, capability: str, kwargs: str = "{}") -> ToolResult:
        cap = _caps().get(capability)
        if cap is None:
            return ToolResult(ok=False, output=f"capacidade desconhecida: {capability}. "
                              f"Existentes: {', '.join(c.name for c in _caps().all())}")
        try:
            kw = json.loads(kwargs or "{}")
        except json.JSONDecodeError:
            kw = {}
        out = cap.perceive(**kw)
        return ToolResult(output=out)


class CapabilitiesStatusTool(Tool):
    name = "capabilities_status"
    description = "Mostra quais sentidos/ações estão disponíveis neste sistema (com fallbacks)."
    parameters = {"type": "object", "properties": {}}

    def run(self) -> ToolResult:
        rows = _caps().status()
        return ToolResult(output="\n".join(
            f"{'✅' if r['disponivel'] else '⚠️ '} {r['capacidade']}" for r in rows))


# atalhos específicos p/ o LLM usar direto (menos JSON aninhado = menos erro)
class SeeScreenTool(Tool):
    name = "see_screen"
    description = "Captura a tela atual (screenshot) para análise."
    parameters = {"type": "object", "properties": {"out_path": {"type": "string"}}}

    def run(self, out_path: str = "") -> ToolResult:
        return ToolResult(output=_caps().get("ver_tela").perceive(out_path=out_path))


class CameraTool(Tool):
    name = "camera_view"
    description = "Tira um frame da câmera para o Joe 'ver' o mundo."
    parameters = {"type": "object", "properties": {"out_path": {"type": "string"}}}

    def run(self, out_path: str = "") -> ToolResult:
        return ToolResult(output=_caps().get("ver_camera").perceive(out_path=out_path))


class SpeakTool(Tool):
    name = "speak"
    description = "Fala um texto em voz alta (TTS com fallbacks offline)."
    parameters = {"type": "object", "properties": {"text": {"type": "string"}}, "required": ["text"]}

    def run(self, text: str) -> ToolResult:
        return ToolResult(output=_caps().get("falar").perceive(text=text))


class PhoneTool(Tool):
    name = "phone"
    description = ("Ações no celular via Termux API: action=status|notificar|abrir_app|mandar_sms|"
                   "ler_notificacoes (+ title, message, app, phone, text).")
    parameters = {"type": "object", "properties": {
        "action": {"type": "string"}, "title": {"type": "string"}, "message": {"type": "string"},
        "app": {"type": "string"}, "phone": {"type": "string"}, "text": {"type": "string"}},
        "required": ["action"]}

    def run(self, action: str, **kw) -> ToolResult:
        return ToolResult(output=_caps().get("celular").perceive(action=action, **kw))


class MakeImageTool(Tool):
    name = "make_image"
    description = "Gera uma imagem a partir de um prompt (backend plugável; fallback procedural offline)."
    parameters = {"type": "object", "properties": {
        "prompt": {"type": "string"}, "out_path": {"type": "string"}}, "required": ["prompt"]}

    def run(self, prompt: str, out_path: str = "") -> ToolResult:
        return ToolResult(output=_caps().get("criar_imagem").perceive(prompt=prompt, out_path=out_path))


class EditVideoTool(Tool):
    name = "edit_video"
    description = "Edita vídeo com ffmpeg (offline): action=trim|thumbnail|convert, src, dst, start, duration."
    parameters = {"type": "object", "properties": {
        "action": {"type": "string"}, "src": {"type": "string"}, "dst": {"type": "string"},
        "start": {"type": "string"}, "duration": {"type": "string"}}, "required": ["action", "src", "dst"]}

    def run(self, action: str, src: str, dst: str, start: str = "0", duration: str = "10") -> ToolResult:
        return ToolResult(output=_caps().get("editar_video").perceive(
            action=action, src=src, dst=dst, start=start, duration=duration))


for t in (SearchWebTool(), UseCapabilityTool(), CapabilitiesStatusTool(), SeeScreenTool(),
          CameraTool(), SpeakTool(), PhoneTool(), MakeImageTool(), EditVideoTool()):
    registry.register(t)
