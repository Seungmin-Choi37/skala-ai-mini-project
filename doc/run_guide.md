# 실행 가이드

macOS/Linux 터미널 기준이다. Windows는 [7. 스크립트 없이 직접 실행](#7-스크립트-없이-직접-실행-windows-포함)을 본다.

## 0. 준비물

```bash
python3 --version   # 3.10 이상 필요 (코드에 str | None 문법 사용)
curl --version      # 테스트용
```

## 1. 압축 풀기

```bash
cd ~/Downloads                  # zip을 받은 폴더
unzip chem_safety_agent.zip
cd chem_safety_agent
```

## 2. 가상환경 만들기 (처음 한 번)

```bash
bash sh/setup_venv.sh
```
`.venv` 생성 + `requirements.txt` 설치. 마지막에 `필요 패키지 전부 import 성공`이 나오면 정상이다.

## 3. 환경설정 파일

```bash
printf "CHEM_DATA_MODE=live\nOPENAI_API_KEY=\n" > .env
```

필요한 값만 채운다 (fake 모드는 그대로 둬도 된다).

```ini
CHEM_DATA_MODE=sample        # sample(샘플 6종) | live(PubChem 실시간)
DATA_GO_KR_API_KEY=          # 공공데이터포털 키 — 지금은 미사용 (KOSHA 확장용)
LANGCHAIN_TRACING_V2=false   # LangSmith를 쓸 때만 true + LANGCHAIN_API_KEY 입력
OPENAI_API_KEY=sk-...        # OpenAI 모드에서만 필요
```

`.env`에는 실제 키가 들어가므로 깃에 올리거나 공유하지 않는다(`.gitignore`에 포함됨).

## 4. fake 모드로 실행 (키 불필요, 처음엔 이걸로)

### 터미널 1 — 백엔드 (:8100)

```bash
bash sh/run_agent_fake.sh
```
`/health` 결과가 출력되고 서버가 떠 있는 상태로 대기한다. 종료는 `Ctrl+C`.

### 터미널 2 — curl로 테스트

```bash
cd ~/Downloads/chem_safety_agent

# ① 서버 상태
curl -s localhost:8100/health | python3 -m json.tool --no-ensure-ascii

# ② 안내문 한 번에 받기 (응답 전체 + metadata + events)
curl -s -X POST localhost:8100/chat \
  -H 'Content-Type: application/json' \
  -d '{"message":"내일 실험에 톨루엔이랑 질산 쓸 건데 주의사항 알려줘","thread_id":"t1"}' \
  | python3 -m json.tool --no-ensure-ascii

# ③ 안내문 본문만 보기
curl -s -X POST localhost:8100/chat \
  -H 'Content-Type: application/json' \
  -d '{"message":"염산과 NaOH로 중화 실험할 거야","thread_id":"t2"}' \
  | python3 -c "import json,sys; print(json.load(sys.stdin)['reply'])"

# ④ 진행 과정 실시간 스트리밍 (SSE)
curl -sN -G localhost:8100/chat/stream \
  --data-urlencode "message=메탄올이랑 질산 보관 주의" \
  --data-urlencode "thread_id=t3"

# ⑤ 대화 기억 (thread_id를 같게)
curl -s -X POST localhost:8100/chat -H 'Content-Type: application/json' \
  -d '{"message":"제 이름은 승민입니다","thread_id":"me"}' | python3 -m json.tool --no-ensure-ascii
curl -s -X POST localhost:8100/chat -H 'Content-Type: application/json' \
  -d '{"message":"제 이름이 뭐였죠?","thread_id":"me"}' | python3 -m json.tool --no-ensure-ascii
```

`/chat` 응답의 `metadata`에서 볼 값:

| 필드 | 의미 |
|---|---|
| `route` | `safety`(안내문 파이프라인) 또는 `chat`(일반 대화) |
| `gate_removed` | 혼촉 점검을 제거한 횟수 (물질 1종이면 1) |
| `replan_rounds` | 취급 지침을 추가로 조사한 횟수 |
| `validation_attempts` | 검수 시도 횟수 (fake는 일부러 1차를 반려하므로 2) |
| `retry_count` | 첫 SDS 조회의 의도적 타임아웃을 재시도로 복구했으면 1 |
| `parallel_elapsed` | 병렬 조회에 걸린 시간(초) |

질문별 기대 결과는 [test_queries.md](test_queries.md)에 있다.

### 터미널 3 — 브라우저 화면 (:8600)

```bash
cd ~/Downloads/chem_safety_agent
bash sh/run_frontend.sh
```
브라우저에서 **http://localhost:8600** 접속. 백엔드(터미널 1)가 먼저 떠 있어야 한다(없으면 에러 후 종료).

## 5. PubChem 실데이터로 바꾸기 (live 모드)

`.env`에서 한 줄만 바꾸고 백엔드를 다시 띄운다.

```ini
CHEM_DATA_MODE=live
```

```bash
bash sh/run_agent_fake.sh     # 터미널 1에서 Ctrl+C 후 재실행

# PubChem이 이 PC에서 닿는지 먼저 확인 (CID 1140이 나오면 정상)
curl -s https://pubchem.ncbi.nlm.nih.gov/rest/pug/compound/name/toluene/cids/JSON

curl -s -X POST localhost:8100/chat -H 'Content-Type: application/json' \
  -d '{"message":"톨루엔 사용 전 주의사항","thread_id":"live1"}' \
  | python3 -c "import json,sys; print(json.load(sys.stdin)['reply'])"
```

- 출처 줄에 `https://pubchem.ncbi.nlm.nih.gov/compound/1140...`이 나오면 실제 PubChem 데이터다.
- `샘플 데이터`로 나오면 PubChem 호출이 실패해 샘플로 폴백한 것이다. `/health`의 `chem_data_mode`가
  `live`인지, 위 확인용 curl이 성공하는지 본다.
- PubChem 응답 파서는 구조를 맞춘 테스트 데이터로만 검증했다. 유해성 항목이 비거나 이상하면
  `agent/pubchem.py`의 `parse_safety_record`를 실제 응답에 맞춰 손봐야 한다.

## 6. 실제 LLM으로 실행

### Ollama (로컬, 무료)

```bash
bash sh/setup_ollama.sh        # 처음 한 번: qwen3:0.6b, qwen3:8b 받기 (용량 큼)
bash sh/run_agent_ollma.sh     # 백엔드 :8100
```

### OpenAI (과금)

```bash
# .env에 OPENAI_API_KEY=sk-... 입력 후
bash sh/run_agent_openai.sh    # "계속하시겠습니까? [y/N]"에 y
```

테스트 방법은 4번과 같다. 실모델은 질문에서 물질명을 스스로 뽑으므로, live 모드와 함께 쓰면 샘플에
없는 물질도 **영문명이나 CAS 번호**로 물어볼 수 있다.

```bash
curl -s -X POST localhost:8100/chat -H 'Content-Type: application/json' \
  -d '{"message":"ethanol 취급 주의사항 알려줘","thread_id":"x1"}' \
  | python3 -c "import json,sys; print(json.load(sys.stdin)['reply'])"
```

## 7. 스크립트 없이 직접 실행 (Windows 포함)

스크립트는 bash와 `lsof`를 쓴다. 직접 띄울 때는 다음과 같다.

### macOS/Linux

```bash
source .venv/bin/activate
LLM_BACKEND=fake CHEM_DATA_MODE=sample python3 server.py      # 터미널 1
python3 web_demo_server.py                                     # 터미널 2
```

### Windows PowerShell

```powershell
python -m venv .venv
.venv\Scripts\Activate.ps1
pip install -r requirements.txt
Set-Content .env "CHEM_DATA_MODE=live`nOPENAI_API_KEY="

# 터미널 1
$env:LLM_BACKEND="fake"; $env:CHEM_DATA_MODE="sample"; python server.py
# 터미널 2 (.venv 활성화 후)
python web_demo_server.py
```

PowerShell에서 테스트:

```powershell
Invoke-RestMethod -Method Post -Uri http://localhost:8100/chat `
  -ContentType "application/json; charset=utf-8" `
  -Body '{"message":"톨루엔이랑 질산 주의사항","thread_id":"w1"}' | Select-Object -ExpandProperty reply
```

## 8. 그 밖의 실행 옵션

```bash
# v1(구버전 단순 파이프라인)과 비교
SUPERVISOR_VERSION=v1 bash sh/run_agent_fake.sh

# 서버 재시작 후에도 대화를 기억하는지 확인
# (run_agent_*.sh는 시작할 때 session.db를 지우므로 server.py를 직접 실행)
source .venv/bin/activate
LLM_BACKEND=fake python3 server.py     # 이름 알려주고 Ctrl+C → 다시 실행 → 이름 물어보기
```

## 9. 문제 해결

| 증상 | 해결 |
|---|---|
| `ModuleNotFoundError` | `bash sh/setup_venv.sh` 재실행. 직접 실행 시 `.venv` 활성화 확인 |
| `address already in use` (8100/8600) | `lsof -ti:8100 \| xargs kill -9` (Windows: `netstat -ano \| findstr 8100` → `taskkill /PID 번호 /F`) |
| 채팅 화면 제목에 `{{ backendLabel }}`가 그대로 보임 | Vue를 unpkg.com에서 못 받은 것 — 인터넷/사내 방화벽 확인 |
| 화면은 뜨는데 답이 안 옴 | 백엔드(8100)가 꺼져 있음 — 터미널 1 확인 |
| `.env가 없습니다` | `printf "CHEM_DATA_MODE=live\nOPENAI_API_KEY=\n" > .env` 로 만든다 (ollama/openai 스크립트는 `.env` 필수) |
| 이전 대화가 섞임 | `thread_id`를 바꾸거나 `session.db` 삭제 |
| 다른 사람에게 배포 | `bash sh/make_student_zip.sh` → `.env`(키)를 뺀 zip이 상위 폴더에 생성 |
