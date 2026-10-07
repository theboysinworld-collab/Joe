"""Configuração do Joe: ~/.joe/config.json + variáveis de ambiente."""

from __future__ import annotations

import json
import os
from dataclasses import dataclass, field, asdict
from pathlib import Path

JOE_HOME = Path(os.environ.get("JOE_HOME", Path.home() / ".joe"))
CONFIG_PATH = JOE_HOME / "config.json"
MEMORY_PATH = JOE_HOME / "memory.json"
HISTORY_PATH = JOE_HOME / "history"


@dataclass
class Config:
    model: str = os.environ.get("JOE_MODEL", "gpt-4o-mini")
    base_url: str = os.environ.get("JOE_BASE_URL", "https://api.openai.com/v1")
    api_key_env: str = "OPENAI_API_KEY"
    temperature: float = 0.3
    max_tokens: int = 4096
    # máximas iterações agente<->ferramentas por turno
    max_tool_iterations: int = 12
    # pedir confirmação antes de executar comandos shell?
    confirm_shell: bool = True
    # limite de saída de comandos (bytes)
    shell_output_limit: int = 8192
    language: str = "pt-BR"
    extra: dict = field(default_factory=dict)

    @classmethod
    def load(cls) -> "Config":
        cfg = cls()
        if CONFIG_PATH.exists():
            try:
                data = json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
                for k, v in data.items():
                    if hasattr(cfg, k) and k != "extra":
                        setattr(cfg, k, v)
                    else:
                        cfg.extra[k] = v
            except Exception:
                pass
        return cfg

    def save(self) -> Path:
        CONFIG_PATH.parent.mkdir(parents=True, exist_ok=True)
        payload = {k: v for k, v in asdict(self).items() if k != "extra"}
        payload.update(self.extra)
        CONFIG_PATH.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")
        return CONFIG_PATH

    @property
    def api_key(self) -> str | None:
        return os.environ.get(self.api_key_env) or self.extra.get("api_key")
