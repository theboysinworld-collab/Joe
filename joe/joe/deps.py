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
import os
import platform
import shutil
import subprocess
import sys
import time
from pathlib import Path

# módulos já verificados nesta sessão: evita repetir tentativas caras
_checked: dict[str, bool] = {}
_installed: set[str] = set()
# log auditável das tentativas de auto-baixa (sucesso/falha/motivo)
_deps_log: list[dict] = []


def deps_log() -> list[dict]:
    """Cópias das tentativas de instalação desta sessão: [{module, package, ok, reason}]."""
    return list(_deps_log)


def clear_caches() -> None:
    """Zera caches/log — usado por testes e por /heal para re-verificar dependências."""
    _checked.clear()
    _installed.clear()
    _deps_log.clear()


def _pip_install(pkg: str, timeout: int = 60) -> tuple[bool, str]:
    """Tenta instalar `pkg` via pip do próprio interpretador.

    Nunca silencioso para o chamador: devolve (ok, motivo). O motivo vai para
    o log auditável (`deps_log`) — sem isso, falhas de instalação eram
    invisíveis e a promessa de "auto-cura de dependências" não era verificável.
    """
    try:
        r = subprocess.run(
            [sys.executable, "-m", "pip", "install", "--quiet", "--disable-pip-version-check", pkg],
            capture_output=True, text=True, timeout=timeout,
        )
        if r.returncode == 0:
            return True, "instalado"
        err = (r.stderr or r.stdout or "").strip().splitlines()
        return False, f"pip exit {r.returncode}: {err[-1][:200] if err else 'sem saída'}"
    except subprocess.TimeoutExpired:
        return False, f"pip timeout ({timeout}s)"
    except Exception as exc:  # nunca lança para o chamador
        return False, f"erro: {type(exc).__name__}: {exc}"


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
            ok_inst, reason = _pip_install(pkg)
            if ok_inst:
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
                    reason = "instalado e importa"
                except Exception as exc:
                    ok = False
                    reason = f"instalado mas import falhou: {type(exc).__name__}: {exc}"
            _deps_log.append({"module": module, "package": pkg, "ok": ok, "reason": reason})
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
        "piper": "piper-tts",      # síntese neural local (Python <3.15)
        "numpy": "numpy",          # RAG vetorial acelerado (opcional)
        "pytesseract": "pytesseract",  # OCR via wrapper Python
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


def _candidate_flutter_roots() -> list[Path]:
    roots = []
    for variable in ("FLUTTER_ROOT", "FLUTTER_HOME"):
        if os.environ.get(variable):
            roots.append(Path(os.environ[variable]))
    roots.extend([
        Path("/workspaces/.flutter-sdk"),
        Path.home() / ".flutter-sdk",
        Path.home() / "development" / "flutter",
    ])
    if os.name == "nt":
        local_app_data = os.environ.get("LOCALAPPDATA")
        if local_app_data:
            roots.append(Path(local_app_data) / "Programs" / "Flutter")
        roots.append(Path("C:/src/flutter"))
    else:
        roots.extend([Path("/opt/flutter"), Path("/usr/local/flutter")])
    return list(dict.fromkeys(roots))


def _candidate_dart_roots() -> list[Path]:
    candidates = [Path(root) / "bin" for root in _candidate_flutter_roots()]
    if os.environ.get("DART_SDK"):
        candidates.append(Path(os.environ["DART_SDK"]) / "bin")
    candidates.extend([
        Path("/workspaces/.dart-sdk/bin"),
        Path.home() / ".dart-sdk" / "bin",
        Path("/usr/lib/dart/bin"),
    ])
    return list(dict.fromkeys(candidates))


def _candidate_runtime_roots() -> list[Path]:
    roots = []
    for variable in ("CONDA_PREFIX", "MAMBA_ROOT_PREFIX", "JOE_TOOL_PREFIX"):
        if os.environ.get(variable):
            roots.append(Path(os.environ[variable]))
    roots.append(Path("/opt/conda"))
    return list(dict.fromkeys(roots))


def prepare_runtime_paths() -> list[str]:
    """Expose already-installed local SDKs, commands, libraries and voice data."""
    added: list[str] = []
    bins = []
    for root in _candidate_flutter_roots():
        bins.append(Path(root) / "bin")
    for root in _candidate_dart_roots():
        bins.append(Path(root))
    library_dirs = []
    for root in _candidate_runtime_roots():
        bins.append(root / "usr" / "bin")
        library_dirs.extend([root / "lib", root / "usr" / "lib" / "x86_64-linux-gnu"])
        voice_data = root / "usr" / "lib" / "x86_64-linux-gnu" / "espeak-ng-data"
        if voice_data.is_dir():
            os.environ.setdefault("ESPEAK_DATA_PATH", str(voice_data))
    current = os.environ.get("PATH", "").split(os.pathsep)
    for folder in bins:
        path = str(folder)
        if folder.exists() and path not in current:
            current.insert(0, path)
            added.append(path)
    if added:
        os.environ["PATH"] = os.pathsep.join(current)
    library_path = os.environ.get("LD_LIBRARY_PATH", "").split(os.pathsep)
    added_libraries = []
    for folder in library_dirs:
        path = str(folder)
        if folder.is_dir() and path not in library_path:
            library_path.insert(0, path)
            added_libraries.append(path)
    if added_libraries:
        os.environ["LD_LIBRARY_PATH"] = os.pathsep.join(library_path)
    return added


