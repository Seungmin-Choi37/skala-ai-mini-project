"""LLM_BACKEND=openai — 실제 OpenAI API 백엔드 (fake_llm/ollama_llm과 동일 인터페이스).

fast(기본 gpt-4o-mini)는 분류·계획·단순 대화, quality(기본 gpt-4o)는 안내문 작성 전담.
⚠️ 실제 과금이 발생하는 원격 API다.

- 모든 호출은 httpx.AsyncClient로 한다 — 응답을 기다리는 동안 서버의 다른 요청이 멈추지 않는다.
- 사용자에게 보이는 글(대화 답변·안내문)은 stream=true로 받아 토큰이 오는 대로 화면에 흘린다.
  분류·계획(JSON)은 부분 출력이 의미 없으므로 한 번에 받는다.
- 구조화 출력은 `response_format={"type": "json_object"}`(JSON 모드)로 강제한다 — 스키마까지
  강제하지는 않으므로 프롬프트에 필드를 명시하고 agent/prompts.py의 코드 보정을 거친다.

사전 조건: 유효한 `OPENAI_API_KEY` 환경변수(.env).
"""
import json
import os

import httpx
from langsmith import traceable

from . import prompts

OPENAI_URL = os.environ.get("OPENAI_URL", "https://api.openai.com/v1/chat/completions")


class OpenAILLM:
    def __init__(self, model: str, usd_per_1k_tokens: float):
        self.name = f"openai:{model}"
        self.model = model
        self.usd_per_1k = usd_per_1k_tokens
        self.api_key = os.environ.get("OPENAI_API_KEY", "")

    def _headers(self) -> dict:
        if not self.api_key:
            raise RuntimeError(
                "OPENAI_API_KEY가 설정되지 않았습니다 — .env 또는 환경변수로 지정하세요."
            )
        return {"Authorization": f"Bearer {self.api_key}", "Content-Type": "application/json"}

    def _payload(self, system: str, user: str) -> dict:
        return {"model": self.model,
                "messages": [{"role": "system", "content": system},
                             {"role": "user", "content": user}],
                "temperature": 0}

    def _usage(self, tokens: int, system: str, user: str) -> dict:
        return {"model": self.name, "tokens": tokens,
                "usd": round(tokens / 1000 * self.usd_per_1k, 6),
                "prompt": f"[SYSTEM]\n{system}\n\n[USER]\n{user}"}

    async def _chat(self, system: str, user: str, json_mode: bool = False) -> tuple[str, dict]:
        """한 번에 받는 호출 — 분류·계획(JSON)용."""
        payload = self._payload(system, user)
        if json_mode:
            payload["response_format"] = {"type": "json_object"}
        async with httpx.AsyncClient(timeout=120.0) as client:
            resp = await client.post(OPENAI_URL, json=payload, headers=self._headers())
        resp.raise_for_status()
        data = resp.json()
        content = data["choices"][0]["message"]["content"]
        tokens = data.get("usage", {}).get("total_tokens", 0)
        return content, self._usage(tokens, system, user)

    async def _chat_stream(self, system: str, user: str, on_token=None) -> tuple[str, dict]:
        """스트리밍 호출 — 'data: {...}' 줄마다 delta.content를 받아 on_token으로 바로 넘긴다.
        stream_options.include_usage를 켜면 마지막 조각에 토큰 사용량이 실려 온다."""
        payload = {**self._payload(system, user), "stream": True,
                   "stream_options": {"include_usage": True}}
        chunks: list[str] = []
        tokens = 0
        async with httpx.AsyncClient(timeout=120.0) as client:
            async with client.stream("POST", OPENAI_URL, json=payload,
                                     headers=self._headers()) as resp:
                if resp.status_code >= 400:
                    await resp.aread()
                    resp.raise_for_status()
                async for line in resp.aiter_lines():
                    if not line.startswith("data:"):
                        continue
                    body = line[len("data:"):].strip()
                    if body == "[DONE]":
                        break
                    data = json.loads(body)
                    if data.get("usage"):
                        tokens = data["usage"].get("total_tokens", 0)
                    for choice in data.get("choices") or []:
                        piece = (choice.get("delta") or {}).get("content") or ""
                        if piece:
                            chunks.append(piece)
                            if on_token is not None:
                                on_token(piece)
        return "".join(chunks), self._usage(tokens, system, user)


class OpenAIFastLLM(OpenAILLM):
    """분류·계획·단순 대화 전담 (저비용 모델, 기본 gpt-4o-mini)."""

    @traceable(run_type="llm", name="openai_fast.route")
    async def route(self, query: str) -> tuple[dict, dict]:
        system = prompts.ROUTE_SYSTEM + " " + prompts.ROUTE_JSON_HINT
        content, usage = await self._chat(system, query, json_mode=True)
        try:
            raw = json.loads(content)
            route = raw.get("route") if raw.get("route") in ("chat", "safety") else None
            result = {"route": route or prompts.keyword_route(query),
                      "needs_analysis": bool(raw.get("needs_analysis", False))}
        except (json.JSONDecodeError, AttributeError):
            result = {"route": prompts.keyword_route(query), "needs_analysis": False}
        return result, usage

    @traceable(run_type="llm", name="openai_fast.plan")
    async def plan(self, query: str, procedure_prompt: str) -> tuple[dict, dict]:
        system = prompts.plan_system() + " " + prompts.PLAN_JSON_HINT
        user = f"## 표준 절차\n{procedure_prompt}\n\n## 사용자 요청\n{query}"
        content, usage = await self._chat(system, user, json_mode=True)
        try:
            raw = json.loads(content)
        except json.JSONDecodeError:
            raw = {"needs_analysis": False, "tasks": []}
        return prompts.normalize_plan(raw, query), usage

    @traceable(run_type="llm", name="openai_fast.chat")
    async def chat(self, query: str, history: list[dict], on_token=None) -> tuple[str, dict]:
        user = prompts.chat_user(query, history)
        return await self._chat_stream(prompts.CHAT_SYSTEM, user, on_token=on_token)


class OpenAIQualityLLM(OpenAILLM):
    """안내문 작성 전담 (고품질 모델, 기본 gpt-4o)."""

    @traceable(run_type="llm", name="openai_quality.write")
    async def write(self, query: str, collected: list[dict], attempt: int,
                    feedback: str | None, on_token=None) -> tuple[str, dict]:
        user = prompts.write_user(query, collected, feedback)
        return await self._chat_stream(prompts.WRITE_SYSTEM, user, on_token=on_token)