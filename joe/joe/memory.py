"""Memória unificada do Joe — SQLite + RAG vetorial/semântica, 100% stdlib.

Arquitetura em camadas (cada camada tem fallback — funciona sem internet):
1. FATO:      memórias explícitas ("o usuário prefere X") — tabela facts.
2. EPISÓDIO:  toda interação fica no audit log → tabela events (alimenta auto-análise).
3. SEMÂNTICA: busca por texto normalizado (FTS simples) sobre tudo.
4. VETORIAL:  embeddings por provider (nomic-embed-text via ollama) OU, sem LLM,
              hashing determinístico de bigramas (fallback offline que mantém a
              RAG funcionando). Similaridade = cosseno. ChromaDB é opcional: se o
              pacote `chromadb` existir, um provider pode usá-lo; o núcleo não depende.
5. HÁBITOS:   contagem de intenções/ferramentas por hora do dia → previsão proativa.

Migração automática: memory.json legado é importado na primeira execução.
"""

from __future__ import annotations

import hashlib
import json
import math
import re
import sqlite3
import time
from pathlib import Path

from .config import DB_PATH, MEMORY_PATH, Config

_STOP = set("a o e u as os um uma de do da dos das em no na nos nas para por com que "
             "seu sua isso essa esse aqui ali hoje agora voce você mim moi me te "
             "eu ele ela eles elas tu nos vcs".split())


def _norm(text: str) -> list[str]:
    words = re.findall(r"[\wÀ-ÿ]+", text.lower())
    return [w for w in words if w not in _STOP and len(w) > 1]


def _hash_vector(text: str, dim: int = 256) -> list[float]:
    """Embedding determinístico offline: tokens+bigramas → buckets com peso.

    Usa contagem única (set) para que a similaridade seja independente do
    tamanho do texto — um parágrafo e uma frase sobre o mesmo assunto têm
    interseção de tokens alta, não diluída pela repetição.
    """
    vec = [0.0] * dim
    toks = _norm(text)
    grams = set(toks) | {f"{a}_{b}" for a, b in zip(toks, toks[1:])}
    for g in grams:
        h = int(hashlib.md5(g.encode()).hexdigest(), 16)
        vec[h % dim] += 1.0
        vec[(h >> 8) % dim] += 0.5
    n = math.sqrt(sum(v * v for v in vec)) or 1.0
    return [v / n for v in vec]


def cosine(a: list[float], b: list[float]) -> float:
    m = min(len(a), len(b))
    return sum(a[i] * b[i] for i in range(m))


