"""Memória persistente do Joe (~/.joe/memory.json).

O agente lembra fatos sobre o usuário e contexto entre sessões.
"""

from __future__ import annotations

import json
import time
from pathlib import Path

from .config import MEMORY_PATH


class Memory:
    def __init__(self, path: Path = MEMORY_PATH):
        self.path = Path(path)
        self.facts: list[dict] = []
        self.load()

    def load(self) -> None:
        if self.path.exists():
            try:
                data = json.loads(self.path.read_text(encoding="utf-8"))
                self.facts = data.get("facts", [])
            except Exception:
                self.facts = []

    def save(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text(
            json.dumps({"facts": self.facts}, indent=2, ensure_ascii=False),
            encoding="utf-8",
        )

    def remember(self, text: str, tag: str = "geral") -> dict:
        entry = {"text": text, "tag": tag, "ts": time.time()}
        # evita duplicatas exatas
        for f in self.facts:
            if f["text"].lower() == text.lower():
                f.update(entry)
                self.save()
                return f
        self.facts.append(entry)
        self.save()
        return entry

    def forget(self, needle: str) -> int:
        before = len(self.facts)
        self.facts = [f for f in self.facts if needle.lower() not in f["text"].lower()]
        self.save()
        return before - len(self.facts)

    def search(self, needle: str) -> list[dict]:
        n = needle.lower()
        return [f for f in self.facts if n in f["text"].lower() or n in f.get("tag", "").lower()]

    def context_block(self, limit: int = 40) -> str:
        if not self.facts:
            return ""
        recent = self.facts[-limit:]
        lines = "\n".join(f"- [{f['tag']}] {f['text']}" for f in recent)
        return f"MEMÓRIA PERSISTENTE (sessões anteriores):\n{lines}"
