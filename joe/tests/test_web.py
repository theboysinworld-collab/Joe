"""HTTP behavior for Joe's local browser interface."""

from __future__ import annotations

import json
import socket
import threading
import time
import urllib.error
import urllib.request

from joe.web import create_server
from joe.bus import bus


class FakeAgent:
    def __init__(self):
        self.messages = []

    def chat(self, message):
        self.messages.append(message)
        bus.emit("agent.step", agent="joe", role="assistant", note=message)
        return f"Joe recebeu: {message}"


def _request(server, path, method="GET", payload=None):
    url = f"http://127.0.0.1:{server.server_port}{path}"
    data = json.dumps(payload).encode() if payload is not None else None
    request = urllib.request.Request(url, data=data, method=method)
    if data is not None:
        request.add_header("Content-Type", "application/json")
    try:
        with urllib.request.urlopen(request, timeout=3) as response:
            return response.status, response.read()
    except urllib.error.HTTPError as error:
        try:
            return error.code, error.read()
        finally:
            error.close()


def _request_raw(server, path, body=b"", headers=None):
    url = f"http://127.0.0.1:{server.server_port}{path}"
    request = urllib.request.Request(url, data=body, method="POST")
    for name, value in (headers or {}).items():
        request.add_header(name, value)
    try:
        with urllib.request.urlopen(request, timeout=3) as response:
            return response.status, response.read()
    except urllib.error.HTTPError as error:
        try:
            return error.code, error.read()
        finally:
            error.close()


def _start_server(agent_factory=FakeAgent):
    server = create_server(agent_factory, port=0)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    return server, thread


def _stop_server(server, thread):
    server.shutdown()
    server.server_close()
    thread.join(timeout=3)


def test_web_page_chat_and_reset():
    server, thread = _start_server()
    try:
        status, page = _request(server, "/")
        assert status == 200
        assert b"Joe | Assistente pessoal" in page
        status, body = _request(server, "/api/status")
        assert status == 200
        assert {"provider", "model", "tools"} <= set(json.loads(body))
        status, body = _request(server, "/api/chat", "POST", {"message": "oi"})
        assert status == 200
        assert json.loads(body)["reply"] == "Joe recebeu: oi"
        status, body = _request(server, "/api/reset", "POST", {})
        assert status == 200 and json.loads(body)["ok"] is True
        assert _request(server, "/missing")[0] == 404
    finally:
        _stop_server(server, thread)


def test_web_validates_chat_requests():
    server, thread = _start_server()
    try:
        status, body = _request(server, "/api/chat", "POST", {"message": "  "})
        assert status == 400 and "mensagem" in json.loads(body)["error"]
        status, body = _request(server, "/api/chat", "POST", {"message": "x" * 4001})
        assert status == 413
        status, body = _request(server, "/api/chat", "POST", ["não é objeto"])
        assert status == 400
        status, body = _request(server, "/api/chat", "POST", {"message": 42})
        assert status == 400
    finally:
        _stop_server(server, thread)


def test_web_rejects_malformed_and_oversized_json():
    server, thread = _start_server()
    try:
        assert _request_raw(server, "/api/chat", headers={"Content-Length": "bad"})[0] == 400
        assert _request_raw(server, "/api/chat", headers={"Content-Length": "0"})[0] == 400
        assert _request_raw(server, "/api/chat", b"{")[0] == 400
        assert _request_raw(server, "/api/chat", b"\xff")[0] == 400
        assert _request_raw(server, "/api/chat", headers={"Content-Length": "16385"})[0] == 413
        assert _request_raw(server, "/api/chat", b"[]")[0] == 400
        assert _request_raw(server, "/missing", b"{}")[0] == 404
    finally:
        _stop_server(server, thread)


