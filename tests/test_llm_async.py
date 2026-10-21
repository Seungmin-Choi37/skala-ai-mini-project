"""실모델 백엔드(openai·ollama)의 비동기 호출과 스트리밍 — 가짜 HTTP 응답으로 확인한다.

진짜 API는 부르지 않는다(키·과금·네트워크 없이). httpx.AsyncClient를 가짜 응답을 주는
transport가 붙은 것으로 바꿔 끼운다.
"""
import asyncio
import json

import httpx

from agent import ollama_llm, openai_llm

_RealAsyncClient = httpx.AsyncClient


def _patch_client(monkeypatch, handler, seen):
    def factory(*args, **kwargs):
        async def wrapped(request):
            seen.append(json.loads(request.content or b"{}"))
            return handler(request)
        return _RealAsyncClient(*args, transport=httpx.MockTransport(wrapped), **kwargs)
    monkeypatch.setattr(httpx, "AsyncClient", factory)


def test_openai_streaming_tokens(monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "test-key")
    chunks = ["[사용 전 ", "안전 안내] ", "톨루엔"]
    sse = "".join(f"data: {json.dumps({'choices': [{'delta': {'content': c}}]})}\n\n" for c in chunks)
    sse += f"data: {json.dumps({'choices': [], 'usage': {'total_tokens': 42}})}\n\ndata: [DONE]\n\n"
    seen = []
    _patch_client(monkeypatch, lambda req: httpx.Response(
        200, text=sse, headers={"content-type": "text/event-stream"}), seen)

    llm = openai_llm.OpenAIQualityLLM("gpt-4o", usd_per_1k_tokens=0.005)
    got = []
    text, usage = asyncio.run(llm.write("톨루엔", [], 1, None, on_token=got.append))
    assert got == chunks                      # 토큰이 오는 대로 하나씩 전달됐다
    assert text == "".join(chunks)
    assert usage["tokens"] == 42 and usage["prompt"].startswith("[SYSTEM]")
    assert seen[0]["stream"] is True and seen[0]["stream_options"] == {"include_usage": True}


def test_openai_route_json_mode_and_fallback(monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "test-key")
    replies = iter(['{"route": "chat", "needs_analysis": false}', "형식이 깨진 답"])
    seen = []
    _patch_client(monkeypatch, lambda req: httpx.Response(200, json={
        "choices": [{"message": {"content": next(replies)}}], "usage": {"total_tokens": 7}}), seen)
    llm = openai_llm.OpenAIFastLLM("gpt-4o-mini", usd_per_1k_tokens=0.00015)
    result, usage = asyncio.run(llm.route("안녕"))
    assert result == {"route": "chat", "needs_analysis": False} and usage["tokens"] == 7
    assert seen[0]["response_format"] == {"type": "json_object"}
    result, _ = asyncio.run(llm.route("톨루엔 주의사항"))   # JSON이 깨지면 키워드 규칙으로
    assert result["route"] == "safety"


def test_openai_missing_key_raises(monkeypatch):
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    llm = openai_llm.OpenAIFastLLM("gpt-4o-mini", usd_per_1k_tokens=0.00015)
    try:
        asyncio.run(llm.route("안녕"))
    except RuntimeError as exc:
        assert "OPENAI_API_KEY" in str(exc)
    else:
        raise AssertionError("키가 없으면 실패해야 한다")


def test_ollama_plan_is_async_and_normalized(monkeypatch):
    seen = []
    plan = {"needs_analysis": True, "tasks": [{"agent": "research", "target": "톨루엔"},
                                              {"agent": "research", "target": "지어낸물질"}]}
    _patch_client(monkeypatch, lambda req: httpx.Response(200, json={
        "message": {"content": json.dumps(plan, ensure_ascii=False)},
        "prompt_eval_count": 10, "eval_count": 5}), seen)
    llm = ollama_llm.OllamaFastLLM("qwen3:0.6b", usd_per_1k_tokens=0.00015)
    result, usage = asyncio.run(llm.plan("톨루엔 주의사항", "절차"))
    targets = [t["target"] for t in result["tasks"] if t["agent"] == "research"]
    assert targets == ["toluene"]             # 질문에 없는 물질은 코드가 버린다
    assert usage["tokens"] == 15 and seen[0]["format"]["required"] == ["needs_analysis", "tasks"]


def test_route_calls_do_not_block_each_other(monkeypatch):
    """비동기라서 느린 응답 두 개를 동시에 기다릴 수 있다(동기 httpx.post였다면 차례로 기다림)."""
    monkeypatch.setenv("OPENAI_API_KEY", "test-key")

    async def slow(request):
        await asyncio.sleep(0.3)
        return httpx.Response(200, json={"choices": [{"message": {"content": '{"route": "chat"}'}}]})

    def factory(*args, **kwargs):
        return _RealAsyncClient(*args, transport=httpx.MockTransport(slow), **kwargs)

    monkeypatch.setattr(httpx, "AsyncClient", factory)
    llm = openai_llm.OpenAIFastLLM("gpt-4o-mini", usd_per_1k_tokens=0.00015)

    async def both():
        loop = asyncio.get_running_loop()
        started = loop.time()
        await asyncio.gather(llm.route("a"), llm.route("b"))
        return loop.time() - started

    assert asyncio.run(both()) < 0.55


def test_openai_suggest_drops_bad_cas(monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "test-key")
    content = json.dumps({"candidates": [
        {"name_ko": "n-뷰틸리튬", "name_en": "n-Butyllithium", "cas": "109-72-8"},
        {"name_ko": "가짜", "name_en": "Fake", "cas": "123-45-6"},          # 검증 숫자 틀림
        {"name_ko": "중복", "name_en": "Dup", "cas": "109-72-8"},
        {"name_ko": "CAS 없음", "name_en": "NoCas"}]}, ensure_ascii=False)
    seen = []
    _patch_client(monkeypatch, lambda req: httpx.Response(200, json={
        "choices": [{"message": {"content": content}}], "usage": {"total_tokens": 9}}), seen)
    llm = openai_llm.OpenAIFastLLM("gpt-4o-mini", usd_per_1k_tokens=0.00015)
    cands, usage = asyncio.run(llm.suggest("자연발화 물질", "pyrophoric"))
    assert cands == [{"name": "n-뷰틸리튬", "cas": "109-72-8"}]
    assert seen[0]["response_format"] == {"type": "json_object"}
    assert "H250" in seen[0]["messages"][0]["content"]


def test_openai_knowledge_answer(monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "test-key")
    content = json.dumps({"answer": "흡습성 물질은 공기 중 수분을 흡수합니다.", "substances": [
        {"name_ko": "염화칼슘", "name_en": "Calcium chloride", "cas": "10043-52-4"},
        {"name_ko": "가짜", "name_en": "Fake", "cas": "123-45-6"}]}, ensure_ascii=False)
    seen = []
    _patch_client(monkeypatch, lambda req: httpx.Response(200, json={
        "choices": [{"message": {"content": content}}], "usage": {"total_tokens": 11}}), seen)
    llm = openai_llm.OpenAIQualityLLM("gpt-4o", usd_per_1k_tokens=0.005)
    result, usage = asyncio.run(llm.answer("흡습성 물질은?", [], "점검 반려: 수치 빼기"))
    assert result == {"answer": "흡습성 물질은 공기 중 수분을 흡수합니다.",
                      "substances": [{"name": "염화칼슘", "cas": "10043-52-4"}]}
    assert seen[0]["response_format"] == {"type": "json_object"}
    assert "점검 반려" in seen[0]["messages"][1]["content"]