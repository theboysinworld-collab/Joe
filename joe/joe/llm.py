"""Camada de LLM do Joe — PLUGGABLE por design (Provider Registry).

Arquitetura (não mude o núcleo, adicione Providers):
1. `Provider` é a interface: id(), available(), list_models(), chat(messages, tools), embed(texts).
2. ProviderRegistry mantém uma ÚNICA seleção ativa por vez → nunca carregamos dois
   modelos grandes na memória simultaneamente (requisito explícito do usuário).
3. Providers embutidos: OllamaProvider (local, sem internet), OpenAIProvider e
   OpenRouterProvider (nuvem). Extensões podem registrar outros (vLLM, LMStudio...).
4. `benchmark()` testa latência/disponibilidade dos candidatos disponíveis e
   escolhe automaticamente o melhor modelo para chat (e embeddings via provider ativo).
5. `chat_completion()` / `embed_texts()` são fachadas estáveis: o resto do sistema
   só fala com estas duas funções, nunca com um provider específico.
6. Sem provedor nenhum disponível? O agente cai no planner determinístico offline
   (ver agents.py) — a base continua útil sem LLM/API/rede.
"""

from __future__ import annotations

import abc
import json
import socket
import time
import urllib.error
import urllib.request

from .config import Config


class LLMError(Exception):
    pass


# ---------------------------------------------------------------- interface
class Provider(abc.ABC):
    id: str = ""

    def __init__(self, config: Config):
        self.config = config

    @abc.abstractmethod
    def available(self) -> bool: ...

    @abc.abstractmethod
    def list_models(self) -> list[str]: ...

    @abc.abstractmethod
    def chat(self, messages: list[dict], tools: list[dict] | None) -> dict:
        """Retorna mensagem estilo OpenAI: {'role':'assistant','content':...,'tool_calls':[...]}"""

    def embed(self, texts: list[str]) -> list[list[float]] | None:
        return None  # embeddings são opcionais; memória tem fallback vetorial próprio


def _post_json(url: str, payload: dict, headers: dict | None = None, timeout: int | None = None) -> dict:
    """POST com retry/backoff para falhas transitórias de rede (5xx/URLError).

    Estratégia: até 3 tentativas; espera 0.5s, 1s (backoff exponencial simples).
    HTTPError 4xx não é retryável (erro do pedido em si).
    Timeout por tentativa: config.llm_timeout via `set_llm_timeout` (padrão 300s);
    timeouts são retryáveis (modelo local pode estar carregando na primeira chamada).
    Conexão recusada NUNCA é retryável: o serviço está comprovadamente morto e
    repetir só trava threads de busca/RAG (orçamento de latência importa)."""
    last_err: Exception | None = None
    to = timeout if timeout is not None else _LLM_TIMEOUT[0]
    for attempt in range(3):
        req = urllib.request.Request(
            url, data=json.dumps(payload).encode(),
            headers={"Content-Type": "application/json", **(headers or {})}, method="POST")
        try:
            with urllib.request.urlopen(req, timeout=to) as resp:
                return json.loads(resp.read().decode())
        except urllib.error.HTTPError as e:
            # 4xx = erro real do pedido (não adianta repetir); 5xx = servidor ocupado/caiu → retry
            if 400 <= e.code < 500:
                raise
            last_err = e
        except urllib.error.URLError as e:
            if isinstance(getattr(e, "reason", None), ConnectionRefusedError):
                raise   # host morto: sem retry — cai no fallback determinístico na hora
            last_err = e
        except (TimeoutError, socket.timeout) as e:
            # modelo lento ainda está trabalhando; vale tentar de novo
            last_err = e
        time.sleep(0.5 * (2 ** attempt))
    assert last_err is not None
    raise last_err


# timeout efetivo das chamadas de LLM (mutável via set_llm_timeout p/ injetar config)
_LLM_TIMEOUT = [300]


def set_llm_timeout(seconds: int) -> None:
    """Define o timeout por tentativa das chamadas ao LLM (chat/embed)."""
    try:
        _LLM_TIMEOUT[0] = max(5, int(seconds))
    except (TypeError, ValueError):
        pass


def _get_json(url: str, headers: dict | None = None, timeout: int = 10) -> dict:
    req = urllib.request.Request(url, headers=headers or {})
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return json.loads(resp.read().decode())