def environment_report() -> dict:
    """Relatório explícito do ambiente: Python, host, Flutter/Dart, módulos e binários.
    Essencial para decidir "full-runtime" vs "native-client" versus "fallback-only".
    """
    prepare_runtime_paths()
    flutter_bin = shutil.which("flutter") or next((str(Path(p) / "bin" / "flutter") for p in _candidate_flutter_roots() if (Path(p) / "bin" / "flutter").exists()), None)
    dart_bin = shutil.which("dart") or next((str(Path(p) / "dart") for p in _candidate_dart_roots() if (Path(p) / "dart").exists()), None)
    python_supported = sys.version_info >= (3, 10)
    modules = {
        "rich": "rich",
        "PIL": "pillow",
        "pytesseract": "pytesseract",
        "numpy": "numpy",
        "pyttsx3": "pyttsx3",
        "piper": "piper-tts",
    }
    system_tools = {
        name: shutil.which(name) is not None
        for name in [
            "ffmpeg", "espeak", "espeak-ng", "say", "scrot", "grim",
            "whisper", "arecord", "termux-notification", "ollama", "powershell", "pwsh", "tesseract",
        ]
    }
    report = {
        "platform": {
            "system": platform.system(),
            "release": platform.release(),
            "machine": platform.machine(),
            "python": sys.version.split()[0],
            "executable": sys.executable,
            "termux": "com.termux" in os.environ.get("PREFIX", ""),
        },
        "runtime_modes": {
            "python": "full-runtime" if python_supported else "unsupported-python-version",
            "flutter": "toolchain-available" if flutter_bin else "not-installed",
            "dart": "toolchain-available" if dart_bin else "not-installed",
        },
        "flutter": {
            "available": bool(flutter_bin),
            "path": flutter_bin,
        },
        "dart": {
            "available": bool(dart_bin),
            "path": dart_bin,
        },
        "python_modules": {
            name: {"available": ensure(name, package, allow_install=False), "package": package}
            for name, package in modules.items()
        },
        "system_tools": system_tools,
        "install_plan": [],
        "optional_setup": [],
    }
    if not python_supported:
        report["install_plan"].append("python>=3.10")
    if not report["flutter"]["available"]:
        report["optional_setup"].append("flutter-sdk: install Flutter only to build/run the native client")
    if not report["dart"]["available"]:
        report["optional_setup"].append("dart-sdk: Flutter bundles Dart; standalone Dart is optional")
    for name, info in report["python_modules"].items():
        if not info["available"]:
            report["optional_setup"].append(f"python:{name}:{info['package']}")
    missing_tools = [name for name, available in system_tools.items() if not available]
    if missing_tools:
        report["optional_setup"].append("system-tools:" + ",".join(missing_tools))
    return report


def ensure_runtime_environment(features=()) -> dict:
    """Diagnostica o ambiente e instala dependências só para features solicitadas.

    Sem features, a função é passiva. Recursos desconhecidos ou sem mapeamento
    confiável permanecem em fallback e são reportados sem tentar adivinhar pacotes.
    Features com instalação conhecida: image, speech, video, rag e phone (Termux).
    """
    report = environment_report()
    if isinstance(features, str):
        features = (features,)
    request_notes = []
    for feature in dict.fromkeys(features):
        if feature == "image":
            if not report["python_modules"]["PIL"]["available"]:
                ensure("PIL", "pillow", allow_install=True)
        elif feature == "speech":
            if sys.version_info < (3, 15) and not report["python_modules"]["piper"]["available"]:
                ensure("piper", "piper-tts", allow_install=True)
            if not any(shutil.which(name) for name in ("say", "espeak", "espeak-ng")):
                installed = any(ensure_bin(name, timeout=120)[0] for name in ("espeak-ng", "espeak"))
                if not installed:
                    ensure("pyttsx3", "pyttsx3", allow_install=True)
        elif feature == "video":
            if not shutil.which("ffmpeg"):
                ensure_bin("ffmpeg", timeout=180)
        elif feature == "rag":
            if not report["python_modules"]["numpy"]["available"]:
                ensure("numpy", "numpy", allow_install=True)
        elif feature == "ocr":
            if not report["python_modules"]["pytesseract"]["available"]:
                ensure("pytesseract", "pytesseract", allow_install=True)
            if not shutil.which("tesseract"):
                ensure_bin("tesseract", timeout=180)
        elif feature == "phone":
            if report["platform"]["termux"] and not shutil.which("termux-notification"):
                ensure_bin("termux-api", timeout=180)
            elif not report["platform"]["termux"]:
                request_notes.append("phone: requer Termux/Android; mantido indisponível neste host")
        else:
            request_notes.append(f"{feature}: feature desconhecida; nenhuma instalação tentada")
    updated_report = environment_report()
    updated_report["optional_setup"].extend(request_notes)
    return updated_report


def tools_status() -> dict[str, bool]:
    """Binários externos que as capacidades usam (nunca instalados por pip)."""
    bins = ["espeak", "espeak-ng", "say", "ffmpeg", "scrot", "grim",
            "gnome-screenshot", "screencapture", "fswebcam", "whisper",
            "arecord", "powershell", "pwsh", "tesseract", "termux-camera-photo", "termux-notification",
            "termux-open", "ollama"]
    return {b: shutil.which(b) is not None for b in bins}