class Memory:
    def __init__(self, db_path: Path = DB_PATH, config: Config | None = None):
        self.config = config or Config.load()
        self.db_path = Path(db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        try:
            self.conn = sqlite3.connect(str(self.db_path), check_same_thread=False)
            self._init_schema()
        except sqlite3.DatabaseError:
            # auto-cura na abertura: banco ilegível → backup .bak + recriação limpa
            try:
                self.conn.close()
            except Exception:
                pass
            bak = self.db_path.with_name(self.db_path.stem + ".db.bak")
            if bak.exists():
                bak.unlink()
            self.db_path.rename(bak)
            self.conn = sqlite3.connect(str(self.db_path), check_same_thread=False)
            self._init_schema()
        # lock de escrita (reentrante): scheduler roda em thread daemon e REPL/
        # sub-agentes na main — sqlite não serializa conexões compartilhadas.
        import threading as _th
        self._wlock = _th.RLock()
        self._migrate_legacy()

    # ------------------------------------------------------------ schema
    def _init_schema(self) -> None:
        c = self.conn
        c.executescript("""
        CREATE TABLE IF NOT EXISTS facts(
            id INTEGER PRIMARY KEY, text TEXT, tag TEXT, ts REAL,
            embedding BLOB, source TEXT DEFAULT 'user');
        CREATE TABLE IF NOT EXISTS events(
            id INTEGER PRIMARY KEY, topic TEXT, payload TEXT, ts REAL);
        CREATE TABLE IF NOT EXISTS docs(
            id INTEGER PRIMARY KEY, path TEXT, chunk TEXT, ts REAL, embedding BLOB);
        CREATE TABLE IF NOT EXISTS habits(
            key TEXT, hour INTEGER, count INTEGER, last_ts REAL,
            PRIMARY KEY(key, hour));
        """)
        c.commit()

    def _migrate_legacy(self) -> None:
        if not MEMORY_PATH.exists():
            return
        try:
            data = json.loads(MEMORY_PATH.read_text(encoding="utf-8"))
            for f in data.get("facts", []):
                self.remember(f["text"], tag=f.get("tag", "geral"), source="legacy")
            MEMORY_PATH.rename(MEMORY_PATH.with_suffix(".json.bak"))
        except Exception:
            pass

    # ------------------------------------------------------------ codecs
    def _encode(self, texts: list[str]) -> list[list[float]]:
        from .llm import embed_texts  # late import: evita ciclo
        vecs = embed_texts(texts, self.config)
        if vecs and len(vecs) == len(texts):
            return vecs
        return [_hash_vector(t) for t in texts]

    @staticmethod
    def _blob(vec: list[float]) -> bytes:
        return json.dumps(vec).encode()

    @staticmethod
    def _unvec(blob) -> list[float] | None:
        if not blob:
            return None
        try:
            return json.loads(bytes(blob).decode())
        except Exception:
            return None

    # ------------------------------------------------------------ escrita
    def remember(self, text: str, tag: str = "geral", source: str = "user") -> int:
        vec = self._encode([text])[0]   # fora do lock: pode tocar rede (ollama)
        text = text.strip()
        with self._wlock:
            cur = self.conn.execute(
                "SELECT id FROM facts WHERE lower(text)=lower(?)", (text,))
            if row := cur.fetchone():  # atualiza em vez de duplicar
                self.conn.execute("UPDATE facts SET tag=?, ts=?, embedding=? WHERE id=?",
                                  (tag, time.time(), self._blob(vec), row[0]))
                self.conn.commit()
                return row[0]
            cur = self.conn.execute(
                "INSERT INTO facts(text,tag,ts,embedding,source) VALUES(?,?,?,?,?)",
                (text, tag, time.time(), self._blob(vec), source))
            self.conn.commit()
            return cur.lastrowid

    def log_event(self, topic: str, payload: dict) -> None:
        with self._wlock:
            self.conn.execute("INSERT INTO events(topic,payload,ts) VALUES(?,?,?)",
                              (topic, json.dumps(payload, ensure_ascii=False, default=str), time.time()))
            self.conn.execute("INSERT INTO habits(key,hour,count,last_ts) VALUES(?,?,1,?) "
                              "ON CONFLICT(key,hour) DO UPDATE SET count=count+1,last_ts=excluded.last_ts",
                              (topic, int(time.localtime().tm_hour), time.time()))
            self.conn.commit()

    def index_file(self, path: str, max_chunks: int = 40) -> int:
        with self._wlock:
            p = Path(path)
            if not p.is_file():
                return 0
            try:
                content = p.read_text(encoding="utf-8", errors="replace")
            except Exception:
                return 0
            # chunking com sobreposição de 200 chars (mantém contexto entre blocos)
            size, overlap = 1200, 200
            step = max(1, size - overlap)
            chunks = [content[i:i + size] for i in range(0, len(content), step)][:max_chunks]
            vecs = self._encode(chunks)
            n = 0
            for ch, v in zip(chunks, vecs):
                self.conn.execute(
                    "INSERT INTO docs(path,chunk,ts,embedding) VALUES(?,?,?,?)",
                    (str(p), ch, time.time(), self._blob(v)))
                n += 1
            self.conn.commit()
            return n

    # ------------------------------------------------------------ leitura
    def forget(self, needle: str) -> int:
        with self._wlock:
            cur = self.conn.execute("DELETE FROM facts WHERE lower(text) LIKE ?",
                                    (f"%{needle.lower()}%",))
            self.conn.commit()
            return cur.rowcount

    def facts(self, limit: int = 200) -> list[dict]:
        rows = self.conn.execute(
            "SELECT text,tag,ts,source FROM facts ORDER BY ts DESC LIMIT ?", (limit,)).fetchall()
        return [{"text": r[0], "tag": r[1], "ts": r[2], "source": r[3]} for r in reversed(rows)]

    def search(self, query: str, k: int = 6) -> list[dict]:
        """RAG híbrida: lexical (SQLite LIKE/token overlap) + vetorial (cosseno)."""
        qvec = self._encode([query])[0]
        qt = set(_norm(query))
        hits: dict[str, dict] = {}
        for text, tag, blob in self.conn.execute("SELECT text,tag,embedding FROM facts"):
            score = cosine(qvec, self._unvec(blob) or [])
            lex = len(qt & set(_norm(text))) / (len(qt) or 1)
            s = 0.7 * score + 0.3 * lex
            if s > 0.15:
                hits[f"f:{text}"] = {"kind": "fato", "text": text, "tag": tag, "score": round(s, 3)}
        for path, chunk, blob in self.conn.execute("SELECT path,chunk,embedding FROM docs LIMIT 4000"):
            score = cosine(qvec, self._unvec(blob) or [])
            lex = len(qt & set(_norm(chunk))) / (len(qt) or 1)
            s = max(score, 0.7 * score + 0.3 * lex)
            if s > 0.2:
                key = f"d:{path}:{chunk[:40]}"
                hits[key] = {"kind": "doc", "text": chunk[:300], "tag": path, "score": round(s, 3)}
        out = sorted(hits.values(), key=lambda x: -x["score"])[:k]
        return out

    def recent_events(self, limit: int = 50, topic_prefix: str = "") -> list[dict]:
        q = "SELECT topic,payload,ts FROM events"
        args: tuple = ()
        if topic_prefix:
            q += " WHERE topic LIKE ?"
            args = (f"{topic_prefix}%",)
        q += " ORDER BY ts DESC LIMIT ?"
        rows = self.conn.execute(q, (*args, limit)).fetchall()
        return [{"topic": r[0], "payload": json.loads(r[1]), "ts": r[2]} for r in reversed(rows)]

    # ------------------------------------------------------------ hábitos / previsão
    def observe_habit(self, intent: str) -> None:
        self.log_event(f"intent.{intent}", {})

    def predict(self, max_results: int = 3) -> list[dict]:
        """Prevê necessidades: intenções mais frequentes NESTA hora do dia."""
        hour = int(time.localtime().tm_hour)
        rows = self.conn.execute(
            "SELECT key,count,last_ts FROM habits WHERE hour=? ORDER BY count DESC LIMIT ?",
            (hour, max_results + 5)).fetchall()
        out = []
        for key, count, last in rows:
            intent = key.split(".", 1)[-1]
            if intent in ("chat", "tool_call"):
                continue
            if time.time() - last < 3600:  # só padrões recorrentes, não eco imediato
                continue
            out.append({"intent": intent, "count": count})
            if len(out) >= max_results:
                break
        return out

    # ------------------------------------------------------------ contexto p/ LLM
    def context_block(self, limit: int = 30) -> str:
        fs = self.facts(limit)
        if not fs:
            return ""
        lines = "\n".join(f"- [{f['tag']}] {f['text']}" for f in fs)
        return f"MEMÓRIA PERSISTENTE (SQLite, sessões anteriores):\n{lines}"

    def close(self) -> None:
        self.conn.close()
