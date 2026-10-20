"""v1 (legacy) supervisor — 버전 토글(SUPERVISOR_VERSION=v1) 데모용.

라우팅·게이트·재계획·혼촉 점검·검수가 없는 초기 선형 버전. 질문에서 처음 찾은 물질
하나만 SDS를 조회해 한 줄로 답한다 — v2와 비교하면 무엇이 빠져 있는지 바로 보인다.
문제가 생기면 환경변수 하나로 v2에서 즉시 롤백해 돌아오는 지점이다.
"""
from langsmith import traceable

from . import chem_rules
from .deps import SupervisorDeps
from .retry import with_retry


@traceable(name="supervisor_v1_legacy")
async def run_v1(deps: SupervisorDeps, query: str, thread_id: str,
                 history: list[dict]) -> dict:
    events = [{"type": "Legacy", "note": "v1: 라우팅/게이트/재계획/혼촉 점검/검수 없음"}]
    reply, usage = await deps.fast_llm.chat(query, history)
    retries = 0
    found = chem_rules.find_substances(query)
    if found:
        data, retries = await with_retry(
            deps.mcp.call_tool, "get_substance_safety", {"name": found[0]})
        if data.get("found"):
            reply = (f"{chem_rules.display_name(data)}: {data.get('signal_word')}, "
                     f"{'; '.join(data.get('hazard_statements', [])[:2])}. "
                     f"자세한 내용은 SDS를 확인하세요.")
        events.append({"type": "TaskCompleted", "source": "sds", "target": found[0],
                       "label": chem_rules.display_name(data) if data.get("found") else found[0],
                       "retries": retries})
    events.append({"type": "Finalize", "pipeline": "legacy-v1"})
    return {"final": reply, "route": "legacy", "events": events,
            "cost_usd": usage["usd"], "validation_attempts": 0,
            "replan_rounds": 0, "gate_removed": 0, "retry_count": retries}
