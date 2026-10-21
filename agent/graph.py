"""v2 Supervisor — 화학물질 사용 전 안전 안내 LangGraph StateGraph.

route → (chat 직행 | safety 파이프라인)
safety: plan(MCP Prompt 절차 반영) → gate(혼촉 점검 필요 여부를 코드가 결정)
        → research(SDS Tool·지침 Resource 병렬 조회 + 재시도)
        → replan_check(지침이 없으면 GHS 유해성 유형을 판정해 지침 추가 조사)
        → analysis(물질 2종 이상일 때 혼촉 규칙 판정) → write
        → validate(형식·필수 문구 + 근거 없는 수치·H코드 검사, 반려 시 재작성) → finalize

모든 노드는 SupervisorDeps를 functools.partial로 주입받는 순수 함수다.
각 노드는 @traceable로 LangSmith에 계층 트리로 기록된다.
"""
import asyncio
import json
import re
import time
from functools import partial
from typing import TypedDict

from langgraph.config import get_stream_writer
from langgraph.graph import END, StateGraph
from langsmith import traceable

from . import chem_rules
from .deps import SupervisorDeps
from .prompts import keyword_route
from .retry import with_retry

REQUIRED_SECTIONS = ["유해성", "보호구", "취급", "혼용", "응급", "보관"]


class AgentState(TypedDict, total=False):
    query: str
    thread_id: str
    history: list[dict]
    route: str
    needs_analysis: bool
    tasks: list[dict]
    gate_removed: int
    collected: list[dict]
    replan_rounds: int
    retry_count: int
    parallel_elapsed: float
    draft: str
    validation_attempts: int
    validation_feedback: str
    final: str
    last_prompt: str
    cost_usd: float
    events: list[dict]
    conflicts: list[dict]
    unknown_mix: list[str]
    notice: dict


def _cost(state: AgentState, usage: dict) -> float:
    return round(state.get("cost_usd", 0.0) + usage["usd"], 6)


def _label(target: str) -> str:
    return chem_rules.label_of(target)


def _sds(state: AgentState) -> list[dict]:
    return [c["data"] for c in state.get("collected", []) if c["source"] == "sds"]


@traceable(name="route_node")
async def route_node(state: AgentState, deps: SupervisorDeps) -> dict:
    """모든 질문이 무거운 파이프라인을 탈 필요는 없다 — 인사·잡담은 바로 답한다."""
    result, usage = await deps.fast_llm.route(state["query"])
    route = result["route"]
    event = {"type": "RouteDecided", "route": route}
    # 안전 우선 — 모델이 chat이라 해도 물질 이름이나 안전 키워드가 보이면 safety로 보낸다.
    # 위험한 질문을 잡담으로 흘려보내는 쪽의 실수가 반대쪽보다 훨씬 크기 때문이다.
    if route == "chat" and keyword_route(state["query"]) == "safety":
        route = "safety"
        event = {"type": "RouteDecided", "route": route, "overridden": True}
    return {"route": route, "needs_analysis": result["needs_analysis"],
            "cost_usd": _cost(state, usage),
            "events": state.get("events", []) + [event]}


@traceable(name="chat_answer_node")
async def chat_answer_node(state: AgentState, deps: SupervisorDeps) -> dict:
    """대화 이력 기반 응답. /chat/stream에서는 토큰 단위로 흘려보낸다."""
    writer = get_stream_writer()
    reply, usage = await deps.fast_llm.chat(
        state["query"], state.get("history", []),
        on_token=lambda piece: writer({"type": "AnswerToken", "delta": piece}))
    return {"final": reply, "last_prompt": usage.get("prompt"),
            "cost_usd": _cost(state, usage),
            "events": state["events"] + [{"type": "Finalize", "pipeline": "chat-direct"}]}


