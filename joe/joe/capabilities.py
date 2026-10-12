"""Capacidades sensoriais do Joe — interface única, implementação em camadas.

Contrato (Capability): name(), available() -> bool, perceive(**kw) -> str.
Extensões podem registrar novas capacidades no capability_registry sem tocar no núcleo.
"""

from __future__ import annotations

import html as _html
import importlib.util
import os
import platform
import re as _re
import shutil
import subprocess
import sys
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


class SpeechCapability(Capability):
    name = "falar"

    def __init__(self, language: str = "pt-BR") -> None:
        self.language = language or "pt-BR"

    def _piper_voice_path(self) -> Path:
        configured = os.environ.get("JOE_PIPER_VOICE")
        if configured:
            return Path(configured)
        return Path.home() / ".local" / "share" / "joe" / "voices" / "pt_BR-faber-medium.onnx"

    def _engine(self, *, allow_install: bool = True) -> str:
        from .deps import ensure, ensure_bin, prepare_runtime_paths

        prepare_runtime_paths()
        if shutil.which("say"):
            return "say"
        if sys.version_info < (3, 15):
            voice_path = self._piper_voice_path()
            if not voice_path.is_file() and allow_install:
                ensure("piper", "piper-tts>=1.8.0; python_version < '3.15'", allow_install=True)
                voice_path.parent.mkdir(parents=True, exist_ok=True)
                subprocess.run(
                    [
                        sys.executable,
                        "-m",
                        "piper.download_voices",
                        "pt_BR-faber-medium",
                        "--data-dir",
                        str(voice_path.parent),
                    ],
                    capture_output=True,
                    text=True,
                    timeout=180,
                    check=False,
                )
            piper_ready = voice_path.is_file() and importlib.util.find_spec("piper") is not None
            if piper_ready and ensure(
                "piper", "piper-tts>=1.8.0; python_version < '3.15'", allow_install=allow_install
            ):
                return "piper"
        for engine in ("espeak-ng", "espeak"):
            if shutil.which(engine):
                return engine
        if allow_install:
            for binary in ("espeak-ng", "espeak"):
                ok, _ = ensure_bin(binary, timeout=120)
                if ok and shutil.which(binary):
                    return binary
        if ensure("pyttsx3", allow_install=allow_install):
            try:
                import pyttsx3

                engine = pyttsx3.init()
                engine.stop()
                return "pyttsx3"
            except Exception:
                pass
        return ""

    def available(self) -> bool:
        return bool(self._engine(allow_install=False))

    def _voice_ok(self, engine: str, _retry: bool = False) -> bool:
        if engine not in ("espeak", "espeak-ng"):
            return True
        import glob

        data_path = os.environ.get("ESPEAK_DATA_PATH", "")
        patterns = ["/usr/share/espeak*/voices/*pt*", "/usr/lib*/espeak*/voices/*pt*"]
        if data_path:
            patterns.append(str(Path(data_path) / "voices" / "pt*"))
        if any(glob.glob(pattern) for pattern in patterns):
            return True
        if _retry:
            return False
        try:
            with urllib.request.urlopen("https://pypi.org", timeout=3):
                pass
        except Exception:
            return False

        for installer in ("apt-get", "dnf", "pacman"):
            if not shutil.which(installer):
                continue
            command = {
                "apt-get": ["apt-get", "install", "-y", "espeak-ng-data"],
                "dnf": ["dnf", "install", "-y", "espeak-ng"],
                "pacman": ["pacman", "-S", "--noconfirm", "--needed", "espeak-ng"],
            }[installer]
            if shutil.which("sudo"):
                command = ["sudo", "-n", *command]
            try:
                result = subprocess.run(command, capture_output=True, text=True, timeout=120, check=False)
                if result.returncode == 0 and self._voice_ok(engine, _retry=True):
                    return True
            except Exception:
                pass
        return False

    def _try_play_wav(self, path: Path) -> bool:
        player = shutil.which("paplay") or shutil.which("aplay") or shutil.which("ffplay")
        if not player:
            return False
        if Path(player).name == "ffplay":
            command = [player, "-nodisp", "-autoexit", "-loglevel", "error", str(path)]
        else:
            command = [player, str(path)]
        try:
            result = subprocess.run(command, capture_output=True, text=True, timeout=15, check=False)
            return result.returncode == 0
        except Exception:
            return False

    def _synthesize_piper(self, text: str, out_path: str = "") -> str:
        import wave
        from piper import PiperVoice

        voice = PiperVoice.load(str(self._piper_voice_path()))
        path = Path(out_path or (Path(tempfile.gettempdir()) / "joe_speech.wav"))
        path.parent.mkdir(parents=True, exist_ok=True)
        with wave.open(str(path), "wb") as wav_file:
            voice.synthesize_wav(text, wav_file)

        if self._try_play_wav(path):
            return f"falei (via Piper, {self.language}): {text[:80]}"
        return f"áudio sintetizado (Piper, {self.language}); reprodução indisponível, WAV salvo em {path}"

    def perceive(self, text: str = "", out_path: str = "", **_) -> str:
        engine = self._engine()
        if not engine:
            return f"[sem motor de voz — texto para fala]: {text}"

        if engine == "piper":
            try:
                return self._synthesize_piper(text, out_path)
            except Exception as exc:
                fallback = shutil.which("espeak-ng") or shutil.which("espeak")
                if not fallback:
                    return f"[Piper falhou — texto para fala]: {text} ({exc})"
                engine = Path(fallback).name

        if engine == "say":
            language = "Brazilian Portuguese" if self.language.startswith("pt") else self.language
            result = subprocess.run(["say", "-v", language, text], capture_output=True, text=True, check=False)
            if result is None or result.returncode != 0:
                subprocess.run(["say", text], check=False)
        elif engine in ("espeak", "espeak-ng"):
            if not self._voice_ok(engine):
                return f"[voz pt indisponível; fallback textual]: {text}"
            result = subprocess.run([engine, "-v", "pt-br", text], capture_output=True, text=True, check=False)
            if result is None or result.returncode != 0:
                audio_path = Path(out_path or (Path(tempfile.gettempdir()) / "joe_speech.wav"))
                wave_result = subprocess.run(
                    [engine, "-w", str(audio_path), "-v", "pt-br", text],
                    capture_output=True,
                    text=True,
                    check=False,
                )
                if wave_result is not None and wave_result.returncode == 0 and audio_path.is_file():
                    if self._try_play_wav(audio_path):
                        return f"falei (via {engine}, {self.language}): {text[:80]}"
                    return f"áudio sintetizado (via {engine}); reprodução indisponível, WAV salvo em {audio_path}"
                return f"[voz indisponível — texto para fala]: {text}"
        else:
            try:
                import pyttsx3

                speech_engine = pyttsx3.init()
                if self.language.startswith("pt"):
                    for voice in speech_engine.getProperty("voices").values():
                        if "pt" in (voice.id or "").lower() or "brazil" in (getattr(voice, "name", "") or "").lower():
                            speech_engine.setProperty("voice", voice.id)
                            break
                speech_engine.say(text)
                speech_engine.runAndWait()
            except Exception as exc:
                return f"[sem motor de voz — texto para fala]: {text} ({exc})"

        return f"falei (via {engine}, {self.language}): {text[:80]}"


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
        return out.stdout.strip() or "[nada reconhecido]"


