"""Local browser interface for Joe, served with the Python standard library."""

from __future__ import annotations

import json
import secrets
import socket
import threading
import webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlsplit

from .bus import bus

MAX_BODY_BYTES = 16_384
MAX_MESSAGE_LENGTH = 4_000
APPROVAL_TIMEOUT = 120


def create_server(agent_factory, host: str = "127.0.0.1", port: int = 8765):
    """Create a loopback web server; agent_factory creates a fresh chat session."""
    state = {"agent": agent_factory(), "approval": None}
    agent_lock = threading.Lock()
    approval_condition = threading.Condition()

    def confirm_shell(prompt: str) -> bool:
        approval = {"id": secrets.token_urlsafe(18), "prompt": prompt, "answer": None}
        with approval_condition:
            if state["approval"] is not None:
                return False
            state["approval"] = approval
            answered = approval_condition.wait_for(
                lambda: approval["answer"] is not None, timeout=APPROVAL_TIMEOUT
            )
            state["approval"] = None
            return bool(answered and approval["answer"])

    class Handler(BaseHTTPRequestHandler):
        def _send(self, body: bytes, content_type: str, status: int = 200) -> None:
            self.send_response(status)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(body)))
            self.send_header("X-Content-Type-Options", "nosniff")
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(body)

        def _json(self, payload: dict, status: int = 200) -> None:
            body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
            self._send(body, "application/json; charset=utf-8", status)

        def _read_json(self):
            try:
                length = int(self.headers.get("Content-Length", "0"))
            except ValueError:
                self._json({"error": "Cabeçalho de tamanho inválido."}, 400)
                return None
            if length <= 0:
                self._json({"error": "Corpo da requisição vazio."}, 400)
                return None
            if length > MAX_BODY_BYTES:
                self._json({"error": "Requisição excede o limite permitido."}, 413)
                return None
            try:
                payload = json.loads(self.rfile.read(length).decode("utf-8"))
            except (UnicodeDecodeError, json.JSONDecodeError):
                self._json({"error": "JSON inválido."}, 400)
                return None
            if not isinstance(payload, dict):
                self._json({"error": "O corpo precisa ser um objeto JSON."}, 400)
                return None
            return payload

        def do_GET(self) -> None:
            path = urlsplit(self.path).path
            if path == "/":
                try:
                    body = Path(__file__).with_name("web_ui.html").read_bytes()
                except OSError:
                    self._json({"error": "Interface web não encontrada."}, 500)
                    return
                self._send(body, "text/html; charset=utf-8")
                return
            if path == "/api/status":
                from .llm import provider_registry
                from .tools.base import registry

                provider = provider_registry.active()
                self._json({
                    "provider": provider.id if provider else "offline-planner",
                    "model": getattr(provider, "model", "") if provider else "",
                    "tools": len(registry.names()),
                })
                return
            if path == "/api/flow":
                events = list(bus.replay[-80:])
                clean = []
                for evt in events:
                    clean.append({
                        "topic": evt.get("topic", "unknown"),
                        "ts": evt.get("ts"),
                        "agent": evt.get("agent"),
                        "role": evt.get("role"),
                        "tool": evt.get("tool"),
                        "note": evt.get("note"),
                        "thought": evt.get("thought"),
                        "ok": evt.get("ok"),
                    })
                self._json({"flow": clean})
                return
            if path == "/api/approval":
                with approval_condition:
                    approval = state["approval"]
                    self._json({"approval": dict(approval) if approval else None})
                return
            self._json({"error": "Rota não encontrada."}, 404)

        def do_POST(self) -> None:
            path = urlsplit(self.path).path
            if path == "/api/chat":
                payload = self._read_json()
                if payload is None:
                    return
                message = payload.get("message")
                if not isinstance(message, str) or not message.strip():
                    self._json({"error": "Escreva uma mensagem antes de enviar."}, 400)
                    return
                message = message.strip()
                if len(message) > MAX_MESSAGE_LENGTH:
                    self._json({"error": "A mensagem excede o limite de 4.000 caracteres."}, 413)
                    return
                try:
                    with agent_lock:
                        answer = state["agent"].chat(message)
                except Exception:
                    self.log_error("Falha ao processar mensagem do chat")
                    self._json({"error": "Joe não conseguiu processar essa mensagem."}, 500)
                    return
                self._json({"reply": str(answer)})
                return
            if path == "/api/reset":
                with agent_lock:
                    state["agent"] = self.server.agent_factory()
                self._json({"ok": True})
                return
            if path == "/api/approval":
                payload = self._read_json()
                if payload is None:
                    return
                with approval_condition:
                    approval = state["approval"]
                    if (approval is None or payload.get("id") != approval["id"]
                            or not isinstance(payload.get("approved"), bool)):
                        self._json({"error": "A confirmação expirou ou não é válida."}, 409)
                        return
                    approval["answer"] = payload["approved"]
                    approval_condition.notify_all()
                self._json({"ok": True})
                return
            self._json({"error": "Rota não encontrada."}, 404)

    server = ThreadingHTTPServer((host, port), Handler)
    server.agent_factory = agent_factory
    server.confirm_shell = confirm_shell
    return server


def serve_web(config, memory, port: int = 8765, open_browser: bool = True) -> int:
    """Start Joe's browser UI on loopback and optionally open the browser."""
    from .agent import Agent
    from .tools.base import registry

    server = None
    selected_port = port
    for candidate in range(port, port + 20):
        try:
            server = create_server(lambda: Agent(config, memory), port=candidate)
            selected_port = candidate
            break
        except OSError:
            if port == 0:
                raise
            if not _port_is_free(candidate):
                continue
            raise
    if server is None:
        raise OSError(f"Não foi possível abrir a interface web em {port}")

    shell = registry.get("bash")
    original_confirm = getattr(shell, "confirm_fn", None)
    if shell is not None:
        shell.confirm_fn = server.confirm_shell
    url = f"http://127.0.0.1:{server.server_port}"
    if selected_port != port:
        print(f"Joe web: {url} (porta alterada automaticamente para {selected_port})", flush=True)
    else:
        print(f"Joe web: {url} (Ctrl+C para encerrar)", flush=True)
    try:
        if open_browser:
            webbrowser.open(url)
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nJoe web encerrado.", flush=True)
    finally:
        server.server_close()
        if shell is not None:
            shell.confirm_fn = original_confirm
    return 0


def _port_is_free(port: int) -> bool:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        try:
            sock.bind(("127.0.0.1", port))
            return True
        except OSError:
            return False
