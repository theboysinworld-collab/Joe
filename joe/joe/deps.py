"""Camada de dependências opcionais do Joe.

Princípio: o núcleo roda 100% com a biblioteca padrão do Python.
Dependências extras (Pillow, pyttsx3…) só melhoram capacidades; quando
ausentes ou quebradas (ex.: instaladas para outra versão do Python →
import falha mesmo com `pip show` dizendo que existe), este módulo:

  1. detecta o problema real tentando importar de verdade;
  2. tenta baixar/instalar a dependência via pip (com cache de tentativa);
  3. se ainda assim falhar, cai no fallback determinístico da capacidade.

Nada aqui lança exceção para o chamador — sempre devolve bool.
"""
from __future__ import annotations

import importlib
import shutil
import subprocess
import sys
import time

# módulos já verificados nesta sessão: evita repetir tentativas caras
_checked: dict[str, bool] = {}
_installed: set[str] = set()


def _pip_install(pkg: str, timeout: int = 60) -> bool:
    """Tenta instalar `pkg` via pip do próprio interpretador. Silencioso e seguro."""
    try:
        r = subprocess.run(
            [sys.executable, "-m", "pip", "install", "--quiet", "--disable-pip-version-check", pkg],
            capture_output=True, text=True, timeout=timeout,
        )
        return r.returncode == 0
    except Exception:
        return False


def ensure(module: str, package: str | None = None, *, allow_install: bool = True) -> bool:
    """O módulo `module` importa? Se não, tenta instalar `package` (padrão: o próprio nome).

    Retorna True quando o módulo passa a importar neste interpretador.
    Cache por nome: uma vez OK, nunca re-verifica; uma vez instalado sem
    sucesso, não insiste mais de uma vez por sessão.
    """
    if module in _checked:
        return _checked[module]
    pkg = package or module
    ok = False
    try:
        importlib.import_module(module)
        ok = True
    except Exception:
        # ausente OU quebrada (deprecated/warning virou erro, ABI errada etc.)
        if allow_install and pkg not in _installed:
            _installed.add(pkg)
            if _pip_install(pkg):
                importlib.invalidate_caches()
                sys.path_importer_cache.clear()
                # pip pode ter instalado em site-packages novo nesta sessão →
                # é preciso recarregar o caminho de busca do interpretador
                try:
                    import site
                    for p in list(site.getsitepackages()) + [site.getusersitepackages()]:
                        if p not in sys.path:
                            sys.path.append(p)
                except Exception:
                    pass
                try:
                    importlib.import_module(module)
                    ok = True
                except Exception:
                    ok = False
    _checked[module] = ok
    return ok


def have(module: str, package: str | None = None) -> bool:
    """Igual a ensure(), mas nunca tenta instalar (para checagens passivas como /health)."""
    return ensure(module, package, allow_install=False)


def status() -> dict[str, bool]:
    """Retrato das dependências opcionais usadas pelo Joe (sem instalar nada)."""
    mods = {
        "PIL": "pillow",           # criar_imagem
        "pyttsx3": "pyttsx3",      # falar (fallback de motor)
        "numpy": "numpy",          # RAG vetorial acelerado (opcional)
    }
    out = {}
    for m, p in mods.items():
        try:
            importlib.import_module(m)
            out[m] = True
        except Exception:
            out[m] = False
    return out


# ------------------------------------------------------- binários de sistema (CLI)
_BIN_PKGS = {  # binário → pacotes por gerenciador (apt/dnf/pacman/brew/pkg[Termux/Android])
    "espeak-ng":   {"apt": "espeak-ng", "dnf": "espeak-ng", "pacman": "espeak-ng", "brew": "espeak-ng"},
    "espeak":      {"apt": "espeak", "dnf": "espeak", "pacman": "espeak", "brew": "espeak"},
    "ffmpeg":      {"apt": "ffmpeg", "dnf": "ffmpeg-free", "pacman": "ffmpeg", "brew": "ffmpeg"},
    "tesseract":   {"apt": "tesseract-ocr", "dnf": "tesseract", "pacman": "tesseract", "brew": "tesseract"},
    "scrot":       {"apt": "scrot", "dnf": "scrot", "pacman": "scrot", "brew": "scrot"},
    "whisper-cpp": {"apt": "whisper-cpp", "dnf": "whisper-cpp", "pacman": "whisper-cpp", "brew": "whisper-cpp"},
    "termux-api":  {"pkg": "termux-api"},  # Android/Termux: pkg install termux-api
}


def ensure_bin(name: str, timeout: int = 180) -> tuple[bool, str]:
    """Garante um binário de sistema; se ausente tenta baixar via gerenciador local.
    Em Termux/Android usa `pkg`; em desktop apt/dnf/pacman/brew conforme disponível.
    Retorna (ok, detalhe). Nunca lança exceção — o chamador cai no fallback."""
    try:
        if shutil.which(name):
            return True, f"{name}: já instalado"
        pkgs = _BIN_PKGS.get(name)
        if not pkgs:
            return False, f"{name}: sem mapeamento de pacote (instale manualmente)"
        installer = next((c for c in ("pkg", "apt-get", "dnf", "pacman", "brew")
                          if shutil.which(c)), None)
        if installer is None:
            return False, f"{name}: nenhum gerenciador de pacotes encontrado"
        key = {"apt-get": "apt", "dnf": "dnf", "pacman": "pacman",
               "brew": "brew", "pkg": "pkg"}[installer]
        pkg = pkgs.get(key)
        if not pkg:
            return False, f"{name}: pacote indisponível para {installer}"
        cmd = {"apt-get": [installer, "install", "-y", pkg],
               "dnf": [installer, "install", "-y", pkg],
               "pacman": [installer, "-S", "--noconfirm", "--needed", pkg],
               "brew": [installer, "install", pkg],
               "pkg": [installer, "install", "-y", pkg]}[installer]
        r = subprocess.run(cmd, capture_output=True, text=True,
                           timeout=timeout, check=False)
        ok = shutil.which(name) is not None
        detail = f"{name}: {'instalado' if ok else 'falha'} via {installer} ({pkg}) rc={r.returncode}"
        return ok, detail + ("" if ok or not r.stderr else f" — {r.stderr[-200:]}")
    except Exception as e:  # nunca derruba o turno por causa de instalação
        return False, f"{name}: erro ao tentar instalar: {e}"


def tools_status() -> dict[str, bool]:
    """Binários externos que as capacidades usam (nunca instalados por pip)."""
    bins = ["espeak", "espeak-ng", "say", "ffmpeg", "scrot", "grim",
            "gnome-screenshot", "screencapture", "fswebcam", "whisper",
            "arecord", "termux-camera-photo", "termux-notification",
            "termux-open", "ollama"]
    return {b: shutil.which(b) is not None for b in bins}
