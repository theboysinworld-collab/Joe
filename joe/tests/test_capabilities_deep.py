"""Cobertura profunda das capacidades: caminhos de fallback e extração."""
import subprocess
from joe import capabilities as cap


class FakeResult:
    def __init__(self, stdout="", returncode=0, stderr=""):
        self.stdout, self.returncode, self.stderr = stdout, returncode, stderr


def _stub_run(monkeypatch, results=None, create_file=None, rc=None):
    """subprocess.run fake: registra chamados, opcionalmente cria arquivo 'gravado'.

    results/rc podem ser dict keyed pelo executável do comando (cmd[0]) ou valor único.
    """
    calls = []

    def _pick(table, cmd, default):
        if isinstance(table, dict):
            return table.get(str(cmd[0]), default)
        return default if table is None else table

    def fake_run(cmd, **kw):
        calls.append(cmd)
        if create_file and str(cmd[-1]) == str(create_file):
            create_file.write_bytes(b"PNG")
        # resultados podem ser buscados pelo executável (cmd[0]), caminho completo (str(cmd[0])) OU nome base
        table = results or {}
        stdout = ""
        if isinstance(table, dict):
            exe_full = str(cmd[0])
            exe_base = exe_full.split("/")[-1]  # "/x/whisper-cli" → "whisper-cli"
            stdout = table.get(exe_base, table.get(exe_full, table.get(cmd[0], "")))
        else:
            stdout = table or ""
        stderr = _pick(getattr(fake_run, "stderr_map", None), cmd, "boom")
        code = _pick(rc, cmd, 0)
        return FakeResult(stdout=stdout, returncode=code, stderr=stderr)
    monkeypatch.setattr(subprocess, "run", fake_run)
    return calls


# ---------------------------------------------------------------- falar/ouvir
def test_speech_no_engine_fallback(monkeypatch):
    monkeypatch.setattr(cap.shutil, "which", lambda n: None)
    c = cap.SpeechCapability()
    assert not c.available()
    out = c.perceive(text="oi")
    assert "sem motor de voz" in out


def test_speech_espeak(monkeypatch):
    monkeypatch.setattr(cap.shutil, "which", lambda n: "/usr/bin/espeak" if n == "espeak" else None)
    calls = _stub_run(monkeypatch)
    c = cap.SpeechCapability()
    assert c.available()
    out = c.perceive(text="olá mundo")
    assert "falei (via espeak)" in out
    assert calls[0][0] == "espeak"


def test_hearing_unavailable_message(monkeypatch):
    monkeypatch.setattr(cap.shutil, "which", lambda n: None)
    c = cap.HearingCapability()
    assert "indisponíveis" in c.perceive()


def test_hearing_with_tools(monkeypatch, tmp_path):
    wav = tmp_path / "joe_listen.wav"

    def which(n):
        # whisper-cli existe mas whisper não → o código deve resolver p/ whisper-cli
        return {"arecord": "/x/arecord", "whisper-cli": "/x/whisper-cli"}.get(n)

    monkeypatch.setattr(cap.shutil, "which", which)
    monkeypatch.setattr(cap.tempfile, "gettempdir", lambda: str(tmp_path))
    calls = _stub_run(monkeypatch, results={"whisper-cli": "texto transcrito"})
    out = cap.HearingCapability().perceive(seconds=2)
    assert out == "texto transcrito"
    assert any(c[0] == "arecord" for c in calls)
    assert calls[-1][0] == "/x/whisper-cli"  # transcritor realmente usado


# ---------------------------------------------------------------- ver tela/câmera
def test_screen_scrot(monkeypatch, tmp_path):
    png = tmp_path / "shot.png"
    monkeypatch.setattr(cap.shutil, "which", lambda n: "/x/scrot" if n == "scrot" else None)
    _stub_run(monkeypatch, create_file=png)
    out = cap.ScreenCapability().perceive(out_path=str(png))
    assert "screenshot salvo" in out


def test_screen_none_available(monkeypatch):
    monkeypatch.setattr(cap.shutil, "which", lambda n: None)
    out = cap.ScreenCapability().perceive()
    assert "indisponível" in out


def test_camera_ffmpeg(monkeypatch, tmp_path):
    jpg = tmp_path / "cam.jpg"
    monkeypatch.setattr(cap.shutil, "which", lambda n: "/x/ffmpeg" if n == "ffmpeg" else None)
    calls = _stub_run(monkeypatch, create_file=jpg)
    out = cap.CameraCapability().perceive(out_path=str(jpg))
    assert "frame da câmera salvo" in out
    assert calls[0][0] == "ffmpeg"


def test_phone_actions(monkeypatch):
    monkeypatch.setattr(cap.shutil, "which",
                        lambda n: "/x/t" if n and n.startswith("termux") else None)
    _stub_run(monkeypatch, results={"termux-notification-list": '[{"title":"WhatsApp"}]'})
    c = cap.PhoneCapability()
    assert c.available()
    assert "notificação enviada" in c.perceive(action="notificar", title="T", message="M")
    assert "abri" in c.perceive(action="abrir_app", app="chrome")
    assert "sms para" in c.perceive(action="mandar_sms", phone="123", text="oi")
    assert "WhatsApp" in c.perceive(action="ler_notificacoes")
    assert "desconhecida" in c.perceive(action="dança")


# ---------------------------------------------------------------- vídeo
def test_video_usage_hint(monkeypatch):
    monkeypatch.setattr(cap.shutil, "which", lambda n: "/x/ffmpeg" if n == "ffmpeg" else None)
    c = cap.VideoEditCapability()
    assert "uso:" in c.perceive(action="trim", src="", dst="")
    assert "uso:" in c.perceive(action="strange", src="a.mp4", dst="b.mp4")


def test_video_trim_ok_and_fail(monkeypatch):
    monkeypatch.setattr(cap.shutil, "which", lambda n: "/x/ffmpeg" if n == "ffmpeg" else None)
    _stub_run(monkeypatch, rc=0)
    assert "ok →" in cap.VideoEditCapability().perceive(action="trim", src="a.mp4", dst="b.mp4")
    _stub_run(monkeypatch, rc=1)  # novo stub com returncode=1 → caminho de falha
    assert "falhou" in cap.VideoEditCapability().perceive(action="convert", src="a", dst="b")


# ---------------------------------------------------------------- web: extração
def test_web_extract_filters_ads_and_dupes():
    page = (
        '<a href="https://real.example/pagina-um">Primeiro Resultado Real</a>'
        '<a href="https://html.duckduckgo.com/yad.js?id=ad">Anúncio Pago Aqui</a>'
        '<a href="https://duck.co/random">Página do DDG</a>'
        '<a href="https://outro.example/pagina-dois">Segundo Resultado &#233; com acento</a>'
        '<a href="https://dup.example/x">Primeiro Resultado Real</a>'
    )
    import re as re_mod, html as html_mod
    res = cap.WebSearchCapability._extract(page, re_mod, html_mod, 6)
    titles = [r.split("\n")[0] for r in res]
    assert len(res) == 2                      # ads filtrados
    assert "é com acento" in res[1]           # unescape HTML
    assert sum("Primeiro Resultado" in t for t in titles) == 1  # dedupe


def test_web_no_query_and_offline(monkeypatch):
    c = cap.WebSearchCapability()
    assert "informe a busca" in c.perceive(query="")

    def boom(*a, **k):
        raise OSError("no network")
    monkeypatch.setattr(cap.urllib.request, "urlopen", boom)
    out = c.perceive(query="qualquer coisa")
    assert "/rag" in out                      # aviso claro sugerindo recall local
