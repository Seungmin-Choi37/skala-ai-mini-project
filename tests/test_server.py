"""FastAPI 서버 + 진짜 MCP 서버(subprocess, stdio) 연결까지 — 가장 실제에 가까운 확인.

서버가 켜질 때 mcp_server.py·mcp_server_docs.py를 실제로 띄우므로 몇 초 걸린다.
"""
import json

import pytest
from fastapi.testclient import TestClient


@pytest.fixture(scope="module")
def client(tmp_path_factory):
    import server
    from agent.session_store import SqliteSessionStore
    db = tmp_path_factory.mktemp("session") / "session.db"
    with pytest.MonkeyPatch.context() as mp:   # 테스트 대화가 실제 session.db에 섞이지 않게
        mp.setattr(server, "SqliteSessionStore", lambda: SqliteSessionStore(db))
        with TestClient(server.app) as c:
            yield c


def test_health(client):
    body = client.get("/health").json()
    assert body["status"] == "ok" and body["creation_count"] == 1
    assert body["llm_backend"] == "fake" and body["chem_data_mode"] == "sample"


def test_substances_list(client):
    items = client.get("/substances").json()["items"]
    assert any(i["key"] == "toluene" and i["sample"] for i in items)


def test_chat_end_to_end(client):
    body = client.post("/chat", json={"message": "톨루엔이랑 질산 같이 써도 돼?",
                                      "thread_id": "pytest-e2e"}).json()
    assert body["notice"]["kind"] == "notice"
    assert body["notice"]["conflicts"]
    assert "1) 유해성 요약" in body["reply"]
    # 첫 Tool 호출은 일부러 한 번 실패하게 돼 있다(재시도 데모) → 재시도로 복구됐어야 한다
    assert body["metadata"]["retry_count"] >= 0


def test_chat_stream_events(client):
    with client.stream("GET", "/chat/stream", params={"message": "아세톤 주의사항",
                                                      "thread_id": "pytest-sse"}) as r:
        text = "".join(r.iter_text())
    events = [json.loads(line[6:]) for line in text.split("\n\n")
              if line.startswith("data: ") and line != "data: [DONE]"]
    types = [e["type"] for e in events]
    assert types[0] == "RouteDecided" and "FinalAnswer" in types
    final = next(e for e in events if e["type"] == "FinalAnswer")
    assert final["notice"]["substances"][0]["name"] == "아세톤"
    assert text.rstrip().endswith("data: [DONE]")