class ScreenCapability(Capability):
    name = "ver_tela"

    def available(self) -> bool:
        system = platform.system()
        if system == "Darwin":
            return bool(shutil.which("screencapture"))
        if system == "Windows":
            return any(shutil.which(shell) for shell in ("powershell", "pwsh"))
        return any(shutil.which(tool) for tool in ("scrot", "grim", "gnome-screenshot"))

    def perceive(self, out_path: str = "", **_) -> str:
        path = Path(out_path or (Path(tempfile.gettempdir()) / "joe_screen.png"))
        system = platform.system()
        cmd = None
        if system == "Darwin" and shutil.which("screencapture"):
            cmd = ["screencapture", "-x", str(path)]
        elif system == "Linux":
            if shutil.which("scrot"):
                cmd = ["scrot", str(path)]
            elif shutil.which("grim"):
                cmd = ["grim", str(path)]
            elif shutil.which("gnome-screenshot"):
                cmd = ["gnome-screenshot", "-f", str(path)]
        elif system == "Windows":
            shell = shutil.which("powershell") or shutil.which("pwsh")
            if shell:
                escaped = str(path).replace("'", "''")
                script = (
                    "Add-Type -AssemblyName System.Windows.Forms; "
                    "Add-Type -AssemblyName System.Drawing; "
                    "$r=[Windows.Forms.Screen]::PrimaryScreen.Bounds; "
                    "$b=New-Object Drawing.Bitmap $r.Width,$r.Height; "
                    "$g=[Drawing.Graphics]::FromImage($b); "
                    "$g.CopyFromScreen($r.Location,[Drawing.Point]::Empty,$r.Size); "
                    f"$b.Save('{escaped}',[Drawing.Imaging.ImageFormat]::Png); "
                    "$g.Dispose(); $b.Dispose()"
                )
                cmd = [shell, "-NoProfile", "-Command", script]
        if cmd is None:
            return "captura de tela indisponível (sem scrot/grim/screencapture)."
        subprocess.run(cmd, check=False)
        if path.exists():
            return f"screenshot salvo em {path} (pronto p/ análise de visão se o modelo suportar imagens)"
        return "falha ao capturar tela"


