# 실행 가이드 (자세한 버전)

README의 "처음 실행하기"를 끝낸 뒤 보는 문서다. macOS/Linux 터미널 기준이고,
Windows는 [9. 스크립트 없이 직접 실행](#9-스크립트-없이-직접-실행-windows-포함)을 본다.

## 0. 준비물

```bash
python3 --version   # 3.10 이상 (코드에 str | None 문법 사용)
curl --version      # 확인용
```

## 1. 프로젝트 폴더로 이동

```bash
cd ~/Downloads/chem_safety_agent     # 깃으로 받았다면 clone한 폴더
pwd                                  # 끝이 /chem_safety_agent 이면 맞다
```

이후 모든 명령은 이 폴더 안에서 실행한다.

## 2. 가상환경 (처음 한 번)

```bash
bash sh/setup_venv.sh
```
`.venv` 생성 + `requirements.txt` 설치. 마지막에 `필요 패키지 전부 import 성공`이 나오면 정상이다.
`requirements.txt`가 바뀐 뒤(예: pytest 추가)에도 이 명령을 한 번 더 실행하면 빠진 것만 설치된다.

## 3. 설정 파일 `.env`

`.env`가 아직 없을 때만 만든다. **이미 있으면 이 명령을 실행하지 않는다**(덮어써서 넣어 둔 키가 지워진다).

```bash
ls .env 2>/dev/null || printf "CHEM_DATA_MODE=live\nOPENAI_API_KEY=\nDATA_GO_KR_API_KEY=\n" > .env
```

쓸 수 있는 값 (필요한 줄만 넣으면 된다):

```ini
CHEM_DATA_MODE=live          # live(기본, KOSHA·PubChem 실시간) | sample(샘플 6종, 오프라인)
OPENAI_API_KEY=              # OpenAI 모드에서만 필요
DATA_GO_KR_API_KEY=          # 공공데이터포털 키 — 넣으면 안전보건공단 MSDS를 먼저 조회한다
KOSHA_ENABLED=1              # 0으로 두면 키가 있어도 KOSHA를 쓰지 않는다
CHEM_CACHE_TTL_DAYS=30       # 조회 결과 캐시 보관 일수 (0이면 캐시 끔)
LANGCHAIN_TRACING_V2=false   # LangSmith를 쓸 때만 true + LANGCHAIN_API_KEY
```

`.env`에는 실제 키가 들어가므로 깃에 올리거나 화면 캡처로 공유하지 않는다(`.gitignore`에 포함됨).
키가 노출됐다면 발급한 사이트에서 바로 폐기하고 새로 발급받는다.

## 4. fake 모드로 실행 (LLM 키 불필요)

### 터미널 1 — 백엔드 (:8100)

```bash
bash sh/run_agent_fake.sh
```
`/health` 결과가 출력되고 서버가 떠 있는 상태로 대기한다. 종료는 `Ctrl+C`.
`"chem_data_mode": "live"`이면 KOSHA·PubChem에서 실데이터를 가져온다.

### 터미널 2 — 브라우저 화면 (:8600)

```bash
cd ~/Downloads/chem_safety_agent
bash sh/run_frontend.sh
```
브라우저에서 **http://localhost:8600**. 화면 사용법은 README 6단계에 있다.

### 터미널 3 — curl로 직접 호출 (선택)

```bash
cd ~/Downloads/chem_safety_agent

# ① 서버 상태
curl -s localhost:8100/health | python3 -m json.tool --no-ensure-ascii

# ② 안내문 본문만 보기
curl -s -X POST localhost:8100/chat \
  -H 'Content-Type: application/json' \
  -d '{"message":"톨루엔이랑 질산 같이 써도 돼?","thread_id":"t1"}' \
  | python3 -c "import json,sys; print(json.load(sys.stdin)['reply'])"

# ③ 화면용 구조화 데이터(그림문자·혼촉 쌍·단독 주의·보호구)
curl -s -X POST localhost:8100/chat \
  -H 'Content-Type: application/json' \
  -d '{"message":"금속나트륨이랑 염산 같이 보관해도 돼?","thread_id":"t2"}' \
  | python3 -c "import json,sys; print(json.dumps(json.load(sys.stdin)['notice'], ensure_ascii=False, indent=2))"

# ④ 진행 과정 실시간 스트리밍 (SSE) — 화면의 진행 막대가 받는 것과 같다
curl -sN -G localhost:8100/chat/stream \
  --data-urlencode "message=메탄올이랑 질산 보관 주의" \
  --data-urlencode "thread_id=t3"

# ⑤ 대화 기억 (thread_id를 같게)
curl -s -X POST localhost:8100/chat -H 'Content-Type: application/json' \
  -d '{"message":"제 이름은 승민입니다","thread_id":"me"}' | python3 -c "import json,sys; print(json.load(sys.stdin)['reply'])"
curl -s -X POST localhost:8100/chat -H 'Content-Type: application/json' \
  -d '{"message":"제 이름이 뭐였죠?","thread_id":"me"}' | python3 -c "import json,sys; print(json.load(sys.stdin)['reply'])"
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

## 5. 데이터 출처와 캐시

`CHEM_DATA_MODE=live`(기본)일 때 물질 하나를 이 순서로 찾는다.

| 순서 | 출처 | 조건 | 특징 |
|---|---|---|---|
| ① | 안전보건공단 MSDS | `.env`에 `DATA_GO_KR_API_KEY`가 있을 때 | 한국어 원문. 사전에 없는 한국어 이름(예: 아니솔)도 찾는다 |
| ② | PubChem | 영문명·CAS를 알 때 (이름 사전 `data/name_map.json`이 한국어명→CAS 변환) | 키 불필요. 유해·위험 문구는 한국어 표(`data/h_statements_ko.json`)로 바꿔 보여준다. 응급조치·취급 문장은 영어 원문 |
| ③ | 샘플 6종 | 위 둘이 모두 실패했을 때 | `data/substances.json`. 안내문에 "샘플 데이터 사용" 메모가 붙는다 |

- 안내문의 **출처** 줄과 라벨 카드 맨 아래에서 어느 출처를 썼는지 확인한다.
- ①이 실패하면 그 이유(예: `KOSHA 조회 실패(KoshaError: HTTP 500)`)가 메모로 남고 ②로 넘어간다.
- **캐시**: 찾은 결과는 `lookup_cache.db`(SQLite)에 30일 보관한다. 서버를 다시 켜도 같은 물질은 다시
  조회하지 않아 빠르다. 최신 자료로 다시 받고 싶으면 서버를 끄고 이 파일을 지운다(`rm lookup_cache.db`).
  못 찾은 결과는 캐시하지 않는다(이름을 고쳐 다시 물을 수 있게).
- `CHEM_DATA_MODE=sample`이면 인터넷 없이 샘플 6종만 쓴다(발표·오프라인 데모용).

PubChem이 이 PC에서 닿는지 확인 (`{"IdentifierList": {"CID": [1140]}}`이 나오면 정상):

```bash
curl -s https://pubchem.ncbi.nlm.nih.gov/rest/pug/compound/name/toluene/cids/JSON
```

## 6. 안전보건공단(KOSHA) MSDS 켜기

> ⚠️ `agent/kosha.py`는 공개된 명세(아래 표)를 보고 만들었고, 개발 환경에서 외부 접속이 막혀
> **실제 서버로는 시험해 보지 못했다.** 처음 켤 때 아래 6-3 확인을 꼭 한 번 한다.
> 실패해도 PubChem으로 자동으로 넘어가므로 서비스가 멈추지는 않는다.

### 6-1. 키 받기

1. https://www.data.go.kr 로그인 → "안전보건공단 물질안전보건자료" 검색
2. `한국산업안전보건공단_물질안전보건자료(MSDS)` 오픈 API → **활용신청** (개발계정은 자동 승인)
3. 마이페이지 → 개발계정 → 일반 인증키를 복사. 인코딩 키·디코딩 키 **어느 쪽을 넣어도 된다**
   (코드가 알아서 맞춘다)
4. 신청 직후에는 키가 아직 등록되지 않아 `SERVICE_KEY_IS_NOT_REGISTERED_ERROR`가 날 수 있다 — 1시간쯤 뒤 다시 시도

### 6-2. `.env`에 넣기

```ini
DATA_GO_KR_API_KEY=발급받은키
```
그리고 백엔드를 다시 켠다(`Ctrl+C` → `bash sh/run_agent_fake.sh`).

### 6-3. 연결 확인

키를 화면에 출력하지 않는 확인 명령이다.

```bash
.venv/bin/python3 -c "
from dotenv import load_dotenv; load_dotenv()
from agent import kosha
print('KOSHA 켜짐:', kosha.enabled())
r = kosha.fetch_kosha('108-88-3', '톨루엔', 'toluene', cas='108-88-3')
print('찾음:', r['found'], '| 신호어:', r.get('signal_word'))
print('문구:', r.get('hazard_statements', [])[:3])
print('응급조치 항목:', list(r.get('first_aid', {})), '| 이유:', r.get('reason'))
"
```

| 결과 | 뜻 |
|---|---|
| `찾음: True`, 문구·응급조치가 채워짐 | 정상 |
| `KoshaError: 30 SERVICE_KEY_IS_NOT_REGISTERED_ERROR` | 키가 아직 등록 전이거나 잘못 복사됨 |
| `KoshaError: 22 …` | 하루 호출 한도 초과 — 내일 다시 |
| `KoshaError: HTTP 404` | 주소가 바뀌었다 — 아래 6-4 |
| `찾음: True`인데 문구·응급조치가 비어 있음 | 응답 모양이 명세와 다르다 — 아래 원본 응답을 보고 `agent/kosha.py`의 `parse_hazards`·`parse_first_aid`를 고친다 |

원본 응답 보기(응답에는 키가 들어 있지 않다):

```bash
.venv/bin/python3 -c "
from dotenv import load_dotenv; load_dotenv()
import httpx
from agent import kosha
r = httpx.get(f'{kosha.BASE}/{kosha.LIST_OP}', timeout=10, params={'serviceKey': kosha._service_key(),
              'searchWrd': '108-88-3', 'searchCnd': '1', 'numOfRows': '3', 'pageNo': '1'})
print(r.status_code); print(r.text[:1500])
"
```

### 6-4. 명세 (코드가 쓰는 값)

| 항목 | 값 | `.env`로 바꾸기 |
|---|---|---|
| 기본 주소 | `https://apis.data.go.kr/B552468/msdschem1` | `KOSHA_BASE` |
| 목록 조회 | `getChemList001` — `serviceKey`, `searchWrd`, `searchCnd`(0 국문명 · 1 CAS No), `numOfRows`, `pageNo` | `KOSHA_LIST_OP` |
| 상세 조회 | `getChemDetail{NN}1` — `serviceKey`, `chemId` (NN = MSDS 항목 번호, 02 유해성 · 04 응급조치 · 07 취급·저장 · 09 물리화학적 특성) | `KOSHA_DETAIL_OP` |

예전 주소를 써야 한다면: `KOSHA_BASE=https://apis.data.go.kr/B552468/msdschem`,
`KOSHA_LIST_OP=getChemList`, `KOSHA_DETAIL_OP=getChemDetail{section:02d}`.

검색 결과는 **CAS가 같거나 이름이 정확히 같은 물질만** 고른다("톨루엔"으로 검색해 "톨루엔
다이아이소사이아네이트"를 고르지 않게).

## 7. 실제 LLM으로 실행

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

프론트는 똑같이 `bash sh/run_frontend.sh`. 화면 오른쪽 위가 `openai · 데이터 live`로 바뀐다.

- 두 백엔드 모두 **비동기**로 호출한다 — 모델이 답을 쓰는 동안에도 다른 요청(다른 사람의 질문,
  `/health`)이 멈추지 않는다.
- 안내문과 대화 답변은 **스트리밍**으로 받는다 — 글자가 생기는 대로 화면에 나온다.
- 실모델이 쓴 안내문도 코드 검수(validate)를 거친다. 수집된 자료에 없는 수치(°C·%·ppm 등)나
  H코드를 쓰면 반려하고 다시 쓰게 한다(최대 3회). 과정 보기에서 반려 사유를 볼 수 있다.

실모델은 질문에서 물질명을 스스로 뽑으므로 영문명·CAS로도 물어볼 수 있다.

```bash
curl -s -X POST localhost:8100/chat -H 'Content-Type: application/json' \
  -d '{"message":"anisole 취급 주의사항 알려줘","thread_id":"x1"}' \
  | python3 -c "import json,sys; print(json.load(sys.stdin)['reply'])"
```

## 8. 자동 테스트

코드를 고친 뒤 기존 기능이 깨지지 않았는지 한 번에 확인한다. 인터넷·API 키 없이 돌아가고,
`.env`에 실제 키가 있어도 쓰지 않는다(테스트는 항상 fake·sample·KOSHA 끔으로 고정).

```bash
bash sh/run_tests.sh
```
마지막 줄에 `NN passed`가 나오면 전부 통과다. `failed`가 있으면 위쪽에 어느 파일 몇 번째 줄에서
무엇이 달랐는지 나온다.

| 파일 | 확인하는 것 |
|---|---|
| `tests/test_chem_rules.py` | 물질·약어 인식, 섭취·합성 의도, 한국어 H문구, 반응성 태그, 혼촉 규칙, 단독 주의 |
| `tests/test_validate.py` | 검수 기준 — 형식·필수 문구, 지어낸 수치·H코드 반려 |
| `tests/test_lookup.py` | PubChem 파서·SQLite 캐시, KOSHA 파서(가짜 응답), 조회 순서 KOSHA → PubChem → 샘플, 오류 메시지에 키가 안 찍히는지 |
| `tests/test_llm_async.py` | OpenAI 스트리밍·JSON 모드, Ollama 비동기 호출 (가짜 HTTP 응답) |
| `tests/test_graph.py` | 그래프 전체 흐름 — test_queries.md의 대표 질문들 |
| `tests/test_server.py` | FastAPI + 진짜 MCP 서버(subprocess) 연결, `/chat`, `/chat/stream` |

파일 하나만 돌리기: `.venv/bin/python3 -m pytest tests/test_chem_rules.py -q`

## 9. 스크립트 없이 직접 실행 (Windows 포함)

### macOS/Linux

```bash
source .venv/bin/activate
LLM_BACKEND=fake python3 server.py        # 터미널 1
python3 web_demo_server.py                 # 터미널 2
python3 -m pytest -q                       # 테스트
```

### Windows PowerShell

```powershell
python -m venv .venv
.venv\Scripts\Activate.ps1
pip install -r requirements.txt
if (!(Test-Path .env)) { Set-Content .env "CHEM_DATA_MODE=live`nOPENAI_API_KEY=`nDATA_GO_KR_API_KEY=" }

# 터미널 1
$env:LLM_BACKEND="fake"; python server.py
# 터미널 2 (.venv 활성화 후)
python web_demo_server.py
# 테스트
python -m pytest -q
```

PowerShell에서 호출:

```powershell
Invoke-RestMethod -Method Post -Uri http://localhost:8100/chat `
  -ContentType "application/json; charset=utf-8" `
  -Body '{"message":"톨루엔이랑 질산 주의사항","thread_id":"w1"}' | Select-Object -ExpandProperty reply
```

## 10. 그 밖의 실행 옵션

```bash
# v1(구버전 단순 파이프라인)과 비교
SUPERVISOR_VERSION=v1 bash sh/run_agent_fake.sh

# 오프라인 데모 (인터넷 없이 샘플 6종만)
CHEM_DATA_MODE=sample bash sh/run_agent_fake.sh

# 서버 재시작 후에도 대화를 기억하는지 확인
# (run_agent_*.sh는 시작할 때 session.db를 지우므로 server.py를 직접 실행)
source .venv/bin/activate
LLM_BACKEND=fake python3 server.py     # 이름 알려주고 Ctrl+C → 다시 실행 → 이름 물어보기
```

## 11. 문제 해결

| 증상 | 해결 |
|---|---|
| `ModuleNotFoundError` (pytest 포함) | `bash sh/setup_venv.sh` 재실행. 직접 실행 시 `.venv` 활성화 확인 |
| `address already in use` (8100/8600) | `lsof -ti:8100 \| xargs kill -9` (Windows: `netstat -ano \| findstr 8100` → `taskkill /PID 번호 /F`) |
| 채팅 화면 제목에 `{{ backendLabel }}`가 그대로 보임 | Vue를 unpkg.com에서 못 받은 것 — 인터넷/사내 방화벽 확인 |
| 화면은 뜨는데 답이 안 옴 | 백엔드(8100)가 꺼져 있음 — 터미널 1 확인 |
| 출처가 계속 `샘플 데이터` | PubChem 접속 실패 — 5번의 확인용 curl 실행 |
| KOSHA를 켰는데 출처가 PubChem | 안내문의 메모에 KOSHA 실패 이유가 있다 — 6-3 확인 |
| 예전 조회 결과가 계속 나옴 | 캐시 때문 — 서버를 끄고 `rm lookup_cache.db` |
| `.env가 없습니다` | 3번의 명령으로 만든다 (ollama/openai 스크립트는 `.env` 필수) |
| 이전 대화가 섞임 | `thread_id`를 바꾸거나 `session.db` 삭제 |
| 다른 사람에게 배포 | `bash sh/make_student_zip.sh` → `.env`(키)를 뺀 zip이 상위 폴더에 생성 |