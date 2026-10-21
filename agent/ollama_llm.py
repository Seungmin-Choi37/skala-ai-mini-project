"""LLM_BACKEND=ollama — 로컬 Ollama 실모델 백엔드 (fake_llm과 동일 인터페이스).

fast(기본 qwen3:0.6b)는 분류·계획·단순 대화, quality(기본 qwen3:8b)는 안내문 작성과
일반 화학 질문(knowledge) 답변 전담.
Ollama의 구조화 출력(format=<JSON Schema>)과 temperature=0으로 변동을 줄이지만 실모델이라
결정론은 아니다 — 그래서 gate·replan·validate가 연출이 아니라 실제 방어선으로 동작한다.
프롬프트와 계획 보정 코드는 openai_llm.py와 공유한다(agent/prompts.py).

모든 호출은 httpx.AsyncClient로 한다 — 모델이 생각하는 동안 서버의 다른 요청(다른 사용자의
질문, /health 등)이 멈추지 않는다. 동기 httpx.post를 쓰면 이벤트 루프 전체가 그동안 막힌다.

비용(cost_usd)은 '가상 단가 × 실측 토큰 수'다 — 로컬 추론이라 실제 과금은 0이다.

사전 조건: `ollama serve` 실행 중 + `ollama pull qwen3:0.6b qwen3:8b`.
"""
import json
import os

import httpx
from langsmith import traceable

from . import chem_rules, prompts

OLLAMA_URL = os.environ.get("OLLAMA_URL", "http://127.0.0.1:11434/api/chat")

ROUTE_SCHEMA = {
    "type": "object",
    "properties": {
        "route": {"type": "string", "enum": ["chat", "safety", "knowledge"]},
        "needs_analysis": {"type": "boolean"},
    },
    "required": ["route", "needs_analysis"],
}

PLAN_SCHEMA = {
    "type": "object",
    "properties": {
        "needs_analysis": {"type": "boolean"},
        "tasks": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "agent": {"type": "string", "enum": ["research", "analysis"]},
                    "target": {"type": "string"},
                    "target_en": {"type": "string"},
                },
                "required": ["agent"],
            },
        },
    },
    "required": ["needs_analysis", "tasks"],
}


SUGGEST_SCHEMA = {
    "type": "object",
    "properties": {
        "candidates": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {"name_ko": {"type": "string"}, "name_en": {"type": "string"},
                               "cas": {"type": "string"}},
                "required": ["cas"],
            },
        },
    },
    "required": ["candidates"],
}


KNOWLEDGE_SCHEMA = {
    "type": "object",
    "properties": {
        "answer": {"type": "string"},
        "substances": SUGGEST_SCHEMA["properties"]["candidates"],
    },
    "required": ["answer", "substances"],
}


class OllamaLLM:
    def __init__(self, model: str, usd_per_1k_tokens: float):
        self.name = f"ollama:{model}"
        self.model = model
        self.usd_per_1k = usd_per_1k_tokens

    async def _chat(self, system: str, user: str, schema: dict | None = None) -> tuple[str, dict]:
        payload = {
            "model": self.model,
            "messages": [{"role": "system", "content": system},
                         {"role": "user", "content": user}],
            "stream": False,
            "options": {"temperature": 0.0},
        }
        if schema is not None:
            payload["format"] = schema
        async with httpx.AsyncClient(timeout=300.0) as client:
            resp = await client.post(OLLAMA_URL, json=payload)
        resp.raise_for_status()
        data = resp.json()
        content = data["message"]["content"]
        tokens = (data.get("prompt_eval_count") or 0) + (data.get("eval_count") or 0)
        usage = {"model": self.name, "tokens": tokens,
                 "usd": round(tokens / 1000 * self.usd_per_1k, 6)}
        return content, usage

    async def _chat_stream(self, system: str, user: str, on_token=None) -> tuple[str, dict]:
        """사용자 대면 텍스트(최종 답변류) 전용 — Ollama NDJSON 스트림을 토큰 단위로
        받아 on_token(조각)을 즉시 호출해 SSE로 실시간 중계할 수 있게 한다. 분류/계획
        등 JSON 스키마 출력(route/plan/analyze)은 부분 출력이 의미가 없어 그대로 _chat을
        쓴다."""
        payload = {
            "model": self.model,
            "messages": [{"role": "system", "content": system},
                         {"role": "user", "content": user}],
            "stream": True,
            "options": {"temperature": 0.0},
        }
        chunks: list[str] = []
        usage = {"model": self.name, "tokens": 0, "usd": 0.0}
        async with httpx.AsyncClient(timeout=300.0) as client:
            async with client.stream("POST", OLLAMA_URL, json=payload) as resp:
                resp.raise_for_status()
                async for line in resp.aiter_lines():
                    if not line:
                        continue
                    data = json.loads(line)
                    piece = data.get("message", {}).get("content", "")
                    if piece:
                        chunks.append(piece)
                        if on_token is not None:
                            on_token(piece)
                    if data.get("done"):
                        tokens = ((data.get("prompt_eval_count") or 0)
                                  + (data.get("eval_count") or 0))
                        usage = {"model": self.name, "tokens": tokens,
                                 "usd": round(tokens / 1000 * self.usd_per_1k, 6)}
        usage["prompt"] = f"[SYSTEM]\n{system}\n\n[USER]\n{user}"
        return "".join(chunks), usage