@traceable(name="plan_node")
async def plan_node(state: AgentState, deps: SupervisorDeps) -> dict:
    """조사할 물질 목록 수립. MCP Prompt(safety_notice_prompt)가 표준 절차를 깔아준다.
    어느 서버가 그 prompt를 가졌는지 가정하지 않고 find_prompt()로 먼저 찾는다."""
    query = state["query"]
    intent = chem_rules.detect_intent(query)
    found = chem_rules.extract_targets(query)
    prompt_server = await deps.mcp.find_prompt("safety_notice_prompt")
    procedure = await deps.mcp.get_prompt(
        "safety_notice_prompt",
        {"substances": ", ".join(t["label"] for t in found) or "(질문에서 확인 필요)",
         "purpose": query},
        server=prompt_server,
    )
    result, usage = await deps.fast_llm.plan(query, procedure)
    tasks = result["tasks"]

    # 후속 질문("그럼 보관은?")처럼 이번 질문에 물질이 없으면, 최근 사용자 질문에서 물질을 이어받는다
    inherited = False
    if not any(t["agent"] == "research" for t in tasks):
        for msg in reversed(state.get("history", [])[-6:]):
            if msg.get("role") != "user":
                continue
            prev = chem_rules.extract_targets(msg.get("content", ""))
            if prev:
                tasks = [{"id": f"task-{i}", "agent": "research", "kind": "sds",
                          "target": t["target"], "label": t["label"], "lookup": t["lookup"] or "",
                          "status": "pending"} for i, t in enumerate(prev)] + tasks
                inherited = True
                break

    targets = [t.get("label") or _label(t["target"]) for t in tasks
               if t["agent"] == "research" and t.get("target")]
    event = {"type": "PlanCreated", "task_count": len(tasks), "targets": targets,
             "intent": intent, "inherited": inherited}
    return {"tasks": tasks, "needs_analysis": result["needs_analysis"],
            "collected": [{"source": "intent", "data": intent}],
            "cost_usd": _cost(state, usage), "events": state["events"] + [event]}


@traceable(name="gate_node")
async def gate_node(state: AgentState, deps: SupervisorDeps) -> dict:
    """권위 게이트 — 혼촉 점검(analysis) 여부는 모델의 needs_analysis가 아니라 코드가
    정한다. 물질이 2종 이상이면 반드시 점검(빠졌으면 추가), 1종 이하면 제거.
    안전 점검이 모델의 변덕으로 건너뛰어지면 안 되기 때문이다."""
    tasks = state["tasks"]
    n_research = sum(1 for t in tasks if t["agent"] == "research")
    has_analysis = any(t["agent"] == "analysis" for t in tasks)
    removed = added = 0
    if n_research >= 2 and not has_analysis:
        tasks = tasks + [{"id": "task-analysis", "agent": "analysis",
                          "kind": "incompatibility", "status": "pending"}]
        added = 1
    elif n_research < 2 and has_analysis:
        kept = [t for t in tasks if t["agent"] != "analysis"]
        removed = len(tasks) - len(kept)
        tasks = kept
    event = {"type": "GateApplied", "removed_analysis_tasks": removed,
             "added_analysis_tasks": added}
    return {"tasks": tasks, "needs_analysis": n_research >= 2, "gate_removed": removed,
            "events": state["events"] + [event]}


