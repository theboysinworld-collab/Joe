"""Testes do scheduler proativo (coração autônomo) e do boot com background."""
import subprocess
import sys
import time
from pathlib import Path


from joe.bus import bus
from joe.scheduler import Scheduler

ROOT = Path(__file__).resolve().parents[1]  # /workspace/joe


def test_register_task_rodar_em_cada_iteracao():
    s = Scheduler()
    hits = []
    s.register("t", 1.0, lambda: hits.append(1) or "ok")
    assert s.run_once() == 1
    assert hits == [1]
    # segunda chamada imediata não repete (respeita intervalo)
    assert s.run_once() == 0
    assert hits == [1]


def test_sensor_emite_apenas_quando_muda():
    s = Scheduler()
    events = []
    listener = lambda ev: events.append(ev)
    bus.subscribe("sense.", listener)
    try:
        state = {"v": "a"}
        s.add_sensor("x", lambda: state["v"])
        s.run_once()                       # primeira leitura → emite
        s.run_once()                       # igual → silêncio
        assert len(events) == 1
        state["v"] = "b"
        s.run_once()                       # mudou → emite de novo
        assert len(events) == 2
        assert events[1]["topic"] == "sense.x" and events[1]["observation"] == "b"
    finally:
        bus.unsubscribe(listener)


def test_sensor_quebrado_nao_propaga():
    s = Scheduler()

    def boom():
        raise RuntimeError("sensor caiu")

    s.add_sensor("ruim", boom)
    s.run_once()                           # não levanta exceção
    assert s.iterations == 1


def test_tarefa_com_erro_vira_evento_de_erro():
    s = Scheduler()
    events = []
    listener = lambda ev: events.append(ev)
    bus.subscribe("scheduler.error", listener)
    try:
        s.register("ruim", 1.0, lambda: (_ for _ in ()).throw(ValueError("x")))
        s.run_once()
        assert any(e.get("name") == "ruim" for e in events)
    finally:
        bus.unsubscribe(listener)


def test_proatividade_sugere_por_habito(tmp_path):
    from joe.memory import Memory

    mem = Memory(db_path=tmp_path / "m.db")
    # API pública registra o hábito; depois envelhecemos last_ts p/ simular recorrência
    mem.observe_habit("lembrete")
    mem.conn.execute("UPDATE habits SET count=5, last_ts=?", (time.time() - 7200,))
    mem.conn.commit()
    s = Scheduler()
    s.set_memory(mem)
    s._last_suggest_ts = 0.0
    events = []
    listener = lambda ev: events.append(ev)
    bus.subscribe("suggest", listener)
    try:
        s.run_once()
        assert events and events[0]["items"] == ["lembrete"]
        s.run_once()                       # cooldown: não repete
        assert len(events) == 1
    finally:
        bus.unsubscribe(listener)
        mem.close()


def test_thread_daemon_start_stop():
    s = Scheduler(tick_s=0.05)
    hits = []
    s.register("pulso", 0.01, lambda: hits.append(1))
    s._tasks["pulso"] = (0.01, s._tasks["pulso"][1], 0.0)   # força disparo imediato
    s.start()
    s.start()                              # idempotente: não duplica thread
    time.sleep(0.3)
    s.stop()
    assert len(hits) >= 2
    assert not s._thread.is_alive()


def test_full_boot_liga_scheduler_e_desliga():
    """full_boot(background=True) liga o coração proativo (subprocesso isolado)."""
    code = ("import sys; sys.path.insert(0, %r);"
            "import importlib.util as u;"
            "spec = u.spec_from_file_location('run_joe_sched', %r);"
            "m = u.module_from_spec(spec); spec.loader.exec_module(m);"
            "cfg, mem = m.full_boot(background=True);"
            "from joe.scheduler import scheduler;"
            "assert scheduler._thread and scheduler._thread.is_alive();"
            "assert 'notificacoes' in scheduler._sensors;"
            "scheduler.stop(timeout=2); mem.close(); print('SCHED-BOOT-OK')"
            % (str(ROOT), str(ROOT / "run.py")))
    r = subprocess.run([sys.executable, "-c", code],
                       capture_output=True, text=True, timeout=60, cwd=str(ROOT))
    assert r.returncode == 0, r.stderr
    assert "SCHED-BOOT-OK" in r.stdout
