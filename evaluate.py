"""LLM-as-a-Judge 평가 실행기 — data/eval_cases.json의 질문을 그래프에 넣고 채점해 보고서를 만든다.

    python evaluate.py                     # .env 설정대로 (LLM_BACKEND, Judge는 키가 있으면 openai)
    python evaluate.py --judge fake        # 키 없이 규칙 Judge로 흐름만 확인
    python evaluate.py --limit 3           # 앞의 3문항만

케이스마다
  1) 코드 검사 : 경로(route)가 기대와 같은가, 필수 문구(must_include)가 답에 있는가
  2) Judge 채점: 근거 충실성·안전성·유용성 1~5점 + 이유 (agent/judge.py)
  3) 통과      : 코드 검사 통과 + 세 점수 모두 3점 이상
결과: doc/eval_report.md(표), doc/eval_results.json(원자료)

LangSmith 트레이싱(LANGCHAIN_TRACING_V2=true)을 켜면 케이스마다 eval_case 실행 트리가 남고,
끝난 뒤 Judge 점수를 그 실행에 피드백(groundedness·safety·helpfulness, 0~1)으로 붙인다.
"""
import argparse
import asyncio
import json
import os
import time
from datetime import datetime
from pathlib import Path

from dotenv import load_dotenv

load_dotenv()

from langsmith import traceable  # noqa: E402
from langsmith.run_helpers import get_current_run_tree  # noqa: E402

from agent.deps import SupervisorDeps  # noqa: E402
from agent.graph import build_graph  # noqa: E402
from agent.judge import CRITERIA, PASS_SCORE, build_context, make_judge  # noqa: E402

ROOT = Path(__file__).resolve().parent
CASES = ROOT / "data" / "eval_cases.json"


def load_cases(path: Path = CASES) -> list[dict]:
    return json.loads(path.read_text(encoding="utf-8"))["cases"]


@traceable(name="eval_case")
async def run_case(graph, judge, case: dict) -> dict:
    started = time.perf_counter()
    result = await graph.ainvoke({"query": case["question"], "thread_id": f"eval-{case['id']}",
                                  "history": [], "events": [], "cost_usd": 0.0})
    answer = result.get("final", "")
    route_ok = result.get("route") == case.get("route")
    missing = [w for w in case.get("must_include", []) if w not in answer]
    scores = await judge.score(case["question"], answer, build_context(result))
    passed = route_ok and not missing and all(scores[k] >= PASS_SCORE for k in CRITERIA)
    rt = get_current_run_tree()
    return {"id": case["id"], "question": case["question"], "expected_route": case.get("route"),
            "route": result.get("route"), "route_ok": route_ok, "missing": missing,
            "scores": {k: scores[k] for k in CRITERIA}, "reason": scores["reason"], "passed": passed,
            "answer": answer, "cost_usd": result.get("cost_usd", 0.0),
            "seconds": round(time.perf_counter() - started, 2), "run_id": str(rt.id) if rt else None}


async def run_cases(deps: SupervisorDeps, cases: list[dict], judge) -> list[dict]:
    graph = build_graph(deps)
    return [await run_case(graph, judge, c) for c in cases]


def summarize(rows: list[dict]) -> dict:
    n = len(rows) or 1
    return {"cases": len(rows), "passed": sum(r["passed"] for r in rows),
            "route_ok": sum(r["route_ok"] for r in rows),
            "avg": {k: round(sum(r["scores"][k] for r in rows) / n, 2) for k in CRITERIA},
            "cost_usd": round(sum(r["cost_usd"] for r in rows), 4)}


