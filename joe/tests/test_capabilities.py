"""Testes das capacidades sensoriais — camadas de fallback SEMPRE presentes.

Estratégia: monkeypatch em shutil.which / subprocess.run para simular cada
hardware/backend ausente ou presente, sem depender do ambiente real.
"""

import shutil
import subprocess

import pytest

from joe import capabilities as C


@pytest.fixture
def fake_which(monkeypatch):
    """substitui shutil.which por uma função controlada pelo teste."""
    def _set(present: set[str]):
        monkeypatch.setattr(shutil, "which", lambda cmd: f"/usr/bin/{cmd}" if cmd in present else None)
        # capabilities importa `shutil` no topo do módulo → patch no módulo também
        monkeypatch.setattr(C.shutil, "which", lambda cmd: f"/usr/bin/{cmd}" if cmd in present else None)
    return _set


class FakeProc:
    def __init__(self, returncode=0, stdout="", stderr=""):
        self.returncode, self.stdout, self.stderr = returncode, stdout, stderr


# ---------------------------------------------------------------- falar
def test_falar_sem_motor_devolve_texto_nunca_explode(fake_which, monkeypatch):
    fake_which(set())                      # nenhum binário de voz no PATH
    from joe import deps as _d
    monkeypatch.setattr(_d, "_checked", {})
    monkeypatch.setattr(_d, "_installed", {"pyttsx3"})   # sem baixar nada
    monkeypatch.setattr(_d, "_pip_install", lambda *a, **k: False)
    monkeypatch.setitem(__import__("sys").modules, "pyttsx3", None)  # módulo 'quebrado'
    cap = C.SpeechCapability()
    assert not cap.available()
    out = cap.perceive(text="olá mundo")
    assert "olá mundo" in out             # fallback: eco textual (sempre funciona)


def test_falar_com_espeak_chama_subprocess(fake_which, monkeypatch):
    fake_which({"espeak"})
    import glob as _g
    monkeypatch.setattr(_g, "glob", lambda pat: ["/fake"] if "pt" in pat else [])  # voz pt instalada
    calls = []
    monkeypatch.setattr(subprocess, "run", lambda cmd, **kw: calls.append(cmd))
    out = C.SpeechCapability().perceive(text="oi")
    assert calls and calls[0][0] == "espeak" and "-v" in calls[0]
    assert "falei" in out


# ---------------------------------------------------------------- ouvir
def test_ouvir_indisponivel_avisa_claramente(fake_which):
    fake_which(set())
    cap = C.HearingCapability()
    assert not cap.available()
    assert "indispon" in cap.perceive(seconds=1).lower()


# ---------------------------------------------------------------- ver tela
def test_ver_tela_sem_ferramenta_avisa(fake_which, monkeypatch):
    fake_which(set())
    monkeypatch.setattr(C.platform, "system", lambda: "Linux")
    from joe import deps
    monkeypatch.setattr(deps, "ensure_bin", lambda *a, **k: (False, "sem gerenciador"))
    cap = C.ScreenCapability()
    assert not cap.available()
    assert "indispon" in cap.perceive().lower()


def test_ver_tela_scrot_salva_arquivo(fake_which, monkeypatch, tmp_path):
    fake_which({"scrot"})
    out_file = tmp_path / "tela.png"

    def fake_run(cmd, **kw):                     # "scrot" cria o arquivo
        out_file.write_bytes(b"\x89PNG")
        return FakeProc()
    monkeypatch.setattr(subprocess, "run", fake_run)
    res = C.ScreenCapability().perceive(out_path=str(out_file))
    assert out_file.exists() and "screenshot salvo" in res


def test_ver_tela_windows_powershell(fake_which, monkeypatch, tmp_path):
    fake_which({"powershell"})
    monkeypatch.setattr(C.platform, "system", lambda: "Windows")
    out_file = tmp_path / "tela.png"
    calls = []

    def fake_run(cmd, **kw):
        calls.append(cmd)
        out_file.write_bytes(b"\x89PNG")
        return FakeProc()

    monkeypatch.setattr(subprocess, "run", fake_run)
    cap = C.ScreenCapability()
    assert cap.available()
    result = cap.perceive(out_path=str(out_file))
    assert "screenshot salvo" in result
    assert calls[0][0] == "/usr/bin/powershell"
    assert "CopyFromScreen" in calls[0][-1]


def test_capability_status_nao_instala_dependencias(monkeypatch):
    from joe import deps

    calls = []
    monkeypatch.setattr(C.shutil, "which", lambda _: None)
    monkeypatch.setattr(
        deps,
        "ensure",
        lambda module, package=None, *, allow_install=True: calls.append(
            (module, package, allow_install)
        ) or False,
    )
    monkeypatch.setattr(
        deps,
        "ensure_bin",
        lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("não pode instalar")),
    )

    assert not C.SpeechCapability().available()
    assert not C.ImageGenCapability().available()
    assert calls == [("pyttsx3", None, False), ("PIL", "pillow", False)]