@traceable(name="research_node")
async def research_node(state: AgentState, deps: SupervisorDeps) -> dict:
    """SDS Tool·취급 지침 Resource 실호출 — 병렬 실행 + 일시 오류 재시도."""
    tasks = state["tasks"]
    research_indices = [i for i, t in enumerate(tasks)
                        if t["agent"] == "research" and t["status"] == "pending"]
    if not research_indices:   # 객체가 아니라 '실행할 목록'을 검사한다
        return {"events": state["events"] +
                [{"type": "ResearchSkipped", "reason": "pending research task 0개"}]}

    retry_total = state.get("retry_count", 0)
    needs_guide = any(tasks[i]["kind"] == "guide" for i in research_indices)
    guide_server = guide_template = None
    if needs_guide:   # URI를 하드코딩하지 않고, 연결된 서버들에서 템플릿을 먼저 찾는다
        guide_server, guide_template = await deps.mcp.find_resource_template(
            "get_safety_guide")

    async def run_one(idx: int) -> dict:
        task = tasks[idx]
        await asyncio.sleep(0.35)   # 태스크당 고정 지연 — 병렬 효과를 실측하기 위함
        if task["kind"] == "sds":
            data, retries = await with_retry(
                deps.mcp.call_tool, "get_substance_safety",
                {"name": task["target"], "lookup": task.get("lookup") or ""})
            return {"idx": idx, "source": "sds", "data": data, "retries": retries}
        uri = guide_template.format(hazard_type=task["target"])
        data = await deps.mcp.read_resource(uri, server=guide_server)
        return {"idx": idx, "source": "guide", "data": data, "retries": 0}

    started = time.perf_counter()
    results = await asyncio.gather(*(run_one(i) for i in research_indices))
    elapsed = round(time.perf_counter() - started, 2)

    new_tasks = [dict(t) for t in tasks]
    collected = list(state.get("collected", []))
    events = list(state["events"])
    for r in results:
        task = new_tasks[r["idx"]]
        task["status"] = "done"
        collected.append({"source": r["source"], "data": r["data"]})
        retry_total += r["retries"]
        if r["source"] == "sds":
            label = chem_rules.display_name(r["data"]) if r["data"].get("found") \
                else (task.get("label") or _label(task["target"]))
            extra = {"found": bool(r["data"].get("found")), "data_source": r["data"].get("source")}
        else:
            label = chem_rules.HAZARD_LABELS.get(task["target"], task["target"])
            extra = {}
        events.append({"type": "TaskCompleted", "id": task["id"], "source": r["source"],
                       "target": task["target"], "label": label,
                       "retries": r["retries"], **extra})
    return {"tasks": new_tasks, "collected": collected, "retry_count": retry_total,
            "parallel_elapsed": elapsed, "events": events}


@traceable(name="replan_check_node")
async def replan_check_node(state: AgentState, deps: SupervisorDeps) -> dict:
    """1차 수집 결과 평가 — 취급 지침이 없으면, 조회된 SDS의 GHS 분류에서 유해성 유형을
    코드로 판정해 유형별 지침 task를 추가하고 research로 되돌아간다(최대 1회)."""
    has_guide = any(c["source"] == "guide" for c in state.get("collected", []))
    rounds = state.get("replan_rounds", 0)
    if has_guide or rounds >= 1:
        verdict = {"type": "ReplanCheck", "round": rounds, "is_sufficient": True}
        return {"events": state["events"] + [verdict]}
    hazard_types = chem_rules.hazard_types_for_guides(_sds(state))
    gap_tasks = [{"id": f"task-guide-{h}", "agent": "research", "kind": "guide",
                  "target": h, "status": "pending"} for h in hazard_types]
    verdict = {"type": "ReplanCheck", "round": rounds, "is_sufficient": False,
               "targets": [chem_rules.HAZARD_LABELS[h] for h in hazard_types]}
    return {"tasks": state["tasks"] + gap_tasks, "replan_rounds": rounds + 1,
            "events": state["events"] + [verdict]}


