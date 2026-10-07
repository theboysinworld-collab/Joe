import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
os.environ["JOE_HOME"] = str(Path(__file__).parent / "_joe_home")

from joe.agent import Agent
from joe.config import Config
from joe.ide.editor import MiniIDE
from joe.tools.base import registry


def setup_module():
    Path(os.environ["JOE_HOME"]).mkdir(exist_ok=True)


def test_tools_registered():
    names = {t.name for t in registry.all()}
    assert {"bash", "read_file", "write_file", "edit_file", "list_dir", "grep",
            "remember", "recall", "forget"} <= names


def test_file_tools(tmp_path):
    r = registry.execute("write_file", {"path": str(tmp_path / "a.txt"), "content": "l1\nl2\nl3"})
    assert r.ok
    r = registry.execute("read_file", {"path": str(tmp_path / "a.txt")})
    assert "l2" in r.output
    r = registry.execute("edit_file", {"path": str(tmp_path / "a.txt"), "old_text": "l2", "new_text": "L2X"})
    assert r.ok
    r = registry.execute("grep", {"pattern": "L2X", "path": str(tmp_path)})
    assert r.ok and "a.txt:2" in r.output


def test_bash_tool_no_confirm():
    cfg = Config.load()
    cfg.confirm_shell = False
    from joe.tools.shell import ShellTool
    sh = ShellTool(cfg)
    r = sh.run(command="echo oi")
    assert r.ok and "oi" in r.output


def test_memory(tmp_path):
    from joe.memory import Memory
    m = Memory(path=tmp_path / "mem.json")
    m.remember("usuario prefere python", tag="preferencia")
    assert m.search("python")
    m2 = Memory(path=tmp_path / "mem.json")  # persiste entre instancias
    assert any("python" in f["text"] for f in m2.facts)
    assert m.forget("python") == 1


def test_agent_offline_turno():
    cfg = Config.load()
    cfg.api_key_env = "JOE_DEFINITELY_MISSING_KEY"
    agent = Agent(config=cfg)
    reply = agent.chat("oi joe")
    assert "Joe" in reply or "offline" in reply.lower()


def test_mini_ide(tmp_path):
    ide = MiniIDE()
    ide.root = tmp_path
    ide.command(":new x.txt")
    ide.command(":insert 0 ola mundo")
    out = ide.command(":save")
    assert "salvo" in out
    assert (tmp_path / "x.txt").read_text().strip() == "ola mundo"
    ide.command(":edit 1 OLA MUNDO")
    ide.command(":save")
    assert "OLA" in (tmp_path / "x.txt").read_text()
