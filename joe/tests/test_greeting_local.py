"""Saudação offline: o Joe responde localmente (sem LLM), lembra o nome e agradece."""
import os, subprocess, sys
from joe.agents import classify_intent, deterministic_plan, greeting_reply


def _run_once(tmp_path, text, home):
    env = dict(os.environ, JOE_HOME=str(home))
    r = subprocess.run([sys.executable, "run.py", "--once", text], cwd="/workspace/joe",
                       capture_output=True, text=True, timeout=90, env=env)
    return r.stdout + r.stderr


class TestGreetingOffline:
    def test_classifica_varias_colocais(self):
        for frase in ("oi joe", "e ai, tudo bem?", "bom dia!", "opa", "hello there",
                      "quem é você?", "obrigado joe"):
            assert classify_intent(frase)[0] == "conversar", frase

    def test_plano_tem_greeting_sem_llm(self):
        plan = deterministic_plan("oi joe")
        assert plan and plan[0]["name"] == "greeting"

    def test_resposta_cumprimento_offline(self):
        out = greeting_reply("oi joe")
        assert "Joe" in out and "Experimente" in out

    def test_quem_e_voce_responde_capacidades(self):
        out = greeting_reply("quem é você?")
        assert "IDE" in out and "agente" in out

    def test_agradecimento(self):
        out = greeting_reply("obrigado!")
        assert "Disponha" in out

    def test_lembranca_do_nome_via_memoria(self, tmp_path):
        class FakeMem:
            def search(self, q, k=6):
                return [{"text": "meu nome é Marina"}]
        out = greeting_reply("opa joe", memory=FakeMem())
        assert "Marina" in out

    def test_e2e_run_py_com_nome_persistido(self, tmp_path):
        home = tmp_path / "joe_home"
        _run_once(tmp_path, "lembre-se que meu nome é Marina", home)
        out = _run_once(tmp_path, "oi joe", home)
        assert "Olá, Marina!" in out
