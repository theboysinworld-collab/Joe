"""Testes da ferramenta de shell: política de confirmação, timeout e truncamento."""

from joe.config import Config
from joe.tools.shell import ShellTool


def _cfg(tmp_path, confirm=True):
    cfg = Config.load()
    cfg.confirm_shell = confirm
    cfg.shell_output_limit = 200
    return cfg


def test_execucao_ok(tmp_path):
    tool = ShellTool(_cfg(tmp_path, confirm=False))
    res = tool.run("echo oi")
    assert res.ok and "oi" in res.output


def test_cancelado_pelo_usuario(tmp_path):
    tool = ShellTool(_cfg(tmp_path, confirm=True), confirm_fn=lambda p: False)
    res = tool.run("echo x")
    assert not res.ok and "cancelou" in res.output


def test_confirmacao_para_comando_perigoso(tmp_path):
    prompts = []

    def cf(p):
        prompts.append(p)
        return False

    tool = ShellTool(_cfg(tmp_path, confirm=True), confirm_fn=cf)
    tool.run("rm -rf / --no-preserve-root")
    assert prompts and "PERIGOSO" in prompts[0]


def test_timeout(tmp_path):
    tool = ShellTool(_cfg(tmp_path, confirm=False))
    res = tool.run("sleep 3", timeout=1)
    assert not res.ok and "timeout" in res.output


def test_truncamento_de_saida(tmp_path):
    tool = ShellTool(_cfg(tmp_path, confirm=False))
    res = tool.run("yes A | head -c 5000")
    assert "truncada" in res.output and len(res.output) < 600


def test_exit_code_nao_zero(tmp_path):
    tool = ShellTool(_cfg(tmp_path, confirm=False))
    res = tool.run("false")
    assert not res.ok and res.meta["exit_code"] == 1
