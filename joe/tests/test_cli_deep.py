"""Cobertura profunda da CLI: subcomandos argparse + branches finos do REPL."""
import sys

import pytest


@pytest.fixture()
def joe_home(tmp_path, monkeypatch):
    monkeypatch.setenv("JOE_HOME", str(tmp_path / "joe-home"))
    monkeypatch.setenv("JOE_NO_DAEMON", "1")
    return tmp_path


def _run(joe_cli, argv, monkeypatch):
    monkeypatch.setattr(sys, "argv", ["joe", *argv])
    return joe_cli.main()


def test_cmd_config_e_memory_via_main(joe_home, monkeypatch):
    from joe import cli
    assert _run(cli, ["config", "model", "qwen2.5-coder"], monkeypatch) == 0
    assert _run(cli, ["config", "openrouter", "sk-test"], monkeypatch) == 0
    assert _run(cli, ["config", "qualquer_coisa", "valor"], monkeypatch) == 0
    assert _run(cli, ["memory", "add", "gosto", "de", "café"], monkeypatch) == 0
    assert _run(cli, ["memory", "list"], monkeypatch) == 0
    assert _run(cli, ["memory", "search", "café"], monkeypatch) == 0
    from joe.config import Config
    cfg = Config.load()
    assert cfg.model == "qwen2.5-coder" and cfg.openrouter_api_key == "sk-test"
    assert cfg.extra["qualquer_coisa"] == "valor"


def test_cmd_ide_and_bench_and_health_via_main(joe_home, monkeypatch, capsys):
    from joe import cli
    # ide sem arquivo: run_ide com input já no :q (run_ide retorna None → main normaliza p/ 0)
    monkeypatch.setattr("builtins.input", lambda *a: ":q")
    assert _run(cli, ["ide"], monkeypatch) in (0, None)
    # bench sem provedores disponíveis → mensagem e exit 1 (caminho real offline)
    rc = _run(cli, ["bench"], monkeypatch)
    assert rc in (0, 1)
    assert _run(cli, ["health"], monkeypatch) == 0
    out = capsys.readouterr().out
    assert "ferramentas" in out.lower() or "capacidades" in out.lower()


def test_compat_positional_ask_vira_pergunta(joe_home, monkeypatch, capsys):
    from joe import cli
    rc = _run(cli, ["oi joe"], monkeypatch)
    assert rc == 0
    assert capsys.readouterr().out.strip()  # respondeu algo (offline ou LLM)


def test_repl_eof_break_and_extra_branches(joe_home, monkeypatch, capsys):
    from joe import cli
    seq = iter([
        "/graph live",          # entra no loop ao vivo; q encerra
        "q",                    # tecla de saída do live loop
        "/config provider ollama",   # setattr genérico em cfg
        "/sub pesquisador para mapear o repo",  # ramo 'para'
        "/sub fazer café",      # ramo genérico (_sub_name/_sub_goal)
        "/ext",                 # lista extensões (ramo vazio incluso)
        "",                     # linha vazia → continue
        "/cmd-inexistente arg",  # slash desconhecido → ferramenta inexistente
        "EOF",
    ])

    def fake_input(prompt=""):
        item = next(seq)
        if item == "EOF":
            raise EOFError
        return item

    monkeypatch.setattr("builtins.input", fake_input)
    # live_graph usa sys.stdin.readline quando não-TTY; mockamos p/ caminho rico
    # e limitamos fps p/ teste rápido (throttling real continua coberto em test_live_graph_fps)
    monkeypatch.setattr("joe.ui.live_graph", lambda **kw: 2)
    assert cli.repl() == 0
    out = capsys.readouterr().out
    assert "ao vivo" in out.lower() or "grafo" in out.lower()


def test_repl_ide_e_reset_exit(joe_home, monkeypatch, capsys):
    from joe import cli
    seq = iter(["/ide", ":q", "/reset", "/exit"])
    monkeypatch.setattr("builtins.input", lambda *a: next(seq))
    assert cli.repl() == 0


def test_live_graph_fps_throttle(monkeypatch):
    """Com input_fn presente o loop dá sleep(1/fps) — sem spin-busy."""
    from joe import ui
    slept: list[float] = []
    monkeypatch.setattr(ui.time, "sleep", lambda s: slept.append(s))
    keys = iter(["", "q"])  # enter repinta, q sai
    frames = ui.live_graph(input_fn=lambda: next(keys), fps=7.0, max_secs=0)
    assert frames >= 2 and all(x == pytest.approx(1 / 7.0) for x in slept)


def test_repl_exit_after_reset(joe_home, monkeypatch):
    from joe import cli
    seq = iter(["/reset", "/exit"])
    monkeypatch.setattr("builtins.input", lambda *a: next(seq))
    assert cli.repl() == 0
