"""RAG 벡터 DB — 문서 색인, 의미 검색, 관련 없는 질문 거르기, 문서가 바뀌면 다시 색인."""
import httpx

import mcp_server
from agent import vector_store as vs


def test_load_chunks_has_titles_and_full_doc_text():
    chunks = vs.load_chunks()
    hyg = [c for c in chunks if c["doc"] == "knowledge/hygroscopic.md"]
    assert len(hyg) >= 3 and hyg[0]["title"].startswith("흡습성과 조해성")
    assert all("염화칼슘" in c["doc_text"] for c in hyg)          # 어느 문단이 걸려도 문서 전체를 근거로
    assert any(c["doc"].startswith("safety_guides/") for c in chunks)


def test_search_finds_relevant_docs(tmp_path):
    store = vs.VectorStore(tmp_path / "v.db", vs.LocalEmbedder())
    cases = {"공기 중에 수증기를 흡수하는 물질은?": "knowledge/hygroscopic.md",
             "산과 염기를 섞으면 왜 열이 나?": "knowledge/neutralization_heat.md",
             "흄후드는 언제 써야 해?": "knowledge/fume_hood.md",
             "에테르는 왜 오래 두면 위험해?": "knowledge/peroxide_formers.md",
             "폐액은 어떻게 버려?": "knowledge/waste_segregation.md"}
    for query, doc in cases.items():
        hits = store.search(query)
        assert hits and hits[0]["doc"] == doc, query
        assert hits[0]["score"] >= vs.MIN_SCORE["local-ngram-512"]


def test_search_returns_nothing_for_unrelated(tmp_path):
    store = vs.VectorStore(tmp_path / "v.db", vs.LocalEmbedder())
    assert store.search("오늘 날씨 어때") == []
    assert store.search("고분자 결정화 속도론을 설명해 줘") == []


def test_reindex_when_documents_change(tmp_path):
    docs = tmp_path / "docs"
    docs.mkdir()
    (docs / "a.md").write_text("# 증류\n\n증류는 끓는점 차이로 액체 혼합물을 나누는 방법이다.", encoding="utf-8")
    store = vs.VectorStore(tmp_path / "v.db", vs.LocalEmbedder(), dirs=[docs])
    assert store.ensure_index() == 1
    (docs / "b.md").write_text("# 재결정\n\n재결정은 용해도 차이로 고체를 정제하는 방법이다.", encoding="utf-8")
    assert store.ensure_index() == 2                              # 바뀐 것을 알아채고 다시 색인
    assert store.search("재결정으로 고체 정제하는 방법")[0]["doc"] == "docs/b.md"


def test_openai_embedder_request(monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "test-key")
    seen = {}

    def fake_post(url, json, headers, timeout):
        seen.update(url=url, json=json, auth=headers["Authorization"])
        data = [{"index": i, "embedding": [3.0, 4.0]} for i in range(len(json["input"]))]
        return httpx.Response(200, json={"data": data}, request=httpx.Request("POST", url))

    monkeypatch.setattr(vs.httpx, "post", fake_post)
    vectors = vs.OpenAIEmbedder().embed(["가", "나"])
    assert vectors == [[0.6, 0.8], [0.6, 0.8]]                   # 정규화됨
    assert seen["json"]["model"] == "text-embedding-3-small" and seen["auth"] == "Bearer test-key"


def test_default_embedder_choice(monkeypatch):
    monkeypatch.delenv("EMBEDDING_BACKEND", raising=False)
    monkeypatch.setenv("LLM_BACKEND", "fake")
    assert isinstance(vs.default_embedder(), vs.LocalEmbedder)
    monkeypatch.setenv("LLM_BACKEND", "openai")
    monkeypatch.setenv("OPENAI_API_KEY", "x")
    assert isinstance(vs.default_embedder(), vs.OpenAIEmbedder)


def test_mcp_search_knowledge_tool():
    out = mcp_server.search_knowledge("공기 중에 수증기를 흡수하는 물질은?", k=2)
    assert out["embedder"] == "local-ngram-512"
    assert out["results"][0]["doc"] == "knowledge/hygroscopic.md"
    assert "수산화나트륨" in out["results"][0]["text"]
    assert "흡습성" in mcp_server.get_knowledge_doc("hygroscopic")