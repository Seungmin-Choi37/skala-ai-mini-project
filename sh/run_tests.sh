#!/usr/bin/env bash
#
# chem_safety_agent — 자동 테스트(pytest) 실행.
# 인터넷·API 키 없이 돌아간다. .env에 실제 키가 있어도 쓰지 않는다
# (tests/conftest.py가 fake LLM · sample 데이터 · KOSHA 끔으로 고정한다).
#
# 실행:
#   bash sh/run_tests.sh            # 전체
#   bash sh/run_tests.sh -k 혼촉    # 이름에 '혼촉'이 들어간 테스트만 (pytest 옵션 그대로 전달)
#
set -euo pipefail
cd "$(dirname "$0")/.."

PY=python3
[ -x ".venv/bin/python3" ] && PY=".venv/bin/python3"
"$PY" -c "import pytest" 2>/dev/null \
    || { echo "ERROR: pytest가 없습니다 — 먼저 bash sh/setup_venv.sh 를 실행하세요."; exit 1; }

NO_PROXY="127.0.0.1,localhost,testserver${NO_PROXY:+,$NO_PROXY}" "$PY" -m pytest -q -p no:warnings "$@"