# chem_safety_agent — 화학물질 사용 전 안전 안내 에이전트

실험 전에 "톨루엔이랑 질산 쓸 건데 주의사항 알려줘"라고 물으면, 물질별 SDS 정보를 조회하고
유해성 유형별 취급 지침과 **혼촉(같이 쓰면 위험한 조합)** 점검 결과를 합쳐
**6개 항목 안내문**(유해성·보호구·취급·혼용·응급조치·보관/폐기)을 작성해 주는 에이전트다.

SKALA `all_total_vue`(운영 장애 대응 에이전트)의 뼈대 — FastAPI lifespan 싱글턴, LangGraph
StateGraph, MCP 멀티 서버, SSE 스트리밍, SQLite 세션, Vue 채팅 화면, LLM 백엔드 3종 — 를
그대로 쓰고, **업무 지식(MCP 도구·데이터·판단 규칙)만 화학안전용으로 교체**했다.

> ⚠️ 교육·데모용이다. 농도·제조사에 따라 GHS 분류가 달라질 수 있다.
> 실제 사용 전에는 반드시 공식 SDS(KOSHA MSDS, 제조사 SDS)와 기관 안전 규정을 따를 것.

---

## 처음 실행하기 — 순서대로 따라 하기

API 키도, AI 모델 설치도 필요 없는 **fake 모드**로 채팅 화면까지 띄우는 방법이다.
아래 명령은 한 줄씩 복사해 터미널에 붙여 넣고 Enter를 누르면 된다.
(macOS 기준. Windows는 [Windows에서 실행하기](#windows에서-실행하기) 참고)

### 1단계. 터미널 열기

- macOS: `Cmd + Space` → "터미널" 입력 → Enter
- 터미널은 **총 2개**가 필요하다.
  - 터미널 ① : 백엔드(에이전트, 8100번 포트)
  - 터미널 ② : 프론트(채팅 화면, 8600번 포트)
- 새 터미널 창은 터미널에서 `Cmd + N`으로 연다.

### 2단계. 프로젝트 폴더로 이동

zip을 `다운로드` 폴더에 받았다면:

```bash
cd ~/Downloads
unzip chem_safety_agent.zip
cd chem_safety_agent
```

이후 모든 명령은 **이 `chem_safety_agent` 폴더 안에서** 실행해야 한다.
현재 위치는 `pwd`로 확인할 수 있다(끝이 `/chem_safety_agent`이면 맞다).

### 3단계. 설치 (처음 한 번만)

```bash
python3 --version
```
`Python 3.10` 이상이 나와야 한다. 없거나 낮으면 https://www.python.org 에서 설치하고 터미널을 새로 연다.

```bash
bash sh/setup_venv.sh
printf "CHEM_DATA_MODE=live\nOPENAI_API_KEY=\n" > .env
```
- 첫 줄: 이 프로젝트 전용 Python 환경(`.venv` 폴더)을 만들고 필요한 라이브러리를 설치한다.
  1~3분 걸리고, 마지막에 **`필요 패키지 전부 import 성공`**이 나오면 성공이다.
- 둘째 줄: 설정 파일 `.env`를 만든다. fake 모드는 내용을 고칠 필요가 없다.
  OpenAI를 쓸 때만 `.env`를 열어 `OPENAI_API_KEY=` 뒤에 본인 키를 넣는다.

설정이 맞는지 확인한다.

```bash
grep CHEM_DATA_MODE .env
```
**`CHEM_DATA_MODE=live`** 가 나오면 된다. `sample`이 나오면(예전에 만든 `.env`) 파일을 열어
`live`로 고친다. `live`는 PubChem(무료, 키 불필요)에서 물질 정보를 실시간으로 가져오는 모드다.

### 4단계. 백엔드(에이전트) 켜기 — 터미널 ①

```bash
bash sh/run_agent_fake.sh
```
아래처럼 나오면 성공이다. **이 터미널은 닫지 말고 그대로 둔다** (닫으면 에이전트가 꺼진다).

```
{
    "status": "ok",
    ...
    "llm_backend": "fake",
    "chem_data_mode": "live",
    ...
}
################################################################
 에이전트가 떠 있습니다 (http://127.0.0.1:8100, LLM_BACKEND=fake)
################################################################
```

이 스크립트는 시작할 때 이전 대화 기록(`session.db`)을 지운다. 매번 깨끗한 상태로 시작된다.

### 5단계. 프론트(채팅 화면) 켜기 — 터미널 ②

`Cmd + N`으로 **새 터미널**을 열고, 다시 프로젝트 폴더로 이동한다.

```bash
cd ~/Downloads/chem_safety_agent
```

그다음 **A, B 중 하나**로 켠다. 결과는 같다. 처음이라면 **A를 권장**한다.

**A. 스크립트로 켜기 (권장)**

```bash
bash sh/run_frontend.sh
```
스크립트가 알아서 ① `.venv`의 Python을 쓰고, ② 백엔드(8100)가 켜져 있는지 먼저 확인하고,
③ 예전에 켜 둔 프론트가 남아 있으면 끄고 새로 켠다. 성공하면 아래가 나온다.

```
################################################################
 프론트엔드가 떠 있습니다 → http://localhost:8600
################################################################
```

`ERROR: 백엔드(http://127.0.0.1:8100)가 응답하지 않습니다`가 나오면 4단계 터미널 ①이 꺼진 것이다.
4단계를 먼저 다시 실행한다.

**B. 직접 켜기**

```bash
source .venv/bin/activate
python web_demo_server.py
```
- 첫 줄은 가상환경을 켠다. 성공하면 줄 맨 앞에 `(.venv)`가 붙는다. **빼먹으면 `ModuleNotFoundError`가 난다.**
- `브라우저에서 http://localhost:8600 을 열어보세요`가 나오면 성공이다.
- B는 백엔드가 켜져 있는지 확인하지 않는다. 백엔드가 꺼져 있으면 화면은 열리지만 답이 오지 않는다.

어느 쪽이든 **이 터미널도 닫지 말고 그대로 둔다**.

### 6단계. 브라우저에서 사용하기

1. Chrome이나 Safari 주소창에 **http://localhost:8600** 입력 → Enter
   (이미 열려 있었다면 `Cmd + R`로 새로고침)
2. 오른쪽 위에 초록 점과 `fake · 데이터 live`가 보이면 백엔드까지 연결된 것이다.
   빨간 점과 `서버 연결 안 됨`이 보이면 4단계(터미널 ①)를 먼저 확인한다.
3. 첫 화면의 **예시 질문을 누르거나**, 아래 입력창에 질문을 쓰고 **확인**을 누른다.
   물질 이름을 치기 시작하면 등록된 물질이 **자동완성**으로 뜬다. 같은 창에서 이어서 계속 물어봐도 된다.

![시작 화면](doc/images/home.png)

| 이렇게 물어보면 | 이렇게 답한다 |
|---|---|
| `톨루엔이랑 질산 같이 써도 돼?` | 안전 라벨 + "같이 쓸 때"에 **위험**(화재·폭발) 표시 |
| `염산과 NaOH로 중화 실험할 거야` | 화학식(NaOH)도 인식. 산·염기를 섞을 때의 주의 |
| `락스랑 염산 섞어도 돼?` | "같이 쓸 때"에 염소 기체 위험 |
| `자일렌을 합성할거야` | 노란 띠: 합성 절차는 안내하지 않음 + 자일렌 안전 정보 |
| `질산을 마실거야` | 빨간 띠: 섭취 금지 + **119 신고 / 109 상담** 버튼(휴대폰에서 누르면 바로 전화) |
| `그럼 보관은?` | 바로 앞 질문의 물질을 이어받아 답한다 |
| `DMA` | **확인할 수 없는 물질** 카드 + 후보 버튼 2개 — 누르면 그 물질로 다시 확인한다 |
| `날씨 어때?` | "알 수 없습니다" — 화학물질 안전 안내만 한다고 답한다 |

4. 답이 나오는 동안 **진행 막대 8칸**이 차례로 채워진다
   (질문 분류 → 계획 → 점검 범위 → SDS 조회 → 지침 보충 → 혼촉 점검 → 작성 → 검수).
   **과정 보기**를 누르면 단계별 기록과, 검수에서 반려된 초안까지 볼 수 있다.
5. 결과는 **사용 전 안전 라벨** 카드로 나온다.
   - **물질별 줄**: GHS 그림문자(빨간 마름모), 신호어(위험/경고), CAS 번호, 주요 유해·위험 문구
   - **같이 쓸 때**: 물질이 2종 이상이면 모든 쌍을 점검한다. 빨강 = 위험, 초록 = 규칙상 위험 없음, 회색 = 확인 못 해 점검 불가
   - **보호구**: 공통 + 유해성 유형별 보호구
   - **응급조치 / 취급·보관·폐기 / 안내문 전문**: 눌러서 펼친다
   - 오른쪽 위 **복사**(안내문 텍스트 복사)·**인쇄**(이 라벨만 인쇄 — 인쇄 창에서 "PDF로 저장"도 가능)
6. 처음부터 다시 하려면 오른쪽 위 **새 대화**를 누른다.

![사용 전 안전 라벨](doc/images/label_card.png)

**알아둘 점**

- 이 에이전트는 **모르는 것은 모른다고 답한다.** 확인하지 못한 물질의 유해성을 추측해서 채우지 않고,
  `[확인 불가]`와 이유를 보여준 뒤 정식 이름이나 CAS 번호를 다시 요청한다.
- fake 모드가 알아보는 물질: 샘플 6종(톨루엔·아세톤·메탄올·질산·염산·수산화나트륨) +
  이름 사전 `data/name_map.json`의 약 40종(자일렌·헥산·에탄올·황산·락스·니트로글리세린 등) +
  CAS 번호(예: `64-17-5`).
- 사전에 없는 한국어 이름(예: 아니솔)은 PubChem이 한국어를 몰라 조회하지 못한다. 영문명이나 CAS로
  물어보거나, 사전에 추가하거나([물질·규칙 추가하기](#물질규칙-추가하기)), 실모델(Ollama/OpenAI)을 쓴다.
- fake 모드에서는 PubChem에서 가져온 내용(응급조치 등)이 **영어 원문 그대로** 나온다.
  실모델을 쓰면 한국어로 정리된다.

### 7단계. 끄기

터미널 ②와 터미널 ①에서 각각 `Ctrl + C`를 누른다. (B 방법을 썼다면 `deactivate`로 가상환경도 끌 수 있다)

### 다음에 다시 켤 때

3단계(설치)는 건너뛴다.

```bash
# 터미널 ①
cd ~/Downloads/chem_safety_agent
bash sh/run_agent_fake.sh

# 터미널 ② (새 창)
cd ~/Downloads/chem_safety_agent
bash sh/run_frontend.sh
# → 브라우저 http://localhost:8600
```

### 자주 막히는 곳

| 증상 | 원인과 해결 |
|---|---|
| `No such file or directory: sh/...` 또는 `can't open file` | 프로젝트 폴더 밖에 있다. `cd ~/Downloads/chem_safety_agent` 후 다시 실행 |
| `python3: command not found` | Python 미설치. python.org에서 설치 후 터미널을 새로 연다 |
| `ModuleNotFoundError` | 설치가 안 됐거나(→ `bash sh/setup_venv.sh`), B 방법에서 가상환경을 안 켰다(→ `source .venv/bin/activate`) |
| `address already in use` | 이전에 켠 서버가 남아 있다. `lsof -ti:8100 \| xargs kill -9` 와 `lsof -ti:8600 \| xargs kill -9` 후 다시 실행 |
| 터미널 ①에 `SyntaxError` / `IndentationError` | 코드를 고치다 들여쓰기가 어긋났다. 메시지의 `파일명, line 숫자` 위치를 확인 |
| 브라우저에 "사이트에 연결할 수 없음" | 터미널 ②(프론트)가 꺼졌다. 5단계 다시 실행 |
| 화면 제목 옆에 `{{ backendLabel }}`이 그대로 보임 | 화면 라이브러리(Vue)를 인터넷에서 못 받았다. 인터넷 연결 확인 |
| 보내기를 눌러도 답이 없음 | 터미널 ①(백엔드)이 꺼졌다. 4단계 다시 실행 |
| 화면 제목 옆이 `데이터: sample` | `.env`의 `CHEM_DATA_MODE`를 `live`로 고치고 터미널 ①을 다시 켠다 |
| 답에 `PubChem 호출 오류` | PubChem에 접속이 안 된다. 아래 명령으로 확인 — `{"IdentifierList": {"CID": [1140]}}`가 나오면 정상 |

```bash
curl -s https://pubchem.ncbi.nlm.nih.gov/rest/pug/compound/name/toluene/cids/JSON
```

### Windows에서 실행하기

`.sh` 스크립트는 Windows에서 바로 실행되지 않으므로 PowerShell에서 직접 입력한다(5단계의 B 방법과 같다).
(시작 메뉴 → "PowerShell" 검색 → 실행. 압축은 zip 파일 우클릭 → "압축 풀기")

```powershell
# 처음 한 번 (설치)
cd $HOME\Downloads\chem_safety_agent
python --version
python -m venv .venv
.venv\Scripts\Activate.ps1
pip install -r requirements.txt
Set-Content .env "CHEM_DATA_MODE=live`nOPENAI_API_KEY="
```

`Activate.ps1` 실행이 막히면 먼저 `Set-ExecutionPolicy -Scope CurrentUser RemoteSigned`를 실행하고 `Y`.

```powershell
# PowerShell ① — 백엔드
cd $HOME\Downloads\chem_safety_agent
.venv\Scripts\Activate.ps1
$env:LLM_BACKEND="fake"; python server.py
```

```powershell
# PowerShell ② — 프론트 (새 창)
cd $HOME\Downloads\chem_safety_agent
.venv\Scripts\Activate.ps1
python web_demo_server.py
```

그다음 6단계처럼 브라우저에서 http://localhost:8600 을 연다.
직접 켜는 방식은 이전 대화 기록(`session.db`)을 지우지 않는다. 처음부터 하려면 서버를 끄고 `session.db`를 삭제한다.

---

> 📘 curl로 직접 호출하기, Ollama/OpenAI 실모델 실행, 서버 재시작 후 대화 기억 확인 등 더 자세한 내용은
> **[doc/run_guide.md](doc/run_guide.md)**, 질문별 기대 결과는 [doc/test_queries.md](doc/test_queries.md).

## 실행 모드

| 백엔드 스크립트 | LLM | 비용 | 비고 |
|---|---|---|---|
| `sh/run_agent_fake.sh` | 규칙 기반 fake | 없음 | 처음 돌려볼 때. 결과가 매번 같다 |
| `sh/run_agent_ollma.sh` | 로컬 Ollama (qwen3:0.6b / 8b) | 없음 | 먼저 `bash sh/setup_ollama.sh` |
| `sh/run_agent_openai.sh` | OpenAI (gpt-4o-mini / gpt-4o) | **과금** | `.env`에 `OPENAI_API_KEY` |

프론트(`bash sh/run_frontend.sh` 또는 `python web_demo_server.py`)는 어떤 백엔드든 똑같이 쓴다.

### 데이터 출처 (`.env`의 `CHEM_DATA_MODE`)

| 값 | 동작 |
|---|---|
| `live` (기본) | **PubChem** 실시간 조회(무료, API 키 불필요). 한국어 이름은 `data/name_map.json`으로 CAS를 찾아 조회. 인터넷이 안 되면 샘플 6종은 샘플 데이터로 대신한다 |
| `sample` | `data/substances.json`의 6종만 조회. 인터넷 불필요(오프라인 데모용) |

## 파이프라인 (agent/graph.py)

```
route ─ chat ──▶ chat_answer ─▶ 끝
  └ safety ─▶ plan ─▶ gate ─▶ research ─▶ replan_check ─┬▶ analysis ─▶ write ─▶ validate ─▶ finalize
                                  ▲                     │                ▲           │
                                  └── 지침 부족(1회) ─────┘                └─ 반려(≤3회) ┘
```

| 노드 | 하는 일 | 판단 주체 |
|---|---|---|
| route | 화학안전 문의(safety)인지 일반 대화(chat)인지 분류. **모델이 chat이라 해도 물질 이름·안전 키워드가 보이면 safety로 보낸다** | LLM + 코드 |
| plan | 조사할 물질 목록 수립, 섭취·합성 의도 인식. 물질이 없으면 직전 질문의 물질을 이어받는다 | LLM + 코드 보정 |
| gate | 물질 2종 이상이면 혼촉 점검을 반드시 넣고, 1종이면 뺀다 | 코드 |
| research | MCP Tool `get_substance_safety`(SDS)와 Resource `safety-guide://hazard/{type}`(지침)를 병렬 호출, 일시 오류 재시도. 약어는 추측해 조회하지 않는다 | 코드 |
| replan_check | 지침이 없으면 GHS 분류(H코드)에서 유해성 유형을 판정해 유형별 지침 추가 조사 | 코드 |
| analysis | 혼촉 규칙표로 물질 쌍 판정(산화제×인화성, 락스×산, 시안화물×산, 산×염기 등). 확인하지 못한 물질은 "점검 불가"로 밝힌다 | 코드 |
| write | 수집된 사실만으로 안내문 작성. 하나도 확인하지 못했으면 `[확인 불가]`로 짧게 답한다 | LLM |
| validate | 6개 항목·출처·물질 누락, 섭취 시 119 경고, 합성 시 "합성 절차 미안내" 문구를 검사 | 코드 |
| finalize | 안내문 텍스트와 함께 화면용 구조화 데이터(`notice`: 그림문자 코드·신호어·혼촉 쌍·보호구·약어 후보)를 만들어 보낸다 | 코드 |

fake 백엔드는 원본의 연출 두 가지를 유지한다 — plan이 1종일 때도 혼촉 점검을 끼워 넣는 자기모순(→ gate가
제거), write 1차 초안을 일부러 부실하게(→ validate 반려 후 재작성). 첫 SDS 조회 1회는 일부러
타임아웃을 내 재시도 로직을 보여준다.

## 원본(all_total_vue)과 달라진 점

| 구분 | 원본 (장애 대응) | 이 프로젝트 (화학안전) |
|---|---|---|
| MCP Tool | `get_monitoring_status` | `get_substance_safety` (live/sample), `get_kosha_msds`(확장 자리) |
| MCP Resource | `runbook://incident-response/{type}` | `safety-guide://hazard/{type}` 9종 |
| MCP Prompt | `incident_analysis_prompt` | `safety_notice_prompt` |
| 데이터 | `monitoring.json`, runbook 6종 | `substances.json`, `name_map.json`, `safety_guides/` 9종 |
| gate | needs_analysis=False면 analysis 제거 | 물질 수로 혼촉 점검 추가/제거 |
| analysis | LLM이 시스템 패턴 비교 | 코드 규칙표로 혼촉 판정 |
| validate | 30자 이상 + "1)" | 6개 항목·출처·물질 누락·의도 경고 검사 |
| 공통 코드 | 프롬프트·계획 보정이 ollama/openai에 복붙 | `agent/prompts.py`로 통합 |
| 도메인 규칙 | 노드 안에 흩어짐 | `agent/chem_rules.py`에 모음 |

## 소스 구조

```
chem_safety_agent/
├── README.md              # 이 문서
├── server.py              # 백엔드 — FastAPI, /health·/chat·/chat/stream·/substances (:8100)
├── web_demo_server.py     # 프론트 서버 — 채팅 화면 제공 + 8100으로 요청 중계 (:8600)
├── web_chat.html          # 채팅 화면 (Vue 3) — 안전 라벨 카드, 진행 막대, 자동완성
├── mcp_server.py          # MCP 서버 "chem" — SDS Tool, 취급 지침 Resource, 안내문 Prompt
├── mcp_server_docs.py     # MCP 서버 "docs" — 멀티 서버 탐색 데모용 decoy (사용 안 함)
├── agent/
│   ├── graph.py           # LangGraph 노드·간선
│   ├── chem_rules.py      # 물질명·약어 인식, 의도 인식, H코드 분류, 혼촉 규칙, 보호구
│   ├── pubchem.py         # PubChem 조회·파싱 (live 모드)
│   ├── prompts.py         # ollama/openai 공용 프롬프트·계획 보정·의도 경고
│   ├── fake_llm.py / ollama_llm.py / openai_llm.py
│   ├── mcp_client.py      # 다중 MCP 세션 + find_prompt/find_resource_template
│   ├── supervisor_v1.py   # legacy 선형 파이프라인 (SUPERVISOR_VERSION=v1)
│   └── deps.py · retry.py · session_store.py
├── data/
│   ├── substances.json    # 샘플 SDS 요약 6종 (sample 모드·오프라인 대체용)
│   ├── name_map.json      # 한국어 이름 → 영문명·CAS 사전 (live 모드 조회용) + 혼촉 판정 tag
│   └── safety_guides/     # explosive·water-reactive·oxidizer·flammable·toxic·corrosive·health-hazard·irritant·general
├── sh/                    # setup_venv · setup_ollama · run_agent_fake/ollma/openai · run_frontend · make_student_zip
└── doc/                   # run_guide.md(상세 실행), test_queries.md, images/
```

## 물질·규칙 추가하기

- **이름 사전에 물질 추가 (가장 쉬움)**: `data/name_map.json`에 한 줄 추가하면 live 모드에서 PubChem으로 조회된다.
```json
  "anisole": {"name_ko": "아니솔", "name_en": "Anisole", "cas": "100-66-3", "aliases": ["아니솔", "anisole"], "tags": ["organic-solvent"]},
```
  `tags`는 혼촉 판정용이다(`acid`·`base`·`organic-solvent`·`oxidizing-acid`·`hypochlorite`·`ammonia`·`cyanide`·`explosive`·`water-reactive`).
  CAS 번호는 반드시 공식 자료에서 확인해 넣는다.
- **약어 추가**: 여러 물질을 가리키는 약어는 `agent/chem_rules.py`의 `AMBIGUOUS_ABBR`에 후보와 함께 넣는다.
- **오프라인 샘플 추가**: `data/substances.json`에 GHS 정보까지 넣으면 인터넷 없이도 조회된다.
- **혼촉 규칙 추가**: `agent/chem_rules.py`의 `INCOMPATIBILITY_RULES`에 (조건 A, 조건 B, 위험, 권고)를 추가.
- **지침 추가**: `data/safety_guides/{유형}.md` 작성 후 `HAZARD_LABELS`·`_H_CODE_RULES`에 유형 등록.
  `보호구:`·`보관:`·`폐기:`로 시작하는 줄은 fake 안내문에 그대로 인용된다.

추가한 뒤에는 터미널 ①의 백엔드를 `Ctrl + C`로 끄고 다시 켜야 반영된다.

## 확장: 안전보건공단(KOSHA) MSDS Open API

공공데이터포털 "한국산업안전보건공단_물질안전보건자료 조회 서비스"(개발계정 자동승인, 무료, XML)를
붙일 자리로 `mcp_server.py`에 `get_kosha_msds` Tool이 만들어져 있다(현재는 `available: false` 반환).
KOSHA는 한국어 물질명으로 조회되므로, 붙이면 사전에 없는 한국어 이름 문제도 해결된다.

1. `.env`에 `DATA_GO_KR_API_KEY=발급받은키` (코드나 깃에 키를 직접 쓰지 않는다)
2. 포털 마이페이지의 활용가이드에서 요청주소·상세기능(물질 검색 → 항목별 상세)·파라미터 확인
3. `get_kosha_msds`를 구현해 `get_substance_safety`와 같은 dict 모양으로 반환
4. `get_substance_safety`에서 KOSHA → (실패 시) PubChem → (실패 시) sample 순으로 조회

## 참고

- PubChem PUG-View: `https://pubchem.ncbi.nlm.nih.gov/rest/pug_view/data/compound/{cid}/JSON?heading=...`
  — `agent/pubchem.py`는 `GHS Classification`, `First Aid Measures`, `Handling and Storage` 세 섹션만 받는다.
  호출 빈도 제한이 있어 결과를 캐시한다.
- 공공데이터포털 KOSHA MSDS: https://www.data.go.kr/data/15157612/openapi.do