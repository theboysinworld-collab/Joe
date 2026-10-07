"""EventBus — a espinha dorsal de comunicação do Joe.

TODOs:
1. Publicadores emitem eventos nomeados (dict serializável) SEM conhecer consumidores.
2. Consumidores (UI, memória, auditoria, sub-agentes) assinam tópicos por prefixo.
3. O grafo visual na interface central é alimentado exclusivamente por estes eventos.
4. Nenhum módulo importa outro módulo de negócio: todos se falam pelo bus.
"""

from __future__ import annotations

import threading
import time
from typing import Callable

Listener = Callable[[dict], None]


class EventBus:
    def __init__(self) -> None:
        self._listeners: list[tuple[str, Listener]] = []
        self._lock = threading.Lock()
        # ring buffer dos últimos eventos — a UI reconstrói o grafo a partir dele
        self.replay: list[dict] = []
        self._replay_max = 500

    def subscribe(self, topic_prefix: str, listener: Listener) -> None:
        with self._lock:
            self._listeners.append((topic_prefix, listener))

    def unsubscribe(self, listener: Listener) -> None:
        with self._lock:
            self._listeners = [(t, l) for t, l in self._listeners if l is not listener]

    def emit(self, topic: str, **data) -> dict:
        event = {"topic": topic, "ts": time.time(), **data}
        with self._lock:
            self.replay.append(event)
            self.replay = self.replay[-self._replay_max:]
            listeners = [l for t, l in self._listeners if topic.startswith(t)]
        for l in listeners:
            try:
                l(event)
            except Exception:  # um consumidor nunca derruba o sistema
                pass
        return event


# instância global — importar `from ..bus import bus` e usar; não há acoplamento real
bus = EventBus()