class CameraCapability(Capability):
    name = "ver_camera"

    def available(self) -> bool:
        if shutil.which("termux-camera-photo"):
            return True
        if platform.system() != "Linux":
            return False
        has_device = any(Path("/dev").glob("video*"))
        has_backend = any(shutil.which(tool) for tool in ("ffmpeg", "fswebcam"))
        return has_device and has_backend

    def perceive(self, out_path: str = "", device: str = "", **_) -> str:
        path = Path(out_path or (Path(tempfile.gettempdir()) / "joe_cam.jpg"))
        if not device and not shutil.which("termux-camera-photo") and not any(Path("/dev").glob("video*")):
            return "câmera indisponível: nenhum dispositivo está visível neste host."
        if not any(shutil.which(tool) for tool in ("termux-camera-photo", "fswebcam", "ffmpeg")):
            from .deps import ensure_bin

            if "com.termux" in os.environ.get("PREFIX", ""):
                ensure_bin("termux-api", timeout=180)
            else:
                ensure_bin("ffmpeg", timeout=180)
        if shutil.which("termux-camera-photo"):
            subprocess.run(["termux-camera-photo", str(path)], check=False)
        elif shutil.which("fswebcam"):
            subprocess.run(["fswebcam", "-r", "1280x720", "--no-banner", str(path)], check=False)
        elif shutil.which("ffmpeg"):
            inp = device or ("/dev/video0" if Path("/dev/video0").exists() else "0")
            subprocess.run(
                ["ffmpeg", "-y", "-f", "v4l2", "-i", inp, "-frames:v", "1", str(path)],
                capture_output=True,
                check=False,
            )
        else:
            return "câmera indisponível."
        return f"frame da câmera salvo em {path}" if path.exists() else "falha ao capturar câmera"


class PhoneCapability(Capability):
    name = "celular"

    def available(self) -> bool:
        return shutil.which("termux-notification") is not None

    def _ensure_api(self) -> str:
        if self.available():
            return ""
        if "com.termux" not in os.environ.get("PREFIX", ""):
            return "API do celular indisponível (rode o Joe no Termux/Android para notificações/app/mensagens)."
        from .deps import ensure_bin

        ok, detail = ensure_bin("termux-api", timeout=180)
        return (
            "termux-api instalada — conceda as permissões do app Termux e tente de novo."
            if ok
            else f"falha ao baixar termux-api: {detail}"
        )

    def perceive(self, action: str = "status", title: str = "Joe", message: str = "",
                 app: str = "", phone: str = "", text: str = "", **_) -> str:
        if not self.available():
            return self._ensure_api()

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


