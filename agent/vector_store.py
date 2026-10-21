"""RAG용 벡터 DB — 화학 안전 지식 문서를 임베딩해 SQLite에 저장하고, 질문과 의미가 가까운 문단을 찾는다.

- 문서     : data/knowledge/*.md(일반 화학·실험실 안전 지식) + data/safety_guides/*.md(유해성별 취급 지침)
             '# 제목' 아래의 빈 줄로 나뉜 문단 하나가 검색 단위(청크)다. 문서가 짧으므로, 가장 가까운
             문단이 속한 문서의 본문 전체를 근거로 돌려준다(정의 문단과 예시 문단이 나뉘어도 함께 쓰도록).
- 임베딩   : EMBEDDING_BACKEND
               openai : OpenAI text-embedding-3-small (의미 기반, OPENAI_API_KEY 필요)
               local  : 글자 2·3-gram 해시 벡터(차원 512) — 인터넷·키 없이 동작하는 대체용(테스트·fake 모드)
             지정하지 않으면 LLM_BACKEND=openai이고 키가 있을 때 openai, 아니면 local.
- 저장     : VECTOR_DB_PATH(기본 프로젝트 폴더의 vector_store.db, .gitignore 등록).
             문서 내용·임베딩 종류의 해시가 바뀌면 처음 검색할 때 다시 색인한다.
- 검색     : 코사인 유사도 상위 k개 중 임베딩 종류별 최소 점수를 넘는 것만 돌려준다
             — 관련 없는 문서를 억지로 근거로 붙이지 않기 위해서다.

numpy·별도 벡터 DB 서버 없이 표준 라이브러리만 쓴다(문서가 수십 개 규모라 충분히 빠르다).
MCP 서버(mcp_server.py)의 search_knowledge Tool이 이 모듈을 쓴다.
"""
import hashlib
import json
import math
import os
import re
import sqlite3
from pathlib import Path

import httpx

ROOT = Path(__file__).resolve().parent.parent
DOC_DIRS = [ROOT / "data" / "knowledge", ROOT / "data" / "safety_guides"]
DEFAULT_DB = ROOT / "vector_store.db"
# 임베딩 종류별 최소 유사도 — 이보다 낮으면 '관련 문서 없음'으로 본다
MIN_SCORE = {"local-ngram-512": 0.25, "openai:text-embedding-3-small": 0.30}
# 1등 점수의 이 비율보다 낮은 문서는 버린다 — 1등만 관련 있고 나머지는 우연히 겹친 경우를 거른다
RELATIVE_CUTOFF = 0.7


# ── 문서 → 청크 ─────────────────────────────────────────────────────
def load_chunks(dirs: list[Path] | None = None) -> list[dict]:
    chunks = []
    for d in dirs or DOC_DIRS:
        for path in sorted(Path(d).glob("*.md")):
            text = path.read_text(encoding="utf-8").strip()
            m = re.match(r"#\s*(.+)", text)
            title = m.group(1).strip() if m else path.stem
            body = text[m.end():] if m else text
            for i, para in enumerate(p.strip() for p in re.split(r"\n\s*\n", body)):
                if para:
                    chunks.append({"id": f"{path.parent.name}/{path.stem}#{i}",
                                   "doc": f"{path.parent.name}/{path.name}", "title": title,
                                   "text": para, "doc_text": body.strip()})
    return chunks


# ── 임베딩 ──────────────────────────────────────────────────────────
def _normalize(vec: list[float]) -> list[float]:
    norm = math.sqrt(sum(v * v for v in vec)) or 1.0
    return [v / norm for v in vec]


class LocalEmbedder:
    """글자 n-gram을 해시해 고정 차원 벡터로 만든다 — 의미 모델은 아니지만 한국어 표현이 겹치면 가깝게 나온다."""
    name = "local-ngram-512"
    dim = 512

    def embed(self, texts: list[str]) -> list[list[float]]:
        out = []
        for text in texts:
            vec = [0.0] * self.dim
            s = re.sub(r"\s+", " ", text.lower())
            for n in (2, 3):
                for i in range(len(s) - n + 1):
                    gram = s[i:i + n]
                    if gram.strip() and " " not in gram:
                        h = int(hashlib.md5(gram.encode("utf-8")).hexdigest()[:8], 16)
                        vec[h % self.dim] += 1.0
            out.append(_normalize(vec))
        return out