# ---------------------------------------------------------------- câmera
def test_camera_sem_backend_avisa(fake_which, monkeypatch):
    fake_which(set())
    from joe import deps
    monkeypatch.delenv("PREFIX", raising=False)
    monkeypatch.setattr(deps, "ensure_bin", lambda *a, **k: (False, "sem gerenciador"))
    cap = C.CameraCapability()
    assert not cap.available()
    assert "indispon" in cap.perceive().lower()


def test_camera_termux_usa_termux_camera_photo(fake_which, monkeypatch, tmp_path):
    fake_which({"termux-camera-photo"})
    path = tmp_path / "cam.jpg"

    def fake_run(cmd, **kw):
        path.write_bytes(b"jpg")
        return FakeProc()
    monkeypatch.setattr(subprocess, "run", fake_run)
    res = C.CameraCapability().perceive(out_path=str(path))
    assert "frame da câmera" in res


# ---------------------------------------------------------------- celular
def test_celular_fora_de_termux_orienta_usuario(fake_which):
    fake_which(set())
    cap = C.PhoneCapability()
    assert not cap.available()
    assert "termux" in cap.perceive(action="status").lower()


def test_celular_notifica_dentro_de_termux(fake_which, monkeypatch):
    fake_which({"termux-notification"})
    calls = []
    monkeypatch.setattr(subprocess, "run",
                        lambda cmd, **kw: calls.append(cmd) or FakeProc(stdout="ok"))
    cap = C.PhoneCapability()
    assert cap.available()
    out = cap.perceive(action="notificar", title="Alerta", message="reunião às 9")
    assert "notificação enviada" in out
    assert "--title" in calls[0] and "Alerta" in calls[0]


def test_celular_acao_desconhecida_reporteia(fake_which):
    fake_which({"termux-notification"})
    out = C.PhoneCapability().perceive(action="dançar")
    assert "desconhecida" in out


# ---------------------------------------------------------------- vídeo
def test_video_sem_ffmpeg_avisa(fake_which, monkeypatch):
    fake_which(set())
    from joe import deps
    monkeypatch.setattr(deps, "ensure_bin", lambda *a, **k: (False, "sem gerenciador"))
    cap = C.VideoEditCapability()
    assert not cap.available()
    assert "ffmpeg" in cap.perceive(action="trim", src="a.mp4", dst="b.mp4")


def test_video_tenta_instalar_ffmpeg_ao_ser_usado(monkeypatch):
    installed = set()
    calls = []
    monkeypatch.setattr(C.shutil, "which", lambda name: f"/x/{name}" if name in installed else None)

    def install(name, **kwargs):
        calls.append(name)
        installed.add(name)
        return True, "instalado"

    monkeypatch.setattr("joe.deps.ensure_bin", install)
    monkeypatch.setattr(subprocess, "run", lambda *a, **k: FakeProc())
    result = C.VideoEditCapability().perceive(action="trim", src="a.mp4", dst="b.mp4")

    assert calls == ["ffmpeg"]
    assert "trim ok" in result


def test_video_trim_monta_comando_correto(fake_which, monkeypatch):
    fake_which({"ffmpeg"})
    cmds = []
    monkeypatch.setattr(subprocess, "run",
                        lambda cmd, **kw: cmds.append(cmd) or FakeProc(returncode=0))
    out = C.VideoEditCapability().perceive(action="trim", src="in.mp4", dst="out.mp4",
                                           start="5", duration="10")
    assert "ok" in out
    assert cmds[0][:3] == ["ffmpeg", "-y", "-i"] and "out.mp4" in cmds[0]


# ---------------------------------------------------------------- registry/boot
def test_bootstrap_registra_as_oito_capacidades():
    from joe.config import Config
    reg = C.bootstrap_capabilities(Config.load())
    names = {c.name for c in reg.all()}
    assert {"falar", "ouvir", "ver_tela", "ver_camera", "celular",
            "criar_imagem", "editar_video", "pesquisar_web"} <= names
    st = reg.status()
    assert all(set(d) == {"capacidade", "disponivel"} for d in st)


def test_extensao_pode_registrar_capacidade_nova_sem_tocar_nucleo():
    class CapAbstrata(C.Capability):
        name = "sentir_temperatura"

        def available(self):
            return True

        def perceive(self, **kw):
            return "23°C (simulado)"

    reg = C.capability_registry
    before = len(reg.all())
    reg.register(CapAbstrata())
    assert reg.get("sentir_temperatura").perceive() == "23°C (simulado)"
    assert len(reg.all()) == before + 1