class ImageGenCapability(Capability):
    name = "criar_imagem"

    def available(self) -> bool:
        from .deps import ensure

        return ensure("PIL", "pillow", allow_install=False)

    def perceive(self, prompt: str = "", out_path: str = "", width: int = 512, height: int = 512, **_) -> str:
        from .deps import ensure

        if not ensure("PIL", "pillow", allow_install=False):
            return (
                "Pillow indisponível (não consegui instalar sob demanda) — "
                f"fallback determinístico sem imagem: '{prompt[:160]}'"
            )
        try:
            from PIL import Image, ImageDraw
        except ImportError:
            return "Pillow falhou ao importar depois da instalação; fallback textual: " + prompt[:160]

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
        return f"imagem gerada (arte procedural offline a partir do prompt) → {path}."


class VideoEditCapability(Capability):
    name = "editar_video"

    def available(self) -> bool:
        return shutil.which("ffmpeg") is not None

    def perceive(self, action: str = "trim", src: str = "", dst: str = "",
                 start: str = "0", duration: str = "10", **_) -> str:
        if not self.available():
            from .deps import ensure_bin

            _, detail = ensure_bin("ffmpeg", timeout=180)
            if not self.available():
                return f"ffmpeg ausente — instalação sob demanda falhou: {detail}"
        ops = {
            "trim": ["-ss", start, "-t", duration],
            "thumbnail": ["-ss", start, "-frames:v", "1"],
            "convert": [],
        }
        if action not in ops or not src or not dst:
            return "uso: editar_video(action=trim|thumbnail|convert, src, dst, start, duration)"
        cmd = ["ffmpeg", "-y", "-i", src, *ops[action], dst]
        r = subprocess.run(cmd, capture_output=True, text=True, check=False)
        return f"{action} ok → {dst}" if r.returncode == 0 else f"ffmpeg falhou: {r.stderr[-300:]}"


class WebSearchCapability(Capability):
    name = "pesquisar_web"

    def available(self) -> bool:
        return True

    def perceive(self, query: str = "", max_results: int = 6, **_) -> str:
        if not query:
            return "informe a busca."
        import html as _html
        import re

        ua = {"User-Agent": "Mozilla/5.0 (X11; Linux x86_64; rv:109.0) Gecko/20100101 Firefox/115.0"}
        attempts = [
            ("https://lite.duckduckgo.com/lite/", urllib.parse.urlencode({"q": query}).encode()),
            ("https://html.duckduckgo.com/html/?" + urllib.parse.urlencode({"q": query}), None),
        ]
        last_err = None
        for url, data in attempts:
            try:
                page = urllib.request.urlopen(
                    urllib.request.Request(url, data=data, headers=ua), timeout=15
                ).read().decode(errors="replace")
            except Exception as exc:
                last_err = exc
                continue
            results = self._extract(page, re, _html, max_results)
            if results:
                return "\n".join(results)
        return f"sem acesso à internet ou layout inesperado ({last_err}); use /rag para buscar no que já sei."

    @staticmethod
    def _extract(page: str, re_mod, html_mod, max_results: int) -> list[str]:
        out: list[str] = []
        for m in re_mod.finditer(r'<a[^>]+href="(https?://[^"]+)"[^>]*>([^<]{10,140})</a>', page):
            href, title = m.group(1), m.group(2)
            if "duckduckgo-help-pages" in href or "/yad.js" in href or "duck.co" in href:
                continue
            title = html_mod.unescape(title.strip())
            if any(r.split("\n")[0].find(title) != -1 for r in out):
                continue
            out.append(f"- {title}\n  {href}")
            if len(out) >= max_results:
                break
        return out


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
    capability_registry._caps.clear()
    for cap in (
        SpeechCapability(language=getattr(config, "language", "pt-BR")),
        HearingCapability(),
        ScreenCapability(),
        CameraCapability(),
        PhoneCapability(),
        ImageGenCapability(),
        VideoEditCapability(),
        WebSearchCapability(),
    ):
        capability_registry.register(cap)
    return capability_registry


__all__ = [
    "Capability",
    "SpeechCapability",
    "HearingCapability",
    "ScreenCapability",
    "CameraCapability",
    "PhoneCapability",
    "ImageGenCapability",
    "VideoEditCapability",
    "WebSearchCapability",
    "CapabilityRegistry",
    "capability_registry",
    "bootstrap_capabilities",
]