def render_report(rows: list[dict], meta: dict) -> str:
    s = summarize(rows)
    out = ["# LLM-as-a-Judge 평가 결과", "",
           f"- 실행: {meta['when']} · LLM `{meta['llm']}` · Judge `{meta['judge']}` · 데이터 `{meta['data']}` "
           f"· 임베딩 `{meta['embedding']}`",
           f"- 질문 {s['cases']}개 · **통과 {s['passed']}/{s['cases']}** · 경로 일치 {s['route_ok']}/{s['cases']} "
           f"· 에이전트 비용(가상 단가) ${s['cost_usd']}",
           "- 평균 점수(1~5): " + " · ".join(f"{CRITERIA[k]} **{v}**" for k, v in s["avg"].items()),
           f"- 통과 기준: 경로 일치 + 필수 문구 포함 + 세 점수 모두 {PASS_SCORE}점 이상", ""]
    if meta["judge"].startswith("fake"):
        out += ["> ⚠️ 이 결과는 규칙 기반 fake Judge로 낸 것이다(LLM 채점 아님). "
                "실제 LLM 채점은 `bash sh/run_eval.sh` (OpenAI 키 필요).", ""]
    out += ["| ID | 질문 | 경로(기대→실제) | 근거 | 안전 | 유용 | 통과 | Judge 이유 |",
            "|---|---|---|---|---|---|---|---|"]
    for r in rows:
        route = f"{r['expected_route']}→{r['route']}" + ("" if r["route_ok"] else " ❌")
        reason = r["reason"].replace("|", "/").replace("\n", " ")
        if r["missing"]:
            reason = f"필수 문구 없음: {', '.join(r['missing'])}. " + reason
        out.append(f"| {r['id']} | {r['question']} | {route} | {r['scores']['groundedness']} | "
                   f"{r['scores']['safety']} | {r['scores']['helpfulness']} | {'✅' if r['passed'] else '❌'} | "
                   f"{reason[:160]} |")
    return "\n".join(out) + "\n"


def post_feedback(rows: list[dict]) -> int:
    """LangSmith 트레이싱이 켜져 있으면 Judge 점수를 각 eval_case 실행에 피드백으로 붙인다."""
    if os.environ.get("LANGCHAIN_TRACING_V2", "").lower() != "true":
        return 0
    try:
        from langchain_core.tracers.langchain import wait_for_all_tracers
        from langsmith import Client
        wait_for_all_tracers()
        client, sent = Client(), 0
        for r in rows:
            if not r["run_id"]:
                continue
            for k in CRITERIA:
                client.create_feedback(r["run_id"], key=k, score=r["scores"][k] / 5, comment=r["reason"])
                sent += 1
        return sent
    except Exception as exc:   # 피드백 전송 실패는 평가 결과에 영향 없음
        print(f"LangSmith 피드백 전송 실패: {type(exc).__name__}")
        return 0


async def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--judge", choices=["openai", "fake"], default=None)
    parser.add_argument("--limit", type=int, default=0)
    parser.add_argument("--out", default=str(ROOT / "doc" / "eval_report.md"))
    args = parser.parse_args()

    from agent.mcp_client import MCPClient
    from agent.vector_store import default_embedder
    from server import _build_llms

    cases = load_cases()[: args.limit or None]
    judge = make_judge(args.judge)
    backend, fast, quality = _build_llms()
    mcp = MCPClient()
    await mcp.start()
    try:
        deps = SupervisorDeps(fast_llm=fast, quality_llm=quality, mcp=mcp, session_store=None, version="v2")
        rows = await run_cases(deps, cases, judge)
    finally:
        await mcp.stop()

    meta = {"when": datetime.now().strftime("%Y-%m-%d %H:%M"), "llm": backend, "judge": judge.name,
            "data": os.environ.get("CHEM_DATA_MODE", "live"), "embedding": default_embedder().name}
    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(render_report(rows, meta), encoding="utf-8")
    out.with_name("eval_results.json").write_text(
        json.dumps({"meta": meta, "summary": summarize(rows), "rows": rows}, ensure_ascii=False, indent=2),
        encoding="utf-8")
    s = summarize(rows)
    print(f"통과 {s['passed']}/{s['cases']} · 평균 {s['avg']} → {out}")
    sent = post_feedback(rows)
    if sent:
        print(f"LangSmith 피드백 {sent}건 전송")


if __name__ == "__main__":
    asyncio.run(main())