class OpenAIEmbedder:
    name = "openai:text-embedding-3-small"

    def __init__(self, model: str = "text-embedding-3-small"):
        self.model = model
        self.api_key = os.environ.get("OPENAI_API_KEY", "")
        self.url = os.environ.get("OPENAI_EMBED_URL", "https://api.openai.com/v1/embeddings")

    def embed(self, texts: list[str]) -> list[list[float]]:
        if not self.api_key:
            raise RuntimeError("OPENAI_API_KEY가 없어 OpenAI 임베딩을 쓸 수 없습니다")
        resp = httpx.post(self.url, json={"model": self.model, "input": texts},
                          headers={"Authorization": f"Bearer {self.api_key}"}, timeout=60.0)
        resp.raise_for_status()
        data = sorted(resp.json()["data"], key=lambda d: d["index"])
        return [_normalize(d["embedding"]) for d in data]


def default_embedder():
    choice = os.environ.get("EMBEDDING_BACKEND", "").strip().lower()
    if not choice:
        choice = "openai" if (os.environ.get("LLM_BACKEND") == "openai"
                              and os.environ.get("OPENAI_API_KEY")) else "local"
    return OpenAIEmbedder() if choice == "openai" else LocalEmbedder()


# ── 벡터 DB ─────────────────────────────────────────────────────────
class VectorStore:
    def __init__(self, path: str | Path | None = None, embedder=None, dirs: list[Path] | None = None):
        self.path = Path(path or os.environ.get("VECTOR_DB_PATH") or DEFAULT_DB)
        self.embedder = embedder or default_embedder()
        self.dirs = dirs or DOC_DIRS
        with self._conn() as conn:
            conn.execute("CREATE TABLE IF NOT EXISTS meta (key TEXT PRIMARY KEY, value TEXT)")
            conn.execute("CREATE TABLE IF NOT EXISTS chunks (id TEXT PRIMARY KEY, doc TEXT, title TEXT, "
                         "text TEXT, doc_text TEXT, vector TEXT)")

    def _conn(self) -> sqlite3.Connection:
        return sqlite3.connect(self.path, timeout=5)

    def _fingerprint(self, chunks: list[dict]) -> str:
        h = hashlib.sha256(self.embedder.name.encode())
        for c in chunks:
            h.update(c["id"].encode() + c["text"].encode())
        return h.hexdigest()

    def ensure_index(self) -> int:
        """문서나 임베딩 종류가 바뀌었으면 다시 색인한다. 색인된 청크 수를 돌려준다."""
        chunks = load_chunks(self.dirs)
        fp = self._fingerprint(chunks)
        with self._conn() as conn:
            row = conn.execute("SELECT value FROM meta WHERE key='fingerprint'").fetchone()
            if row and row[0] == fp:
                return conn.execute("SELECT COUNT(*) FROM chunks").fetchone()[0]
        vectors = self.embedder.embed([f"{c['title']}\n{c['text']}" for c in chunks]) if chunks else []
        with self._conn() as conn:
            conn.execute("DELETE FROM chunks")
            conn.executemany("INSERT INTO chunks VALUES (?, ?, ?, ?, ?, ?)",
                             [(c["id"], c["doc"], c["title"], c["text"], c["doc_text"], json.dumps(v))
                              for c, v in zip(chunks, vectors)])
            conn.execute("INSERT OR REPLACE INTO meta VALUES ('fingerprint', ?)", (fp,))
            conn.execute("INSERT OR REPLACE INTO meta VALUES ('embedder', ?)", (self.embedder.name,))
        return len(chunks)

    def search(self, query: str, k: int = 3, min_score: float | None = None) -> list[dict]:
        self.ensure_index()
        if min_score is None:
            min_score = MIN_SCORE.get(self.embedder.name, 0.2)
        q = self.embedder.embed([query])[0]
        with self._conn() as conn:
            rows = conn.execute("SELECT id, doc, title, text, doc_text, vector FROM chunks").fetchall()
        scored = []
        for cid, doc, title, text, doc_text, vec in rows:
            score = sum(a * b for a, b in zip(q, json.loads(vec)))
            scored.append({"id": cid, "doc": doc, "title": title, "matched": text, "text": doc_text,
                           "score": round(score, 3)})
        scored.sort(key=lambda r: -r["score"])
        hits, seen_docs = [], set()
        for r in scored:
            if r["score"] < min_score or (hits and r["score"] < hits[0]["score"] * RELATIVE_CUTOFF):
                break
            if r["doc"] in seen_docs:   # 같은 문서의 문단은 가장 가까운 것 하나만
                continue
            seen_docs.add(r["doc"])
            hits.append(r)
            if len(hits) >= k:
                break
        return hits