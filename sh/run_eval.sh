#!/usr/bin/env bash
#
# chem_safety_agent — LLM-as-a-Judge 평가 실행 (evaluate.py).
#
# data/eval_cases.json의 질문 13개를 에이전트 그래프에 넣고, 코드 검사(경로·필수 문구)와
# Judge 채점(근거 충실성·안전성·유용성 1~5점)을 해서 doc/eval_report.md를 만든다.
#
# 실행:
#   bash sh/run_eval.sh            # 실제 평가 — 에이전트 OpenAI(gpt-4o-mini/gpt-4o) + Judge gpt-4o-mini (과금)
#   bash sh/run_eval.sh fake       # 키 없이 흐름만 확인 — fake LLM + 규칙 Judge (무료)
#
# LangSmith: .env에 LANGCHAIN_TRACING_V2=true와 LANGCHAIN_API_KEY가 있으면 프로젝트
# chem-safety-eval에 케이스별 실행 트리와 Judge 점수(피드백)가 남는다.
#
set -euo pipefail
cd "$(dirname "$0")/.."
[ -x ".venv/bin/python3" ] && export PATH="$PWD/.venv/bin:$PATH"

MODE="${1:-openai}"
[ -f .env ] && { set -a; source .env; set +a; }
export LANGCHAIN_PROJECT="chem-safety-eval"

if [ "$MODE" = "fake" ]; then
    LLM_BACKEND=fake EMBEDDING_BACKEND=local python3 evaluate.py --judge fake
    exit 0
fi

if [ -z "${OPENAI_API_KEY:-}" ]; then
    echo "ERROR: .env에 OPENAI_API_KEY가 없습니다 — 키 없이 흐름만 보려면: bash sh/run_eval.sh fake"
    exit 1
fi
echo "LangSmith: project=$LANGCHAIN_PROJECT tracing=${LANGCHAIN_TRACING_V2:-false}"
echo "⚠️  질문 13개 × (에이전트 호출 + Judge 호출)이 실제 OpenAI 요금으로 청구됩니다."
read -r -p "계속하시겠습니까? [y/N] " CONFIRM
case "$CONFIRM" in
    [yY]|[yY][eE][sS]) ;;
    *) echo "중단합니다."; exit 1 ;;
esac
LLM_BACKEND=openai python3 evaluate.py --judge openai