@traceable(name="analysis_node")
async def analysis_node(state: AgentState, deps: SupervisorDeps) -> dict:
    """혼촉 점검 — gate가 살려둔 analysis task가 있을 때만 실행한다. LLM에게 묻지 않고
    chem_rules의 규칙표(산화제×인화성, 산×염기 등)로 판정한다."""
    tasks = state["tasks"]
    pending = [t for t in tasks if t["agent"] == "analysis" and t["status"] == "pending"]
    if not pending:
        return {"events": state["events"] +
                [{"type": "AnalysisSkipped", "reason": "analysis task 0개"}]}
    conflicts = chem_rules.check_incompatibility(_sds(state))
    if conflicts:
        by_pair: dict[str, dict[str, list[str]]] = {}
        for c in conflicts:
            entry = by_pair.setdefault(" + ".join(c["pair"]), {"w": [], "a": []})
            entry["w"].append(c["warning"])
            if c["advice"] not in entry["a"]:
                entry["a"].append(c["advice"])
        for e in by_pair.values():   # '섞지 말 것'이 하나라도 있으면 완화된 권고는 빼서 혼동을 막는다
            if chem_rules.KEEP_APART in e["a"]:
                e["a"] = [chem_rules.KEEP_APART]
        summary = " / ".join(f"{pair}: {'; '.join(e['w'])} — {'; '.join(e['a'])}"
                             for pair, e in by_pair.items())
    else:
        summary = "등록된 혼촉 규칙상 확인된 위험 없음(규칙표에 없는 조합일 수 있으니 SDS 10항 확인)"
    # 확인하지 못한 물질은 점검 자체가 불가 — '위험 없음'으로 읽히지 않게 따로 밝힌다
    unknown = [s.get("query") for s in _sds(state) if not s.get("found") and not s.get("tags")]
    if unknown:
        note = (f"{', '.join(unknown)}은(는) 물질을 확인하지 못해 혼촉 점검을 할 수 없음 — "
                "함께 쓰거나 보관하기 전에 정확한 물질과 SDS 10항(안정성 및 반응성)을 확인할 것")
        summary = note if not conflicts else f"{summary} / {note}"
    new_tasks = [dict(t) for t in tasks]
    for t in new_tasks:
        if t["agent"] == "analysis" and t["status"] == "pending":
            t["status"] = "done"
    collected = state.get("collected", []) + [{"source": "analysis", "data": summary}]
    event = {"type": "AnalysisCompleted", "conflicts": len(conflicts)}
    pairs = [{"pair": p.split(" + "), "warnings": e["w"], "advice": e["a"]}
             for p, e in (by_pair.items() if conflicts else [])]
    return {"tasks": new_tasks, "collected": collected, "events": state["events"] + [event],
            "conflicts": pairs, "unknown_mix": unknown}


@traceable(name="write_node")
async def write_node(state: AgentState, deps: SupervisorDeps) -> dict:
    """고품질 모델로 안내문 초안 작성 (fake는 1차를 일부러 부실하게 쓴다).
    토큰마다 attempt 번호를 실어 보내 재작성 시 프론트가 새 말풍선으로 끊게 한다."""
    attempt = state.get("validation_attempts", 0) + 1
    writer = get_stream_writer()
    draft, usage = await deps.quality_llm.write(
        state["query"], state.get("collected", []),
        attempt, state.get("validation_feedback"),
        on_token=lambda piece: writer(
            {"type": "AnswerToken", "attempt": attempt, "delta": piece}))
    return {"draft": draft, "last_prompt": usage.get("prompt"),
            "cost_usd": _cost(state, usage),
            "events": state["events"] + [{"type": "DraftWritten", "attempt": attempt,
                                          "length": len(draft)}]}


# 근거 확인 대상: 단위가 붙은 수치(인화점·농도·노출기준 등)와 GHS H코드.
# 섹션 번호(1)~6)), 전화번호(119·109), '15분' 같은 일반 숫자는 검사하지 않는다.
_UNIT_NUMBER = re.compile(
    r"(?<![\w.])([-−–]?\d+(?:[.,]\d+)?)\s*(?:°\s*[CF]|℃|℉|%|ppm|ppb|mg/m3|mg/m³|mg/kg|mg/L|g/L|mmHg|kPa)",
    re.I)
_ANY_NUMBER = re.compile(r"[-−–]?\d+(?:[.,]\d+)?")
_H_CODE = re.compile(r"(?<![A-Za-z0-9])((?:EUH|H)\d{3})")


def _norm_number(n: str) -> str:
    return n.replace("−", "-").replace("–", "-").replace(",", ".")


def ungrounded(draft: str, evidence: str) -> tuple[list[str], list[str]]:
    """안내문에 있지만 수집된 자료(evidence)에는 없는 (수치, H코드) — 모델이 지어낸 값 후보."""
    known_numbers = {_norm_number(n) for n in _ANY_NUMBER.findall(evidence)}
    known_codes = set(_H_CODE.findall(evidence))
    numbers = [m.group(0).strip() for m in _UNIT_NUMBER.finditer(draft)
               if _norm_number(m.group(1)) not in known_numbers]
    codes = [c for c in _H_CODE.findall(draft) if c not in known_codes]
    return list(dict.fromkeys(numbers)), list(dict.fromkeys(codes))