class OllamaFastLLM(OllamaLLM):
    """분류·계획·단순 대화 전담 (저사양 소형 모델)."""

    @traceable(run_type="llm", name="ollama_fast.route")
    async def route(self, query: str) -> tuple[dict, dict]:
        content, usage = await self._chat(prompts.ROUTE_SYSTEM, query, schema=ROUTE_SCHEMA)
        try:
            raw = json.loads(content)
            route = raw.get("route") if raw.get("route") in prompts.ROUTES else None
            result = {"route": route or prompts.keyword_route(query),
                      "needs_analysis": bool(raw.get("needs_analysis", False))}
        except (json.JSONDecodeError, AttributeError):
            # 소형 모델의 형식 붕괴 대비 — 코드 레벨 fallback
            result = {"route": prompts.keyword_route(query), "needs_analysis": False}
        return result, usage

    @traceable(run_type="llm", name="ollama_fast.plan")
    async def plan(self, query: str, procedure_prompt: str) -> tuple[dict, dict]:
        user = f"## 표준 절차\n{procedure_prompt}\n\n## 사용자 요청\n{query}"
        content, usage = await self._chat(prompts.plan_system(), user, schema=PLAN_SCHEMA)
        try:
            raw = json.loads(content)
        except json.JSONDecodeError:
            raw = {"needs_analysis": False, "tasks": []}
        return prompts.normalize_plan(raw, query), usage

    @traceable(run_type="llm", name="ollama_fast.suggest")
    async def suggest(self, query: str, prop: str) -> tuple[list[dict], dict]:
        spec = chem_rules.PROPERTY_QUERIES[prop]
        content, usage = await self._chat(prompts.suggest_system(spec["label"], spec["codes"]), query,
                                          schema=SUGGEST_SCHEMA)
        try:
            raw = json.loads(content)
        except json.JSONDecodeError:
            raw = {}
        return prompts.normalize_candidates(raw), usage

    @traceable(run_type="llm", name="ollama_fast.chat")
    async def chat(self, query: str, history: list[dict], on_token=None) -> tuple[str, dict]:
        user = prompts.chat_user(query, history)
        return await self._chat_stream(prompts.CHAT_SYSTEM, user, on_token=on_token)


class OllamaQualityLLM(OllamaLLM):
    """안내문 작성 전담 (상대적으로 큰 모델)."""

    @traceable(run_type="llm", name="ollama_quality.write")
    async def write(self, query: str, collected: list[dict], attempt: int,
                    feedback: str | None, on_token=None) -> tuple[str, dict]:
        user = prompts.write_user(query, collected, feedback)
        return await self._chat_stream(prompts.WRITE_SYSTEM, user, on_token=on_token)

    @traceable(run_type="llm", name="ollama_quality.answer")
    async def answer(self, query: str, history: list[dict], feedback: str | None) -> tuple[dict, dict]:
        """일반 화학 질문 — 설명과 언급한 물질(CAS)을 JSON으로 받는다."""
        content, usage = await self._chat(prompts.KNOWLEDGE_SYSTEM,
                                          prompts.knowledge_user(query, history, feedback),
                                          schema=KNOWLEDGE_SCHEMA)
        try:
            raw = json.loads(content)
        except json.JSONDecodeError:
            raw = {}
        return prompts.normalize_knowledge(raw), usage