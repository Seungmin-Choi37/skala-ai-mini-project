"""pytest 공통 설정 — 테스트는 인터넷·API 키 없이, 항상 같은 결과가 나오게 돌린다.

- LLM은 fake, 데이터는 sample 모드, KOSHA는 끈다 (.env에 실제 키가 있어도 쓰지 않는다.
  python-dotenv는 이미 정해진 환경변수를 덮어쓰지 않으므로 여기서 먼저 정해 두면 된다)
- 영구 캐시(lookup_cache.db)는 끈다 — 캐시 테스트는 임시 폴더에 따로 만든다
- RAG 벡터 DB는 로컬 임베딩으로, 임시 폴더에 만든다
- LangSmith 추적은 끈다
"""
import asyncio
import os
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

os.environ["LLM_BACKEND"] = "fake"
os.environ["CHEM_DATA_MODE"] = "sample"
os.environ["KOSHA_ENABLED"] = "0"
os.environ["CHEM_CACHE_TTL_DAYS"] = "0"
os.environ["LANGCHAIN_TRACING_V2"] = "false"
os.environ["LANGSMITH_TRACING"] = "false"
os.environ["SUPERVISOR_VERSION"] = "v2"
os.environ["EMBEDDING_BACKEND"] = "local"     # RAG 벡터 DB는 로컬 임베딩으로 (키·인터넷 없이)
os.environ["VECTOR_DB_PATH"] = str(Path(tempfile.mkdtemp()) / "vector_store.db")

import pytest  # noqa: E402

import mcp_server  # noqa: E402
from agent.deps import SupervisorDeps  # noqa: E402
from agent.fake_llm import FastLLM, QualityLLM  # noqa: E402
from agent.graph import build_graph  # noqa: E402


class InProcessMCP:
    """MCPClient와 같은 메서드를 가진 가짜 — subprocess 없이 mcp_server.py 함수를 바로 부른다.
    그래프 테스트를 빠르게 돌리기 위한 것이고, 진짜 stdio 연결은 test_server.py가 확인한다."""

    def __init__(self):
        self.calls: list[tuple[str, dict]] = []

    async def find_prompt(self, name):
        return "chem"

    async def get_prompt(self, name, arguments, server="chem"):
        return mcp_server.safety_notice_prompt(**arguments)

    async def call_tool(self, name, arguments, server="chem"):
        self.calls.append((name, arguments))
        return {"get_substance_safety": mcp_server.get_substance_safety,
                "get_kosha_msds": mcp_server.get_kosha_msds,
                "search_knowledge": mcp_server.search_knowledge}[name](**arguments)

    async def find_resource_template(self, name):
        return "chem", "safety-guide://hazard/{hazard_type}"

    async def read_resource(self, uri, server="chem"):
        return mcp_server.get_safety_guide(uri.rsplit("/", 1)[-1])


@pytest.fixture
def run_graph():
    """질문 하나를 v2 그래프에 넣고 최종 상태를 돌려주는 함수."""
    def _run(query: str, history: list[dict] | None = None) -> dict:
        deps = SupervisorDeps(fast_llm=FastLLM("fast-stub", 0.00015),
                              quality_llm=QualityLLM("quality-stub", 0.005),
                              mcp=InProcessMCP(), session_store=None, version="v2")
        graph = build_graph(deps)
        return asyncio.run(graph.ainvoke({"query": query, "thread_id": "test",
                                          "history": history or [], "events": [],
                                          "cost_usd": 0.0}))
    return _run


def events_of(state: dict, kind: str) -> list[dict]:
    return [e for e in state["events"] if e["type"] == kind]