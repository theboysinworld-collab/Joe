"""Configuração do Joe: ~/.joe/config.json + variáveis de ambiente."""

from __future__ import annotations

import json
import os
from dataclasses import dataclass, field, asdict
from pathlib import Path

JOE_HOME = Path(os.environ.get("JOE_HOME", Path.home() / ".joe"))
CONFIG_PATH = JOE_HOME / "config.json"
MEMORY_PATH = JOE_HOME / "memory.json"          # legado (migra sozinho p/ SQLite)
DB_PATH = JOE_HOME / "joe.db"                   # memória unificada: SQLite
EXTENSIONS_DIR = JOE_HOME / "extensions"        # plug-ins drop-in
LEARNINGS_DIR = JOE_HOME / "learnings"          # playbooks gerados pela auto-análise
HISTORY_PATH = JOE_HOME / "history"
AUDIT_LOG = JOE_HOME / "audit.log"              # trilha de auditoria p/ auto-análise


@dataclass
class Config:
    model: str = os.environ.get("JOE_MODEL", "")          # vazio = escolher via benchmark
    base_url: str = os.environ.get("JOE_BASE_URL", "")
    api_key_env: str = "OPENAI_API_KEY"
    openrouter_api_key: str = ""                          # via `joe config openrouter <chave>`
    ollama_host: str = os.environ.get("OLLAMA_HOST", "http://localhost:11434")
    temperature: float = 0.3
    max_tokens: int = 4096
    # timeout (s) por tentativa de chamada ao LLM; modelos locais lentos (CPU, contexto
    # grande, primeiro carregamento) podem passar de 120s → aumente aqui ou via env.
    llm_timeout: int = int(os.environ.get("JOE_LLM_TIMEOUT", "300"))
    auto_falar: bool = os.environ.get("JOE_AUTO_FALAR", "").lower() in ("1", "true", "sim")
    # máximas iterações agente<->ferramentas por turno
    max_tool_iterations: int = 12
    # pedir confirmação antes de executar comandos shell?
    confirm_shell: bool = True
    # limite de saída de comandos (bytes)
    shell_output_limit: int = 8192
    language: str = "pt-BR"
    # embedding local (ollama) para RAG vetorial; fallback: hashing determinístico
    embed_model: str = "nomic-embed-text"
    chat_hint: str = "qwen2.5-coder"                      # preferência de nome (substring)
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

    @property
    def has_any_llm(self) -> bool:
        """Existe alguma rota de LLM possível nesta máquina? (usada pelo planner offline)"""
        if self.api_key or self.openrouter_api_key:
            return True
        try:  # ollama vivo?
            import json as _json, urllib.request as _rq
            with _rq.urlopen(f"{self.ollama_host.rstrip('/')}/api/tags", timeout=1.5) as r:
                return bool(_json.loads(r.read().decode()).get("models"))
        except Exception:
            return False
