"""LLM_BACKEND=openai — 실제 OpenAI API 백엔드 (fake_llm/ollama_llm과 동일 인터페이스).

fast(기본 gpt-4o-mini)는 분류·계획·단순 대화, quality(기본 gpt-4o)는 안내문 작성 전담.
⚠️ 실제 과금이 발생하는 원격 API다.

구조화 출력은 `response_format={"type": "json_object"}`(JSON 모드)로 강제한다 — 스키마까지
강제하지는 않으므로 프롬프트에 필드를 명시하고 agent/prompts.py의 코드 보정을 거친다.

사전 조건: 유효한 `OPENAI_API_KEY` 환경변수(.env).
"""
import json
import os

import httpx
from langsmith import traceable

from . import prompts

OPENAI_URL = "https://api.openai.com/v1/chat/completions"


class OpenAILLM:
    def __init__(self, model: str, usd_per_1k_tokens: float):
        self.name = f"openai:{model}"
        self.model = model
        self.usd_per_1k = usd_per_1k_tokens
        self.api_key = os.environ.get("OPENAI_API_KEY", "")

    def _chat(self, system: str, user: str, json_mode: bool = False) -> tuple[str, dict]:
        if not self.api_key:
            raise RuntimeError(
                "OPENAI_API_KEY가 설정되지 않았습니다 — .env 또는 환경변수로 지정하세요."
            )
        payload = {
            "model": self.model,
            "messages": [{"role": "system", "content": system},
                         {"role": "user", "content": user}],
            "temperature": 0,
        }
        if json_mode:
            payload["response_format"] = {"type": "json_object"}
        headers = {"Authorization": f"Bearer {self.api_key}",
                   "Content-Type": "application/json"}
        resp = httpx.post(OPENAI_URL, json=payload, headers=headers, timeout=120.0)
        resp.raise_for_status()
        data = resp.json()
        content = data["choices"][0]["message"]["content"]
        tokens = data.get("usage", {}).get("total_tokens", 0)
        usage = {"model": self.name, "tokens": tokens,
                 "usd": round(tokens / 1000 * self.usd_per_1k, 6)}
        return content, usage


class OpenAIFastLLM(OpenAILLM):
    """분류·계획·단순 대화 전담 (저비용 모델, 기본 gpt-4o-mini)."""

    @traceable(run_type="llm", name="openai_fast.route")
    def route(self, query: str) -> tuple[dict, dict]:
        system = prompts.ROUTE_SYSTEM + " " + prompts.ROUTE_JSON_HINT
        content, usage = self._chat(system, query, json_mode=True)
        try:
            raw = json.loads(content)
            route = raw.get("route") if raw.get("route") in ("chat", "safety") else None
            result = {"route": route or prompts.keyword_route(query),
                      "needs_analysis": bool(raw.get("needs_analysis", False))}
        except (json.JSONDecodeError, AttributeError):
            result = {"route": prompts.keyword_route(query), "needs_analysis": False}
        return result, usage

    @traceable(run_type="llm", name="openai_fast.plan")
    def plan(self, query: str, procedure_prompt: str) -> tuple[dict, dict]:
        system = prompts.plan_system() + " " + prompts.PLAN_JSON_HINT
        user = f"## 표준 절차\n{procedure_prompt}\n\n## 사용자 요청\n{query}"
        content, usage = self._chat(system, user, json_mode=True)
        try:
            raw = json.loads(content)
        except json.JSONDecodeError:
            raw = {"needs_analysis": False, "tasks": []}
        return prompts.normalize_plan(raw, query), usage

    @traceable(run_type="llm", name="openai_fast.chat")
    async def chat(self, query: str, history: list[dict], on_token=None) -> tuple[str, dict]:
        system = ("당신은 화학 실험실 안전 도우미다. 한국어로 한두 문장으로 간결하게 답하라. "
                  "대화 이력에서 사용자가 밝힌 이름이 있으면 정확히 기억해 답하라. "
                  "모르는 단어·약어·질문에는 추측하지 말고 '알 수 없다'고 말한 뒤 정식 이름이나 "
                  "CAS 번호로 다시 물어봐 달라고 하라. "
                  "화학물질 안전 정보는 추측해서 말하지 말고, 물질명을 알려주면 안내문을 "
                  "작성해 준다고 안내하라.")
        rendered = "\n".join(f"{m['role']}: {m['content']}" for m in history[-10:])
        user = (f"## 지금까지의 대화\n{rendered or '(없음)'}\n\n"
                f"## 사용자의 새 메시지\n{query}")
        content, usage = self._chat(system, user)
        usage["prompt"] = f"[SYSTEM]\n{system}\n\n[USER]\n{user}"
        if on_token is not None:
            on_token(content)
        return content, usage


class OpenAIQualityLLM(OpenAILLM):
    """안내문 작성 전담 (고품질 모델, 기본 gpt-4o)."""

    @traceable(run_type="llm", name="openai_quality.write")
    async def write(self, query: str, collected: list[dict], attempt: int,
                    feedback: str | None, on_token=None) -> tuple[str, dict]:
        user = (f"## 사용자 요청\n{query}\n\n## 수집된 사실\n"
                f"{prompts.render_facts(collected)}")
        if feedback:
            user += f"\n\n## 이전 초안에 대한 검수 반려 사유 (반드시 반영)\n{feedback}"
        content, usage = self._chat(prompts.WRITE_SYSTEM, user)
        usage["prompt"] = f"[SYSTEM]\n{prompts.WRITE_SYSTEM}\n\n[USER]\n{user}"
        if on_token is not None:
            on_token(content)
        return content, usage