def check_draft(draft: str, sds_list: list[dict], intent: dict | None = None,
                evidence: str = "") -> list[str]:
    """안내문 검수 기준 — 통과하면 빈 리스트, 아니면 반려 사유 목록.
    evidence: SDS 외에 모델에게 준 자료(취급 지침·혼촉 판정 문장) — 수치 대조에 함께 쓴다."""
    intent = intent or {}
    problems = []
    numbers, codes = ungrounded(draft, json.dumps(sds_list, ensure_ascii=False) + "\n" + evidence)
    if numbers:
        problems.append(f"수집된 자료에 없는 수치가 있습니다({', '.join(numbers)}) — 지어낸 값은 빼세요")
    if codes:
        problems.append(f"수집된 자료에 없는 H코드가 있습니다({', '.join(codes)}) — 지어낸 코드는 빼세요")
    if intent.get("ingest") and "119" not in draft:
        problems.append("섭취 의도가 있는데 '섭취 금지·119 연락' 경고가 없습니다")
    if intent.get("synthesis") and "합성 절차" not in draft:
        problems.append("합성 의도가 있는데 '합성 절차를 안내하지 않음' 문구가 없습니다")
    if sds_list and not any(s.get("found") for s in sds_list):
        # 하나도 확인하지 못한 경우: 6개 항목을 채우면 오히려 추측이 된다 — 모른다는 답이 정답
        if "공식 SDS" not in draft:
            problems.append("확인하지 못한 물질에 '공식 SDS 확인' 안내가 없습니다")
        for s in sds_list:
            if (s.get("query") or "") not in draft:
                problems.append(f"확인하지 못한 물질 '{s.get('query')}'을(를) 답에서 언급하지 않았습니다")
        if chem_rules.check_incompatibility(sds_list) and "섞" not in draft:
            problems.append("이름만으로 판정한 혼촉 위험(섞으면 안 되는 조합)을 답에 쓰지 않았습니다")
        return problems
    if len(draft) < 80 or "1)" not in draft:
        problems.append("'1) … 6)' 번호 형식의 안내문이 아닙니다")
    missing = [s for s in REQUIRED_SECTIONS if s not in draft]
    if missing:
        problems.append(f"필수 항목 누락: {', '.join(missing)}")
    if "출처" not in draft:
        problems.append("'출처:' 표기가 없습니다")
    for s in sds_list:
        if s.get("found"):
            names = [n for n in (s.get("name_ko"), s.get("name_en")) if n]
            if names and not any(n.lower() in draft.lower() for n in names):
                problems.append(f"조사한 물질 '{names[0]}'이(가) 안내문에 없습니다")
    if any(not s.get("found") for s in sds_list) and "공식 SDS" not in draft:
        problems.append("조회에 실패한 물질이 있는데 '공식 SDS 확인 필요' 안내가 없습니다")
    return problems


@traceable(name="validate_node")
async def validate_node(state: AgentState, deps: SupervisorDeps) -> dict:
    """코드 기준 검수: 6개 필수 항목·출처·조사 물질 누락 + 근거 없는 수치·H코드. 최대 3회 후 통과 처리."""
    attempts = state.get("validation_attempts", 0) + 1
    collected = state.get("collected", [])
    intent = next((c["data"] for c in collected if c["source"] == "intent"), {})
    evidence = "\n".join(c["data"] for c in collected
                         if c["source"] in ("guide", "analysis") and isinstance(c["data"], str))
    problems = check_draft(state["draft"], _sds(state), intent, evidence)
    ok = not problems
    verdict = "합격" if ok else "불합격"
    if not ok and attempts >= 3:   # 실모델 무한 재작성 방지
        ok, verdict = True, "합격(최대 시도 도달 — 통과 처리)"
    feedback = None if ok else (
        "안내문 검수 반려: " + "; ".join(problems) +
        ". 형식: 1) 유해성 요약 2) 보호구 3) 취급 주의 4) 혼용 주의 5) 응급조치 "
        "6) 보관·폐기, 마지막 줄에 '출처:'.")
    event = {"type": "Validation", "attempt": attempts, "verdict": verdict,
             "problems": problems}
    return {"validation_attempts": attempts, "validation_feedback": feedback,
            "events": state["events"] + [event]}


