"""Agendador proativo do Joe — o "coração" autônomo que roda em segundo plano.

TODOs (fluxo padrão de integração):
1. `register(name, interval_s, fn)` é a ÚNICA porta para adicionar comportamento
   periódico — módulos/extensões novos nunca editam o núcleo: chamam register().
2. O loop observa os SENSORS registrados (funções sem argumentos que devolvem
   texto/dict ou None). Cada leitura vira memória (episódio) e alimenta hábitos.
3. Quando um sensor detecta algo novo (hash diferente da última leitura), o
   scheduler EMITE evento no bus ("sense.<nome>") — agentes/assinantes reagem.
4. A heurística de PROATIVIDADE usa Memory.predict(): intenções recorrentes na
   mesma hora viram sugestões emitidas como "suggest" (a UI mostra no grafo).
5. Tudo tem fallback: sem LLM, sem internet e sem sensores, o scheduler apenas
   roda as tarefas registradas e dorme — nunca lança exceção para fora.

Uso típico (feito automaticamente por run.py full_boot):
    from joe.scheduler import start_background, register
    register("resumo", 600, minha_funcao)     # tarefa periódica plugável
    start_background()                          # daemon; stop_background() p/ encerrar
"""

from __future__ import annotations

import hashlib
import json
import threading
import time
from typing import Callable

from .bus import bus


class Scheduler:
    def __init__(self, tick_s: float = 5.0) -> None:
        self.tick_s = tick_s
        self._tasks: dict[str, tuple[float, Callable[[], object], float]] = {}
        self._sensors: dict[str, tuple[Callable[[], object], str]] = {}
        self._last_hash: dict[str, str] = {}
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._memory = None            # injetável (evita ciclo com memory)
        self._last_suggest_ts = 0.0
        self.iterations = 0            # útil para testes / health

    # ------------------------------------------------------------ portas
    def set_memory(self, memory) -> None:
        self._memory = memory

    def register(self, name: str, interval_s: float, fn: Callable[[], object]) -> None:
        """Adiciona/substitui uma tarefa periódica. Plugável por extensões."""
        self._tasks[name] = (max(1.0, float(interval_s)), fn, 0.0)

    def add_sensor(self, name: str, reader: Callable[[], object]) -> None:
        """Sensor = função que 'observa' algo (tela, notificações, câmera...).

        Quando a leitura MUDA desde a última vez, emite sense.<name> no bus e
        grava episódio na memória (se houver). Nunca quebra o loop.
        """
        self._sensors[name] = (reader, "")

    def remove(self, name: str) -> None:
        self._tasks.pop(name, None)
        self._sensors.pop(name, None)

    # ------------------------------------------------------------ execução
    def _run_task(self, name: str) -> None:
        _, fn, _ = self._tasks[name]
        try:
            out = fn()
            bus.emit("scheduler.task", name=name, result=str(out)[:200])
        except Exception as exc:                       # nunca propaga
            bus.emit("scheduler.error", name=name, error=str(exc)[:200])

    def _poll_sensor(self, name: str) -> None:
        reader, _ = self._sensors[name]
        try:
            raw = reader()
        except Exception:
            return                                     # sensor indisponível: ignora
        if raw is None:
            return
        text = raw if isinstance(raw, str) else json.dumps(raw, ensure_ascii=False, default=str)
        h = hashlib.md5(text.encode()).hexdigest()
        if h == self._last_hash.get(name):
            return                                     # nada novo → silêncio
        self._last_hash[name] = h
        bus.emit(f"sense.{name}", observation=text[:500])
        if self._memory is not None:
            try:
                self._memory.log_event(f"sense.{name}", {"observation": text[:500]})
            except Exception:
                pass

    def _maybe_suggest(self) -> None:
        """Proatividade: sugere com base em hábitos desta hora (com cooldown)."""
        if self._memory is None or time.time() - self._last_suggest_ts < 900:
            return
        try:
            preds = self._memory.predict(max_results=2)
        except Exception:
            return
        if preds:
            self._last_suggest_ts = time.time()
            bus.emit("suggest", items=[p["intent"] for p in preds])

    def run_once(self) -> int:
        """Uma iteração completa (útil em testes). Retorna nº de ações tomadas."""
        now = time.time()
        actions = 0
        for name, (interval, _fn, last) in list(self._tasks.items()):
            if now - last >= interval:
                self._tasks[name] = (interval, _fn, now)
                self._run_task(name)
                actions += 1
        for name in list(self._sensors):
            self._poll_sensor(name)
        self._maybe_suggest()
        self.iterations += 1
        return actions

    def _loop(self) -> None:
        while not self._stop.is_set():
            self.run_once()
            self._stop.wait(self.tick_s)

    def start(self) -> None:
        if self._thread and self._thread.is_alive():
            return
        self._stop.clear()
        self._thread = threading.Thread(target=self._loop, daemon=True, name="joe-scheduler")
        self._thread.start()

    def stop(self, timeout: float = 2.0) -> None:
        self._stop.set()
        if self._thread:
            self._thread.join(timeout=timeout)


# singleton do processo + API módulo-level (porta estável p/ extensões)
scheduler = Scheduler()
register = scheduler.register
add_sensor = scheduler.add_sensor


def start_background(memory=None, tick_s: float = 5.0) -> Scheduler:
    """Liga o coração proativo. Chamado pelo run.py full_boot() — idempotente."""
    if memory is not None:
        scheduler.set_memory(memory)
    scheduler.tick_s = tick_s
    scheduler.start()
    return scheduler


def stop_background() -> None:
    scheduler.stop()
