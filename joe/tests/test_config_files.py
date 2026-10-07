"""Testes de configuração (persistência/env) e ferramentas de arquivos."""

import json

from joe.config import Config, CONFIG_PATH
from joe.tools import files as F


# ---------------------------------------------------------------- config
def test_save_load_roundtrip(tmp_path, monkeypatch):
    monkeypatch.setattr("joe.config.CONFIG_PATH", tmp_path / "cfg.json")
    cfg = Config.load()
    cfg.model = "gpt-4o"
    cfg.confirm_shell = False
    cfg.api_key_env = "OPENROUTER_API_KEY"   # campo real do dataclass (não cai em extra)
    p = cfg.save()
    data = json.loads(p.read_text())
    assert data["model"] == "gpt-4o" and data["api_key_env"] == "OPENROUTER_API_KEY"
    back = Config.load()
    assert back.model == "gpt-4o" and back.confirm_shell is False
    assert back.api_key_env == "OPENROUTER_API_KEY"


def test_corrompido_nao_quebra(tmp_path, monkeypatch):
    monkeypatch.setattr("joe.config.CONFIG_PATH", tmp_path / "bad.json")
    (tmp_path / "bad.json").write_text("{nao é json")
    cfg = Config.load()          # deve cair no default silenciosamente
    assert cfg.language == "pt-BR"


def test_api_key_env_tem_precedencia(monkeypatch):
    monkeypatch.setenv("JOE_API_KEY_ENV_TEST", "")  # garante base limpa
    cfg = Config(api_key_env="OPENROUTER_API_KEY_TEST")
    monkeypatch.setenv("OPENROUTER_API_KEY_TEST", "sk-from-env")
    assert cfg.api_key == "sk-from-env"


# ---------------------------------------------------------------- tools/files
def test_read_write_edit_roundtrip(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    p = tmp_path / "a.txt"
    r = F.WriteFileTool().run(str(p), "linha1\nlinha2\n")
    assert r.ok
    r = F.ReadFileTool().run(str(p))
    assert "linha2" in r.output
    r = F.EditFileTool().run(str(p), "linha1", "PRIMEIRA")
    assert r.ok and "PRIMEIRA" in p.read_text()
    # old inexistente -> falha graciosa
    r = F.EditFileTool().run(str(p), "nao existe", "x")
    assert not r.ok


def test_read_com_start_count(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    p = tmp_path / "b.txt"
    p.write_text("\n".join(f"l{i}" for i in range(1, 51)))
    r = F.ReadFileTool().run(str(p), start=10, count=3)
    assert "l10" in r.output and "l13" not in r.output


def test_list_dir_e_grep(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    (tmp_path / "sub").mkdir()
    (tmp_path / "sub" / "c.py").write_text("ALVO = 1\n")
    r = F.ListDirTool().run(".")
    assert "sub" in r.output
    r = F.GrepTool().run("ALVO", path=".", glob="**/*.py")
    assert "c.py" in r.output
    r = F.GrepTool().run("NADA_AQUI", path=".")
    assert r.ok or "nenhuma" in r.output.lower() or r.output.strip() == "" or "(sem" in r.output


def test_arquivos_inexistentes_falham_graciosamente(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    assert not F.ReadFileTool().run("nope.txt").ok
    assert not F.ListDirTool().run("nope_dir").ok
    assert not F.EditFileTool().run("nope.txt", "a", "b").ok
