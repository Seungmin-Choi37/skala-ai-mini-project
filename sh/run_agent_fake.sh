#!/usr/bin/env bash
#
# chem_safety_agent — fake(결정론) LLM으로 에이전트를 "기동만" 해두는 스크립트.
# LLM·API 키 없이 전체 파이프라인을 바로 돌려볼 수 있다(처음 실행해볼 때 추천).
# 종료하려면 이 터미널에서 Ctrl+C.
#
set -euo pipefail
cd "$(dirname "$0")/.."
[ -x ".venv/bin/python3" ] && export PATH="$PWD/.venv/bin:$PATH"

if [ -f .env ]; then set -a; source .env; set +a; fi
export LANGCHAIN_TRACING_V2="${LANGCHAIN_TRACING_V2:-false}"
export LANGCHAIN_PROJECT="chem-safety-agent-fake"
export CHEM_DATA_MODE="${CHEM_DATA_MODE:-live}"

lsof -ti:8100 | xargs kill -9 2>/dev/null || true
rm -f session.db
cleanup() { echo; echo "에이전트를 종료합니다..."; lsof -ti:8100 | xargs kill -9 2>/dev/null || true; }
trap cleanup EXIT

LLM_BACKEND=fake SUPERVISOR_VERSION="${SUPERVISOR_VERSION:-v2}" python3 server.py &
SERVER_PID=$!
for _ in $(seq 1 40); do curl -s http://127.0.0.1:8100/health >/dev/null 2>&1 && break; sleep 0.5; done
curl -s http://127.0.0.1:8100/health | python3 -m json.tool --no-ensure-ascii

cat <<'MSG'

################################################################
 에이전트가 떠 있습니다 (http://127.0.0.1:8100, LLM_BACKEND=fake)
################################################################

1) curl로 물어보기
   curl -s -X POST localhost:8100/chat -H 'Content-Type: application/json' \
     -d '{"message":"내일 실험에 톨루엔이랑 질산 쓸 건데 주의사항 알려줘","thread_id":"t1"}' \
     | python3 -m json.tool --no-ensure-ascii

2) 진행 상황 스트리밍
   curl -sN -G localhost:8100/chat/stream \
     --data-urlencode "message=염산과 수산화나트륨으로 중화 실험할 거야" \
     --data-urlencode "thread_id=t2"

3) 브라우저 채팅 화면 (새 터미널에서)
   bash sh/run_frontend.sh   → http://localhost:8600

종료하려면 Ctrl+C.
MSG
wait "$SERVER_PID"
