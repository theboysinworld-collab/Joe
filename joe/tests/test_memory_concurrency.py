"""Testes estruturais de concorrência (varredura staff): corrida real entre REPL/scheduler."""
import threading
from pathlib import Path

from joe.memory import Memory


def _stress(tmp_path: Path, rounds: int = 2) -> tuple[int, int]:
    """8 threads x (remember+search); retorna (erros, fatos)."""
    total_errs = 0
    for r in range(rounds):
        m = Memory(tmp_path / f"mem{r}.db")
        errs: list[str] = []

        def w(i: int) -> None:
            try:
                for j in range(15):
                    m.remember(f"fato {i}-{j} do thread", tag=f"t{i}")
                    m.search("fato")
            except Exception as e:  # noqa: BLE001 - o teste é justamente capturar
                errs.append(repr(e))

        ts = [threading.Thread(target=w, args=(i,)) for i in range(8)]
        [t.start() for t in ts]
        [t.join() for t in ts]
        total_errs += len(errs)
        assert len(m.facts()) == 120, "escritas concorrentes perderam fatos"
        assert m.search("fato"), "RAG não enxerga os fatos gravados em paralelo"
        m.close()
    return total_errs, rounds


def test_sem_corrida_em_scrita_leitura_concorrente(tmp_path):
    """Bug structural da varredura: cursor aberto em search() era invalidado por
    INSERT/COMMIT de outra thread → ValueError intermitente. Snapshot sob RLock corrige."""
    errs, rounds = _stress(Path(tmp_path))
    assert errs == 0, f"{errs} falhas de corrida em {rounds * 240} operações concorrentes"


def test_encode_nao_derruba_com_provider_quebrado(tmp_path, monkeypatch):
    """_encode deve cair no fallback hash quando embed_texts LANÇA exceção
    (provider flutuante em thread), não só quando retorna None."""
    m = Memory(Path(tmp_path) / "mem.db")

    def boom(texts, config):
        raise ConnectionError("ollama caiu no meio da chamada")

    monkeypatch.setattr("joe.llm.embed_texts", boom)
    fid = m.remember("fato com provider quebrado")          # não pode lançar
    hits = m.search("provider quebrado")                     # idem na leitura
    assert fid and any("provider quebrado" in h["text"] for h in hits)
    m.close()
