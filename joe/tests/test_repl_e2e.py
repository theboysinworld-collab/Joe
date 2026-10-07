"""Testes E2E do REPL: injeta linhas via stdin e percorre os comandos slash.

Usa monkeypatch de input() para simular digitação real, cobrindo o laço
principal de cli.repl() (18% -> alto) com boot completo offline.
"""

import io
import sys

import pytest

from joe import cli


@pytest.fixture()
def fake_stdin(monkeypatch):
    """Simula um usuário digitando uma sequência de linhas no REPL."""
    def _feed(lines):
        it = iter(lines)
        monkeypatch.setattr("builtins.input", lambda *a, **k: next(it))
        # captura stdout para não poluir o teste
        buf = io.StringIO()
        monkeypatch.setattr(sys, "stdout", buf)
        return buf
    return _feed


def test_repl_percorre_comandos_essenciais(fake_stdin, tmp_path, monkeypatch):
    monkeypatch.setenv("JOE_HOME", str(tmp_path / "joe_home"))
    buf = fake_stdin([
        "oi joe",                       # linha natural -> pipeline offline
        "/tools",                       # lista ferramentas
        "/graph",                       # renderiza grafo central
        "/remember meu nome é Ana",     # memória persistente
        "/recall nome",                 # busca semântica
        "/rag ana",                     # RAG híbrido
        "/predict",                     # previsão de necessidades
        "/analyze",                     # auto-análise geral
        "/heal",                        # auto-cura
        "/evolve",                      # auto-evolução
        "/sub pesquisador pesquise python",  # sub-agente no grafo
        "/suggest",                     # proatividade manual
        "/health",                      # diagnóstico
        "/ext",                         # extensões
        "/provider",                    # provedores ativos
        "/bench",                       # benchmark de modelos (offline: aviso)
        "/web python 3.13",             # busca com fallback offline gracioso
        "/speak olá mundo",             # capacidade de fala (fallback TTS ausente)
        "/see tela",                    # visão de tela (fallback sem display)
        "/comandos",                    # ajuda
        "",                             # linha vazia
        "/exit",                        # encerra
    ])
    rc = cli.repl()
    out = buf.getvalue()
    assert rc == 0
    assert "até logo" in out
    # efeitos reais aconteceram durante o REPL:
    assert "Ana" in out or "ana" in out.lower()   # remember/recall/rag responderam
    assert "ferramenta" in out.lower() or "tools" in out.lower()


def test_repl_newext_e_uso_no_mesmo_turno(fake_stdin, tmp_path, monkeypatch):
    """Requisito: criar módulo novo sem tocar o núcleo e usá-lo imediatamente."""
    monkeypatch.setenv("JOE_HOME", str(tmp_path / "joe_home2"))
    buf = fake_stdin([
        "/newext piada",                # cria extensão drop-in
        "conta uma piada",              # intenção nova -> ferramenta criada em vigor já
        "/exit",
    ])
    rc = cli.repl()
    assert rc == 0
    out = buf.getvalue().lower()
    assert "piada" in out  # criação confirmada na saída


def test_repl_comandos_de_configuracao(fake_stdin, tmp_path, monkeypatch):
    """Ramos de configuração da UI: chave OpenRouter, modelo, provedor, esquecimento."""
    monkeypatch.setenv("JOE_HOME", str(tmp_path / "joe_home_cfg"))
    buf = fake_stdin([
        "/config openrouter sk-or-teste-123",   # requisito: chave via interface
        "/model qwen2.5-coder:1.5b",            # modelo preferido
        "/provider ollama",                     # troca limpa (um por vez)
        "/remember gosto de café",
        "/forget café",                         # remove o fato criado acima
        "/rag café",                            # índice vazio após forget → gracioso
        "/see camera",                          # rota câmera (fallback sem hardware)
        "/phone notificar oi",                  # capacidade celular (fallback)
        "/image um gato robô",                  # geração de imagem (fallback)
        "/xyzdesconhecido",                     # rota de comando inválido
        "/exit",
    ])
    rc = cli.repl()
    out = buf.getvalue().lower()
    assert rc == 0
    assert "openrouter" in out          # confirmação da chave salva
    assert "qwen" in out or "modelo" in out
    assert "desconhecido" in out        # tratamento gracioso de comando inválido


def test_main_subcomandos_cli(fake_stdin, tmp_path, monkeypatch):
    """Cobertura dos subcomandos argv: ask/config/memory/health/bench/repl."""
    home = str(tmp_path / "joe_home_main")
    monkeypatch.setenv("JOE_HOME", home)
    buf = fake_stdin(["/exit"])

    assert cli.main(["ask", "oi joe"]) == 0
    assert cli.main(["config", "model", "qwen2.5-coder:1.5b"]) == 0
    assert cli.main(["config", "openrouter", "sk-or-abc"]) == 0
    assert cli.main(["config", "custom_key", "valor"]) == 0
    assert cli.main(["memory", "add", "gosto", "de", "música"]) == 0
    assert cli.main(["memory", "list"]) == 0
    assert cli.main(["memory", "search", "música"]) == 0
    assert cli.main(["health"]) == 0
    cli.main([])                        # sem args -> cai no REPL (já simulado com /exit)
    out = buf.getvalue().lower()
    assert "qwen" in out or "modelo" in out   # config model respondeu
    assert "memorizado" in out or "fato" in out  # memory add respondeu
