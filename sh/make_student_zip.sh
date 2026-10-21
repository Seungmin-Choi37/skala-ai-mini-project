#!/usr/bin/env bash
#
# chem_safety_agent — 학생 배포용 zip을 만드는 스크립트.
#
# .env(실제 API 키가 들어있는 파일)는 절대 포함하지 않는다 — 받는 사람은 README 3단계대로
# .env를 직접 만들고 본인 키를 채운다. 그 외
# .venv/__pycache__/.pytest_cache/session.db/lookup_cache.db/vector_store.db/*.log 등 로컬 실행 중 생기는 산출물과
# .git(커밋 기록)도 제외한다.
#
# 실행:
#   bash sh/make_student_zip.sh
# 결과:
#   ../chem_safety_agent.zip
#
set -euo pipefail
cd "$(dirname "$0")/.."

NAME="chem_safety_agent"
OUT="../${NAME}.zip"

rm -f "$OUT"
rm -rf "/tmp/${NAME}"
mkdir -p "/tmp/${NAME}"

rsync -a \
  --exclude='.venv' \
  --exclude='__pycache__' \
  --exclude='.pytest_cache' \
  --exclude='.git' \
  --exclude='session.db' \
  --exclude='lookup_cache.db' \
  --exclude='vector_store.db' \
  --exclude='*.log' \
  --exclude='.env' \
  --exclude='.DS_Store' \
  ./ "/tmp/${NAME}/"

( cd /tmp && zip -rq "$OLDPWD/$OUT" "${NAME}" )
rm -rf "/tmp/${NAME}"

echo "생성됨: $(cd .. && pwd)/${NAME}.zip"
echo "--- 포함된 .env* 파일 확인 ---"
unzip -l "$OUT" | grep -i "\.env" || echo "(없음)"