# ---------------------------------------------------------------- ollama (local, offline-capable)
class OllamaProvider(Provider):
    id = "ollama"

    def _base(self) -> str:
        return self.config.ollama_host.rstrip("/")

    def available(self) -> bool:
        try:
            return bool(self.list_models())
        except Exception:
            return False

    def list_models(self) -> list[str]:
        data = _get_json(f"{self._base()}/api/tags", timeout=3)
        return [m["name"] for m in data.get("models", [])]

    def chat(self, messages: list[dict], tools: list[dict] | None) -> dict:
        model = self.config.model or self._pick_chat_model()
        payload = {"model": model, "messages": messages, "stream": False,
                   "options": {"temperature": self.config.temperature}}
        if tools:
            payload["tools"] = [{"type": "function", "function": t["function"]} for t in tools]
        data = _post_json(f"{self._base()}/api/chat", payload)
        msg = data.get("message", {})
        calls = msg.get("tool_calls") or []
        return {"role": "assistant", "content": msg.get("content", ""),
                "tool_calls": [{"id": f"ollama_{i}", "type": "function",
                                "function": {"name": c["function"]["name"],
                                             "arguments": json.dumps(c["function"].get("arguments", {}))}}
                               for i, c in enumerate(calls)]}

    def embed(self, texts: list[str]) -> list[list[float]] | None:
        try:
            out = []
            for t in texts:
                d = _post_json(f"{self._base()}/api/embeddings",
                               {"model": self.config.embed_model, "prompt": t})
                out.append(d["embedding"])
            return out
        except Exception:
            return None

    def _pick_chat_model(self) -> str:
        models = self.list_models()
        hint = self.config.chat_hint.lower()
        for m in models:
            if hint in m.lower():
                return m
        prefer = ("coder", "qwen", "llama", "mistral")
        for p in prefer:
            for m in models:
                if p in m.lower():
                    return m
        return models[0] if models else ""


# ---------------------------------------------------------------- openai-compatível (OpenAI / OpenRouter)
class OpenAICompatProvider(Provider):
    def __init__(self, config: Config, id: str, base_url: str, key: str | None):
        super().__init__(config)
        self.id = id
        self.base_url = base_url
        self._key = key

    @property
    def api_key(self) -> str | None:
        return self._key or self.config.api_key

    def available(self) -> bool:
        return bool(self.api_key)

    def list_models(self) -> list[str]:
        try:
            data = _get_json(f"{self.base_url}/models",
                             {"Authorization": f"Bearer {self.api_key}"}, timeout=8)
            return [m["id"] for m in data.get("data", [])][:50]
        except Exception:
            return []

    def chat(self, messages: list[dict], tools: list[dict] | None) -> dict:
        payload = {"model": self.config.model or "gpt-4o-mini", "messages": messages,
                   "temperature": self.config.temperature, "max_tokens": self.config.max_tokens}
        if tools:
            payload["tools"] = tools
            payload["tool_choice"] = "auto"
        try:
            data = _post_json(f"{self.base_url}/chat/completions", payload,
                              {"Authorization": f"Bearer {self.api_key}"})
        except urllib.error.HTTPError as e:
            raise LLMError(f"HTTP {e.code}: {e.read().decode(errors='replace')[:300]}") from e
        except urllib.error.URLError as e:
            raise LLMError(f"rede: {e.reason}") from e
        return data["choices"][0]["message"]


# ---------------------------------------------------------------- registry (UM ativo por vez)
class ProviderRegistry:
    def __init__(self) -> None:
        self._providers: dict[str, Provider] = {}
        self._active_id: str | None = None

    def register(self, provider: Provider) -> None:
        # idempotente por id: boot único (run.py + testes + scheduler) pode chamar
        # build_default várias vezes; duplicatas só multiplicam tentativas de rede
        self._providers[provider.id] = provider

    def all(self) -> list[Provider]:
        return list(self._providers.values())

    def available(self) -> list[Provider]:
        return [p for p in self._providers.values() if p.available()]

    def active(self) -> Provider | None:
        if self._active_id:
            return self._providers.get(self._active_id)
        return None

    def select(self, provider_id: str) -> None:
        if provider_id not in self._providers:
            raise LLMError(f"provider desconhecido: {provider_id}. "
                           f"Disponíveis: {', '.join(self._providers)}")
        self._active_id = provider_id  # troca = um ativo por vez (nunca dois modelos na RAM)

    def build_default(self, config: Config) -> "ProviderRegistry":
        self.register(OllamaProvider(config))
        self.register(OpenAICompatProvider(config, "openai",
                                           config.base_url or "https://api.openai.com/v1", None))
        self.register(OpenAICompatProvider(config, "openrouter",
                                           "https://openrouter.ai/api/v1",
                                           config.openrouter_api_key or None))
        return self