def build_notice(state: AgentState) -> dict:
    """화면의 라벨 카드용 구조화 데이터 — 안내문 텍스트와 별개로, 코드가 확인한 사실만 담는다."""
    collected = state.get("collected", [])
    intent = next((c["data"] for c in collected if c["source"] == "intent"), {})
    guides = [c["data"] for c in collected if c["source"] == "guide"]
    sds = _sds(state)
    substances = []
    for s in sds:
        item = {"name": chem_rules.display_name(s) if s.get("found") else s.get("query"),
                "name_en": s.get("name_en"), "cas": s.get("cas"), "found": bool(s.get("found")),
                "source": s.get("source"), "source_url": s.get("source_url"), "note": s.get("note")}
        if s.get("found"):
            codes = chem_rules.pictogram_codes(s.get("pictograms", []))
            item.update(signal=s.get("signal_word"),
                        pictograms=[{"code": c, "label": chem_rules.PICTOGRAMS[c]} for c in codes],
                        hazards=s.get("hazard_statements", [])[:6],
                        first_aid=s.get("first_aid", {}), handling=s.get("handling"),
                        storage=s.get("storage"), flash_point=s.get("flash_point"))
        else:
            item.update(reason=s.get("reason"), candidates=s.get("candidates", []))
        substances.append(item)

    def guide_lines(prefix: str) -> list[str]:
        return list(dict.fromkeys(line[len(prefix):].strip() for g in guides
                                  for line in g.splitlines() if line.startswith(prefix)))

    found = [s for s in sds if s.get("found")]
    return {
        "kind": "unknown" if sds and not found else ("notice" if sds else "general"),
        "intent": intent,
        "substances": substances,
        "checked_mix": len(sds) >= 2,
        "conflicts": state.get("conflicts", []),
        "unknown_mix": state.get("unknown_mix", []),
        "cautions": chem_rules.solo_cautions(sds),   # 조회 실패여도 이름 사전 태그로 판정
        "ppe": chem_rules.ppe_for(found),
        "disposal": guide_lines("폐기:"),
        "accident": guide_lines("사고 시:"),
    }


@traceable(name="finalize_node")
async def finalize_node(state: AgentState, deps: SupervisorDeps) -> dict:
    return {"final": state["draft"], "notice": build_notice(state),
            "events": state["events"] + [{"type": "Finalize", "pipeline": "safety-v2"}]}


def _after_route(state: AgentState) -> str:
    return "chat_answer" if state["route"] == "chat" else "plan"


def _after_replan_check(state: AgentState) -> str:
    last = state["events"][-1]
    return "analysis" if last.get("is_sufficient") else "research"


def _after_validate(state: AgentState) -> str:
    return "finalize" if state.get("validation_feedback") is None else "write"


def build_graph(deps: SupervisorDeps):
    g = StateGraph(AgentState)
    node = lambda fn: partial(fn, deps=deps)   # 명시적 의존성 주입
    g.add_node("route", node(route_node))
    g.add_node("chat_answer", node(chat_answer_node))
    g.add_node("plan", node(plan_node))
    g.add_node("gate", node(gate_node))
    g.add_node("research", node(research_node))
    g.add_node("replan_check", node(replan_check_node))
    g.add_node("analysis", node(analysis_node))
    g.add_node("write", node(write_node))
    g.add_node("validate", node(validate_node))
    g.add_node("finalize", node(finalize_node))

    g.set_entry_point("route")
    g.add_conditional_edges("route", _after_route,
                            {"chat_answer": "chat_answer", "plan": "plan"})
    g.add_edge("chat_answer", END)
    g.add_edge("plan", "gate")
    g.add_edge("gate", "research")
    g.add_edge("research", "replan_check")
    g.add_conditional_edges("replan_check", _after_replan_check,
                            {"analysis": "analysis", "research": "research"})
    g.add_edge("analysis", "write")
    g.add_edge("write", "validate")
    g.add_conditional_edges("validate", _after_validate,
                            {"finalize": "finalize", "write": "write"})
    g.add_edge("finalize", END)
    return g.compile()