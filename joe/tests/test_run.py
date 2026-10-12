"""Testes do run.py — ponto de entrada único que inicializa o sistema inteiro."""
from __future__ import annotations

import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]  # /workspace/joe


def _run(*args: str) -> subprocess.CompletedProcess:
    return subprocess.run([sys.executable, str(ROOT / "run.py"), *args],
                          capture_output=True, text=True, timeout=60, cwd=str(ROOT))


def test_run_check_boots_all_layers():
    r = _run("--check")
    assert r.returncode == 0, r.stderr
    assert "boot OK" in r.stdout
    assert "ferramentas" in r.stdout and "memória" in r.stdout


def test_run_once_executes_offline_planner():
    r = _run("--once", "lembre-se que gosto de café coado")
    assert r.returncode == 0, r.stderr
    assert "remember" in r.stdout or "planner" in r.stdout.lower()


def test_run_health_reports_modules():
    r = _run("--health")
    assert r.returncode == 0, r.stderr
    for section in ("Capacidades", "Ferramentas"):
        assert section in r.stdout


def test_full_boot_importable_from_any_cwd(tmp_path):
    """full_boot() funciona chamado de fora do diretório do projeto (path autônomo)."""
    code = ("import sys; sys.path.insert(0, %r);"
            "import importlib.util as u;"
            "spec = u.spec_from_file_location('runpy_joe', %r);"
            "m = u.module_from_spec(spec); spec.loader.exec_module(m);"
            "cfg, mem = m.full_boot(); print('BOOT-FULL-OK')"
            % (str(ROOT), str(ROOT / "run.py")))
    r = subprocess.run([sys.executable, "-c", code],
                       capture_output=True, text=True, timeout=60, cwd=str(tmp_path))
    assert r.returncode == 0, r.stderr
    assert "BOOT-FULL-OK" in r.stdout
