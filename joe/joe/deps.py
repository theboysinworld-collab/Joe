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


def tools_status() -> dict[str, bool]:
    """Binários externos que as capacidades usam (nunca instalados por pip)."""
    bins = ["espeak", "espeak-ng", "say", "ffmpeg", "scrot", "grim",
            "gnome-screenshot", "screencapture", "fswebcam", "whisper",
            "arecord", "termux-camera-photo", "termux-notification",
            "termux-open", "ollama"]
    return {b: shutil.which(b) is not None for b in bins}