def test_web_chat_error_and_missing_frontend(monkeypatch):
    import joe.web as web

    class BrokenAgent:
        def chat(self, message):
            raise RuntimeError("deliberately broken")

    server, thread = _start_server(BrokenAgent)
    try:
        status, body = _request(server, "/api/chat", "POST", {"message": "oi"})
        assert status == 500
        assert "não conseguiu" in json.loads(body)["error"]
    finally:
        _stop_server(server, thread)

    monkeypatch.setattr(web.Path, "read_bytes", lambda _: (_ for _ in ()).throw(OSError("missing")))
    server, thread = _start_server()
    try:
        status, body = _request(server, "/")
        assert status == 500 and "não encontrada" in json.loads(body)["error"]
    finally:
        _stop_server(server, thread)


def test_web_flow_endpoint_reports_agent_events():
    server, thread = _start_server()
    try:
        status, body = _request(server, "/api/flow")
        assert status == 200
        payload = json.loads(body)
        assert "flow" in payload
        assert isinstance(payload["flow"], list)
        status, body = _request(server, "/api/chat", "POST", {"message": "oi"})
        assert status == 200
        status, body = _request(server, "/api/flow")
        assert status == 200
        payload = json.loads(body)
        assert payload["flow"]
    finally:
        _stop_server(server, thread)


def test_web_serializes_messages_for_shared_agent():
    class OverlapDetectingAgent:
        def __init__(self):
            self.guard = threading.Lock()
            self.active = 0
            self.overlapped = False

        def chat(self, message):
            with self.guard:
                self.active += 1
                self.overlapped |= self.active > 1
            time.sleep(0.05)
            with self.guard:
                self.active -= 1
            return message

    agent = OverlapDetectingAgent()
    server, thread = _start_server(lambda: agent)
    try:
        results = []
        clients = [threading.Thread(
            target=lambda text=text: results.append(
                _request(server, "/api/chat", "POST", {"message": text})[0]
            )
        ) for text in ("primeiro", "segundo")]
        for client in clients:
            client.start()
        for client in clients:
            client.join(timeout=3)
        assert sorted(results) == [200, 200]
        assert not agent.overlapped
    finally:
        _stop_server(server, thread)


def test_web_shell_approval_round_trip():
    server, thread = _start_server()
    results = []
    worker = threading.Thread(target=lambda: results.append(server.confirm_shell("executar teste")))
    try:
        worker.start()
        approval = None
        for _ in range(50):
            status, body = _request(server, "/api/approval")
            assert status == 200
            approval = json.loads(body)["approval"]
            if approval:
                break
            time.sleep(0.02)
        assert approval is not None
        status, body = _request(server, "/api/approval", "POST", {
            "id": approval["id"], "approved": True,
        })
        assert status == 200 and json.loads(body)["ok"] is True
        worker.join(timeout=3)
        assert results == [True]
    finally:
        if worker.is_alive():
            worker.join(timeout=3)
        _stop_server(server, thread)


def test_web_serve_restores_shell_and_falls_back_to_next_port(monkeypatch, capsys):
    import joe.web as web
    from joe.tools.base import registry

    class Shell:
        confirm_fn = "original"

    class FakeServer:
        server_port = 9001
        confirm_shell = staticmethod(lambda prompt: True)

        def serve_forever(self):
            raise KeyboardInterrupt

        def server_close(self):
            self.closed = True

    shell = Shell()
    fake_server = FakeServer()
    attempts = []

    def create_server(factory, port):
        attempts.append(port)
        if port == 9000:
            raise OSError("port in use")
        return fake_server

    monkeypatch.setattr(registry, "get", lambda name: shell)
    monkeypatch.setattr(web, "create_server", create_server)
    monkeypatch.setattr(web, "_port_is_free", lambda port: False)

    assert web.serve_web(object(), object(), port=9000, open_browser=False) == 0
    assert attempts == [9000, 9001]
    assert shell.confirm_fn == "original"
    assert fake_server.closed
    assert "porta alterada" in capsys.readouterr().out


def test_port_is_free_tracks_bound_socket():
    import joe.web as web

    with socket.socket() as occupied:
        occupied.bind(("127.0.0.1", 0))
        port = occupied.getsockname()[1]
        assert not web._port_is_free(port)
    assert web._port_is_free(port)