"""조회 결과 영구 캐시 (SQLite) — 서버를 다시 켜도 PubChem·KOSHA를 또 부르지 않게 한다.

- 파일 위치 : 환경변수 CHEM_CACHE_PATH (기본: 프로젝트 폴더의 lookup_cache.db, .gitignore에 등록)
- 유효 기간 : 환경변수 CHEM_CACHE_TTL_DAYS (기본 30일). 0이면 캐시를 쓰지 않는다.
- 키       : "pubchem:v2:<조회어 소문자>" 처럼 출처·형식 버전·조회어를 붙인다.
             저장 형식을 바꾸면 버전 숫자만 올리면 옛 캐시는 자연히 무시된다.

MCP 서버(mcp_server.py) 한 프로세스만 쓰지만, 혹시 동시에 열려도 깨지지 않도록
쓰기마다 짧은 연결을 열고 닫는다(SQLite 자체 잠금에 맡긴다).
"""
import json
import os
import sqlite3
import time
from pathlib import Path

DEFAULT_PATH = Path(__file__).resolve().parent.parent / "lookup_cache.db"


class LookupCache:
    def __init__(self, path: str | Path | None = None, ttl_days: float | None = None):
        self.path = Path(path or os.environ.get("CHEM_CACHE_PATH") or DEFAULT_PATH)
        if ttl_days is None:
            ttl_days = float(os.environ.get("CHEM_CACHE_TTL_DAYS", "30"))
        self.ttl = ttl_days * 86400
        if self.enabled:
            with self._conn() as conn:
                conn.execute("CREATE TABLE IF NOT EXISTS cache ("
                             "key TEXT PRIMARY KEY, value TEXT NOT NULL, saved_at REAL NOT NULL)")

    @property
    def enabled(self) -> bool:
        return self.ttl > 0

    def _conn(self) -> sqlite3.Connection:
        return sqlite3.connect(self.path, timeout=5)

    def get(self, key: str) -> dict | None:
        if not self.enabled:
            return None
        try:
            with self._conn() as conn:
                row = conn.execute("SELECT value, saved_at FROM cache WHERE key = ?",
                                   (key,)).fetchone()
        except sqlite3.Error:
            return None   # 캐시는 있으면 좋은 것 — 고장 나도 조회는 계속한다
        if not row or time.time() - row[1] > self.ttl:
            return None
        return json.loads(row[0])

    def set(self, key: str, value: dict) -> None:
        if not self.enabled:
            return
        try:
            with self._conn() as conn:
                conn.execute("INSERT OR REPLACE INTO cache (key, value, saved_at) VALUES (?, ?, ?)",
                             (key, json.dumps(value, ensure_ascii=False), time.time()))
        except sqlite3.Error:
            pass

    def clear(self) -> None:
        if self.enabled:
            with self._conn() as conn:
                conn.execute("DELETE FROM cache")


_DEFAULT: LookupCache | None = None


def default_cache() -> LookupCache:
    """프로세스당 하나 — 환경변수는 처음 쓸 때 읽는다(테스트가 미리 바꿀 수 있게)."""
    global _DEFAULT
    if _DEFAULT is None:
        _DEFAULT = LookupCache()
    return _DEFAULT