provider_registry = ProviderRegistry()


def bootstrap(config: Config) -> ProviderRegistry:
    set_llm_timeout(getattr(config, "llm_timeout", 300))  # config manda no timeout de chat/embed
    provider_registry.build_default(config)
    return provider_registry


def auto_select(config: Config) -> str | None:
    """Escolhe o melhor provedor SEM benchmark pesado (custo zero primeiro)."""
    avail = provider_registry.available()
    for pid in ("ollama", "openrouter", "openai"):
        for p in avail:
            if p.id == pid:
                provider_registry.select(p.id)
                return p.id
    return None


# ---------------------------------------------------------------- fachadas estáveis
def chat_completion(messages: list[dict], tools: list[dict] | None, config: Config) -> dict:
    p = provider_registry.active()
    if p is not None and not p.available():
        provider_registry._active_id = None      # auto-cura: seleção morta é descartada
        p = None
    if p is None:
        bootstrap(config)
        if auto_select(config) is None:
            raise LLMError("nenhum provedor LLM disponível (ollama/offline-key)")
        p = provider_registry.active()
    return p.chat(messages, tools)


def embed_texts(texts: list[str], config: Config) -> list[list[float]] | None:
    """Embeddings: tenta o provedor ativo, depois qualquer provider com suporte.

    Busca vetorial roda em caminhos sensíveis a latência (RAG por turno, e até
    threads de teste concorrentes). Se nenhum provedor embedding responder em
    ~3s no total, devolvemos None imediatamente — Memory._encode então cai no
    fallback hash determinístico. Provedores locais lentos ainda funcionam no
    chat (timeout grande), mas nunca seguram a busca."""
    candidates = [provider_registry.active()] + provider_registry.available()
    seen: set[str] = set()
    t0 = time.time()
    for p in candidates:
        if p is None or p.id in seen:
            continue
        seen.add(p.id)
        if time.time() - t0 > 3.0:     # orçamento global de latência p/ embeddings
            break
        try:
            vecs = p.embed(texts)
        except Exception:
            vecs = None
        if vecs:
            return vecs
    return None


# ---------------------------------------------------------------- benchmark de modelos
def benchmark(config: Config, probe: str = "Diga apenas OK") -> list[dict]:
    """Mede latência e disponibilidade dos MODELOS identificados no sistema.

    Um modelo por vez na arena — descarregamos entre testes (ollama keep_alive=0).
    Retorna ranking p/ escolha automática do melhor chat model.
    """
    results: list[dict] = []
    for p in provider_registry.available():
        try:
            models = p.list_models()
        except Exception:
            models = []
        for m in models[:8]:  # limita custo da varredura
            prev_model = config.model
            config.model = m
            t0 = time.time()
            ok, err = False, ""
            try:
                r = p.chat([{"role": "user", "content": probe}], None)
                ok = bool((r.get("content") or "").strip() or r.get("tool_calls"))
            except Exception as e:
                err = f"{type(e).__name__}: {str(e)[:120]}"
            dt = round(time.time() - t0, 2)
            config.model = prev_model
            if p.id == "ollama":  # libera VRAM/RAM antes do próximo (um por vez!)
                try:
                    _post_json(f"{config.ollama_host.rstrip('/')}/api/generate",
                               {"model": m, "keep_alive": 0, "prompt": ""}, timeout=5)
                except Exception:
                    pass
            score = (1.0 if ok else 0.0) * max(0.0, 10.0 - min(dt, 9.5))
            results.append({"provider": p.id, "model": m, "ok": ok,
                            "latency_s": dt, "error": err, "score": round(score, 2)})
    results.sort(key=lambda x: -x["score"])
    return results


def apply_benchmark_choice(config: Config, results: list[dict]) -> str | None:
    """Persiste a escolha do melhor modelo chat nos primeiros colocados.

    Reordena por (score desc, latência asc) para garantir que o vencedor seja o
    modelo de maior score e, em empate, o mais rápido — não depende da ordem
    em que o caller passou a lista.
    """
    good = sorted((r for r in results if r["ok"]),
                  key=lambda r: (-r.get("score", 0), r.get("latency_s", 9e9)))
    if not good:
        return None
    best = good[0]
    provider_registry.select(best["provider"])
    config.model = best["model"]
    config.save()
    return f"{best['provider']}:{best['model']} ({best['latency_s']}s)"
