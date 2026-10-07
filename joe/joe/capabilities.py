"""Capacidades sensoriais do Joe — interface única, implementação em camadas.

Contrato (Capability): name(), available() -> bool, perceive(**kw) -> str.
Extensões podem registrar novas capacidades no capability_registry sem tocar no núcleo.

Camadas de cada capacidade (regra geral do projeto: SEMPRE tem fallback):
- Falar:   `say` CLI → espeak/espeak-ng → pyttsx3 → texto puro (sempre "funciona").
- Ouvir:   pyttsx? não: whisper via ollama? → `arecord`+whisper-cli → não disponível = avisa.
- Ver:     scrot/pymac/grim (screenshot) + visão por LLM multimodal se o modelo suportar.
- Câmera:  ffmpeg (1 frame) / fswebcam → imagem p/ análise.
- Celular: Termux API (comando `termux-*`) quando rodando no Android; fora disso reporta.
- Criar imagem: StableDiffusion local (ollama ext/forge) → Pillow (arte procedural offline).
- Editar vídeo: ffmpeg (corte, concat, thumbnails) — gratuito, offline.
- Internet: DuckDuckGo HTML (stdlib urllib) → resultados filtrados; offline = cache da memória.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import tempfile
import urllib.parse
import urllib.request
from pathlib import Path

from .config import Config


class Capability:
    name = ""

    def available(self) -> bool:
        return False

    def perceive(self, **kwargs) -> str:  # pragma: no cover - interface
        raise NotImplementedError


# ---------------------------------------------------------------- falar / ouvir
class SpeechCapability(Capability):
    name = "falar"

    def available(self) -> bool:
        return bool(self._engine())

    def _engine(self) -> str:
        if shutil.which("say"):
            return "say"
        for e in ("espeak", "espeak-ng"):
            if shutil.which(e):
                return e
        try:
            import pyttsx3  # noqa: F401
            return "pyttsx3"
        except ImportError:
            return ""

    def perceive(self, text: str = "", **_) -> str:
        engine = self._engine()
        if not engine:
            return f"[sem motor de voz — texto para fala]: {text}"
        if engine == "say":
            subprocess.run(["say", text], check=False)
        elif engine in ("espeak", "espeak-ng"):
            subprocess.run([engine, "-v", "pt-br", text], check=False)
        else:
            import pyttsx3
            pyttsx3.init().say(text).runAndWait()
        return f"falei (via {engine}): {text[:80]}"


class HearingCapability(Capability):
    name = "ouvir"

    def available(self) -> bool:
        return bool(shutil.which("arecord") and (shutil.which("whisper") or shutil.which("whisper-cli")))

    def perceive(self, seconds: int = 5, **_) -> str:
        if not self.available():
            return "gravação/whisper indisponíveis neste sistema (instale arecord + whisper.cpp)."
        wav = Path(tempfile.gettempdir()) / "joe_listen.wav"
        subprocess.run(["arecord", "-d", str(seconds), "-f", "cd", str(wav)], check=False)
        tool = shutil.which("whisper") or shutil.which("whisper-cli")
        out = subprocess.run([tool, str(wav)], capture_output=True, text=True, check=False)
        return (out.stdout.strip() or "[nada reconhecido]")


# ---------------------------------------------------------------- ver / câmera
class ScreenCapability(Capability):
    name = "ver_tela"

    def available(self) -> bool:
        return any(shutil.which(t) for t in ("scrot", "grim", "maimpick")) or \
            (os.uname().sysname == "Darwin" and shutil.which("screencapture")) or \
            bool(shutil.which("powershell"))

    def perceive(self, out_path: str = "", **_) -> str:
        path = Path(out_path or (Path(tempfile.gettempdir()) / "joe_screen.png"))
        sys = os.uname().sysname
        cmds = {
            "Darwin": ["screencapture", "-x", str(path)],
            "Linux": None,
        }
        if sys == "Linux":
            if shutil.which("scrot"):
                cmds["Linux"] = ["scrot", str(path)]
            elif shutil.which("grim"):
                cmds["Linux"] = ["grim", str(path)]
            elif shutil.which("gnome-screenshot"):
                cmds["Linux"] = ["gnome-screenshot", "-f", str(path)]
        cmd = cmds.get(sys)
        if cmd is None:
            return "captura de tela indisponível (sem scrot/grim/screencapture)."
        subprocess.run(cmd, check=False)
        if path.exists():
            return f"screenshot salvo em {path} (pronto p/ análise de visão se o modelo suportar imagens)"
        return "falha ao capturar tela"


class CameraCapability(Capability):
    name = "ver_camera"

    def available(self) -> bool:
        return bool(shutil.which("ffmpeg") or shutil.which("fswebcam")) or \
            shutil.which("termux-camera-photo") is not None

    def perceive(self, out_path: str = "", device: str = "", **_) -> str:
        path = Path(out_path or (Path(tempfile.gettempdir()) / "joe_cam.jpg"))
        if shutil.which("termux-camera-photo"):
            subprocess.run(["termux-camera-photo", str(path)], check=False)
        elif shutil.which("fswebcam"):
            subprocess.run(["fswebcam", "-r", "1280x720", "--no-banner", str(path)], check=False)
        elif shutil.which("ffmpeg"):
            inp = device or ("/dev/video0" if Path("/dev/video0").exists() else "0")
            subprocess.run(["ffmpeg", "-y", "-f", "v4l2", "-i", inp, "-frames:v", "1", str(path)],
                           capture_output=True, check=False)
        else:
            return "câmera indisponível."
        return f"frame da câmera salvo em {path}" if path.exists() else "falha ao capturar câmera"


# ---------------------------------------------------------------- celular (Termux)
class PhoneCapability(Capability):
    name = "celular"

    def available(self) -> bool:
        return shutil.which("termux-notification") is not None

    def perceive(self, action: str = "status", title: str = "Joe", message: str = "",
                 app: str = "", phone: str = "", text: str = "", **_) -> str:
        if not self.available():
            return "API do celular indisponível (rode o Joe no Termux/Android para notificações/app/mensagens)."
        run = lambda c: subprocess.run(c, capture_output=True, text=True, check=False)
        if action == "notificar":
            run(["termux-notification", "--title", title, "--content", message])
            return f"notificação enviada: {title} — {message}"
        if action == "abrir_app":
            r = run(["termux-open", app or text])
            return f"abri {app or text} (rc={r.returncode})"
        if action == "mandar_sms":
            r = run(["termux-sms-send", "-n", phone, text])
            return f"sms para {phone}: rc={r.returncode}"
        if action == "ler_notificacoes":
            r = run(["termux-notification-list"])
            return r.stdout[:2000] or "(sem notificações)"
        return f"ação desconhecida: {action}"


# ---------------------------------------------------------------- criar imagem / editar vídeo
class ImageGenCapability(Capability):
    name = "criar_imagem"

    def available(self) -> bool:
        try:
            import PIL  # noqa: F401
            return True
        except ImportError:
            return False

    def perceive(self, prompt: str = "", out_path: str = "", width: int = 512, height: int = 512, **_) -> str:
        # 1) tentativa: backend externo registrado por extensão (ex.: SD local)
        from .bus import bus
        custom = []
        bus.subscribe("cap.imagegen", lambda e: custom.append(e))
        # 2) fallback determinístico offline: arte generativa por seed do prompt
        try:
            from PIL import Image, ImageDraw
        except ImportError:
            return "Pillow ausente — instale `pillow` ou registre um backend via extensão."
        import hashlib
        seed = int(hashlib.md5(prompt.encode()).hexdigest(), 16)
        img = Image.new("RGB", (width, height), (20, 22, 30))
        d = ImageDraw.Draw(img)
        for i in range(60):
            x = (seed * (i + 7) * 1103515245) % width
            y = (seed * (i + 13) * 12345) % height
            r = 8 + (seed >> i) % 90
            col = ((seed >> (i * 3)) % 256, (seed >> (i * 5)) % 256, (seed >> (i * 7)) % 256)
            d.ellipse([x - r, y - r, x + r, y + r], outline=col, width=2)
        path = out_path or str(Path(tempfile.gettempdir()) / "joe_art.png")
        img.save(path)
        return (f"imagem gerada (arte procedural offline a partir do prompt) → {path}. "
                "Para diffusion real, adicione uma extensão com backend SD/A1111/ComfyUI.")


class VideoEditCapability(Capability):
    name = "editar_video"

    def available(self) -> bool:
        return shutil.which("ffmpeg") is not None

    def perceive(self, action: str = "trim", src: str = "", dst: str = "",
                 start: str = "0", duration: str = "10", **_) -> str:
        if not self.available():
            return "ffmpeg ausente — instale para edição de vídeo (offline, gratuito)."
        ops = {
            "trim": ["-ss", start, "-t", duration],
            "thumbnail": ["-ss", start, "-frames:v", "1"],
            "convert": [],
        }
        if action not in ops or not src or not dst:
            return f"uso: editar_video(action=trim|thumbnail|convert, src, dst, start, duration)"
        cmd = ["ffmpeg", "-y", "-i", src, *ops[action], dst]
        r = subprocess.run(cmd, capture_output=True, text=True, check=False)
        return f"{action} ok → {dst}" if r.returncode == 0 else f"ffmpeg falhou: {r.stderr[-300:]}"


# ---------------------------------------------------------------- internet
class WebSearchCapability(Capability):
    name = "pesquisar_web"

    def available(self) -> bool:
        return True  # usa stdlib; se não houver rede, devolve aviso claro

    def perceive(self, query: str = "", max_results: int = 6, **_) -> str:
        if not query:
            return "informe a busca."
        url = ("https://html.duckduckgo.com/html/?q=" + urllib.parse.quote_plus(query))
        req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0 joe-agent"})
        try:
            html = urllib.request.urlopen(req, timeout=15).read().decode(errors="replace")
        except Exception as e:
            return f"sem acesso à internet ({e}); use /rag para buscar no que já sei."
        import re
        results = []
        for m in re.finditer(r'<a[^>]+class="result__a"[^>]+href="([^"]+)"[^>]*>(.*?)</a>'
                             r'(?:.*?<a[^>]+class="result__snippet"[^>]*>(.*?)</a>)?',
                             html, re.S):
            href, title, snip = m.group(1), m.group(2), m.group(3) or ""
            clean = lambda s: re.sub(r"<[^>]+>", "", s).strip()
            if href.startswith("//duckduckgo.com/l/") or "/yad.js" in href:
                continue  # pula anúncios
            results.append(f"- {clean(title)}\n  {clean(snip)[:200]}\n  {href}")
            if len(results) >= max_results:
                break
        return "\n".join(results) or "nenhum resultado extraível (layout mudou?)"


# ---------------------------------------------------------------- registry
class CapabilityRegistry:
    def __init__(self) -> None:
        self._caps: dict[str, Capability] = {}

    def register(self, cap: Capability) -> None:
        self._caps[cap.name] = cap

    def get(self, name: str) -> Capability | None:
        return self._caps.get(name)

    def all(self) -> list[Capability]:
        return list(self._caps.values())

    def status(self) -> list[dict]:
        return [{"capacidade": c.name, "disponivel": c.available()} for c in self.all()]


capability_registry = CapabilityRegistry()


def bootstrap_capabilities(config: Config) -> CapabilityRegistry:
    for cap in (SpeechCapability(), HearingCapability(), ScreenCapability(),
                CameraCapability(), PhoneCapability(), ImageGenCapability(),
                VideoEditCapability(), WebSearchCapability()):
        capability_registry.register(cap)
    return capability_registry
