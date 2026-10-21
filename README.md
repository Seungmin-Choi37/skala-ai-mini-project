# chem_safety_agent — 화학물질 사용 전 안전 안내 에이전트

실험 전에 "톨루엔이랑 질산 쓸 건데 주의사항 알려줘"라고 물으면, 물질별 SDS 정보를 조회하고
유해성 유형별 취급 지침과 **혼촉(같이 쓰면 위험한 조합)** 점검 결과를 합쳐
**6개 항목 안내문**(유해성·보호구·취급·혼용·응급조치·보관/폐기)을 작성해 주는 에이전트다.

SKALA `all_total_vue`(운영 장애 대응 에이전트)의 뼈대 — FastAPI lifespan 싱글턴, LangGraph
StateGraph, MCP 멀티 서버, SSE 스트리밍, SQLite 세션, Vue 채팅 화면, LLM 백엔드 3종 — 를
그대로 쓰고, **업무 지식(MCP 도구·데이터·판단 규칙)만 화학안전용으로 교체**했다.

> 📄 **최종 보고서: [REPORT.md](REPORT.md)** — 문제·구현·적용 기술(LangGraph·MCP·RAG·LangSmith·LLM-as-a-Judge)·결과·교훈

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

### 2단계. 프로젝트 받고 폴더로 이동

**A, B 중 받은 방법에 맞는 것 하나만** 따라 한다.

**A. 깃허브에서 받기 (git clone)**

```bash
cd ~/Downloads
git clone https://github.com/Seungmin-Choi37/skala-ai-mini-project.git
cd skala-ai-mini-project
```
`git: command not found`가 나오면 git이 없는 것이다. macOS는 안내 창이 뜨면 "설치"를 누르고, 끝나면 다시 실행한다.

**B. zip 파일로 받기**

zip을 `다운로드` 폴더에 받았다면:

```bash
cd ~/Downloads
unzip chem_safety_agent.zip
cd chem_safety_agent
```

이후 모든 명령은 **이 프로젝트 폴더 안에서** 실행해야 한다.
현재 위치는 `pwd`로 확인할 수 있다(끝이 위에서 `cd`한 폴더 이름이면 맞다).

> 💡 **폴더 이름에 띄어쓰기가 있으면** (예: `chem_safety_agent 2`) 따옴표로 감싼다.
> `cd "chem_safety_agent 2"` — 따옴표가 없으면 `no such file or directory`가 난다.
>
> 💡 **VS Code를 쓰면 더 쉽다.** VS Code에서 `파일 → 폴더 열기`로 프로젝트 폴더를 연 뒤
> `터미널 → 새 터미널`을 열면, 터미널이 이미 프로젝트 폴더에 있어서 `cd`가 필요 없다.

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
  **`.env`가 이미 있으면 둘째 줄은 실행하지 않는다**(덮어써서 넣어 둔 키가 지워진다).

설정이 맞는지 확인한다.

```bash
grep CHEM_DATA_MODE .env
```
**`CHEM_DATA_MODE=live`** 가 나오면 된다. `sample`이 나오면(예전에 만든 `.env`) 파일을 열어
`live`로 고친다. `live`는 PubChem(무료, 키 불필요)에서 물질 정보를 실시간으로 가져오는 모드다.

마지막으로 잘 설치됐는지 자동 테스트로 확인한다(인터넷·API 키 불필요, 15초쯤 걸린다).

```bash
bash sh/run_tests.sh
```
마지막 줄에 **`101 passed`**(숫자는 달라질 수 있다)처럼 `passed`만 나오면 성공이다.
`failed`나 `error`가 나오면 `bash sh/setup_venv.sh`를 다시 실행한 뒤 한 번 더 해 본다.

> 💡 `sh/...` 파일 앞에는 항상 `bash`를 붙여 실행한다. 그러면 `chmod +x`(실행 권한 주기)를 할 필요가 없다.

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

`Cmd + N`으로 **새 터미널**을 열고, 다시 프로젝트 폴더로 이동한다(2단계에서 `cd`한 폴더와 같은 곳).

```bash
cd ~/Downloads/chem_safety_agent
```
VS Code라면 터미널 패널 오른쪽 위의 **`+`** 버튼으로 새 터미널을 열면 되고, `cd`는 필요 없다.

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
| `금속나트륨이랑 염산 같이 보관해도 돼?` | 물 반응성×산 **위험** + 노란 띠 "섞지 않아도 주의"(물·습기 금지) |
| `다이에틸에테르 써도 돼?` | 노란 띠: 오래 두면 폭발성 과산화물이 생김 — 개봉일 기록 |
| `n-뷰틸리튬으로 합성할 거야` | 합성 절차 미안내 + 노란 띠 "공기와 닿으면 저절로 불이 붙음"(자연발화성) |
| `자연발화하는 물질은 뭐가 있어?` | **성질로 찾기** 카드 — AI가 후보를 내고, SDS에서 H250이 확인된 물질만 보여 준다. 누르면 그 물질의 라벨로 이어진다 |
| `DMA` | **확인할 수 없는 물질** 카드 + 후보 버튼 2개 — 누르면 그 물질로 다시 확인한다 |
| `공기 중에 수증기를 흡수하는 물질은?` | **일반 화학 지식** 카드 — 벡터 DB에서 근거 문서(흡습성과 조해성)를 찾아(RAG) 답한다. 실제 AI 모드에서는 문서를 근거로 설명하고 답에 나온 물질마다 "SDS 확인됨/확인 못 함"을 붙이고, fake 모드는 문서 문장을 그대로 옮긴다 |
| `흄후드는 언제 써야 해?` | 같은 방식 — 근거 문서 "흄후드는 언제, 어떻게 써야 하나"와 유사도가 함께 나온다 |
| `날씨 어때?` | "알 수 없습니다" — 화학물질 안전 안내만 한다고 답한다 |

4. 답이 나오는 동안 **진행 막대 8칸**이 차례로 채워진다
   (질문 분류 → 계획 → 점검 범위 → SDS 조회 → 지침 보충 → 혼촉 점검 → 작성 → 검수).
   **과정 보기**를 누르면 단계별 기록과, 검수에서 반려된 초안까지 볼 수 있다.
5. 결과는 **사용 전 안전 라벨** 카드로 나온다.
   - **노란 띠**: 섞지 않아도 위험한 성질(과산화물 생성, 물 반응성)이 있으면 물질별로 표시
   - **물질별 줄**: GHS 그림문자(빨간 마름모), 신호어(위험/경고), CAS 번호, 주요 유해·위험 문구(한국어)
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
  물어보거나, 사전에 추가하거나([물질·규칙 추가하기](#물질규칙-추가하기)),
  안전보건공단 MSDS를 켠다([KOSHA 켜기](#안전보건공단kosha-msds-켜기)).
- PubChem의 유해·위험 문구(H코드)는 한국어 표로 바꿔 보여준다. 응급조치·취급 문장은 뜻이 바뀌지
  않도록 **영어 원문 그대로** 둔다(fake 모드). 실모델을 쓰면 한국어로 정리된다.
- 물질을 확인하지 못해도 이름만으로 아는 위험 조합(락스+암모니아 등)은 "이름만으로도 알 수 있는 위험"으로 알린다.
- 한 번 찾은 물질은 `lookup_cache.db`에 30일 보관해 다음부터 바로 나온다.

### 7단계. 끄기

터미널 ②와 터미널 ①에서 각각 `Ctrl + C`를 누른다. (B 방법을 썼다면 `deactivate`로 가상환경도 끌 수 있다)

### (선택) 실제 AI(OpenAI)로 켜기

fake 모드는 정해진 규칙으로만 답한다. 실제 AI가 안내문을 쓰게 하려면 4단계만 바꾸면 된다.
⚠️ **질문할 때마다 OpenAI 계정에 요금이 청구된다.**

1. OpenAI 키를 준비한다. https://platform.openai.com → 로그인 → API keys → `Create new secret key`.
   `sk-`로 시작하는 키가 나오면 복사한다(이 창을 닫으면 다시 볼 수 없다).
2. `.env` 파일을 연다.
```bash
   open -e .env
```
   (macOS 텍스트 편집기로 열린다. VS Code를 쓰면 왼쪽 파일 목록에서 `.env`를 클릭해도 된다.
   Windows는 `notepad .env`)
3. `OPENAI_API_KEY=` 바로 뒤에 띄어쓰기 없이 키를 붙여 넣고 저장한다(`Cmd + S`).
```ini
   CHEM_DATA_MODE=live
   OPENAI_API_KEY=sk-여기에-본인-키
```
   **이 키는 절대 화면 캡처·채팅·깃허브에 올리지 않는다.** `.env`는 `.gitignore`에 들어 있어 깃에 올라가지 않는다.
   키가 노출됐다면 OpenAI 사이트에서 바로 삭제하고 새로 만든다.
4. 터미널 ①에서 fake 백엔드가 켜져 있다면 `Ctrl + C`로 끄고, 대신 이걸 실행한다.
```bash
   bash sh/run_agent_openai.sh
```
5. `계속하시겠습니까? [y/N]`가 나오면 `y`를 입력하고 Enter.
6. 터미널 ②(프론트)는 그대로 두고 브라우저를 새로고침(`Cmd + R`)한다.
   오른쪽 위가 **`openai · 데이터 live`** 로 바뀌었으면 성공이다.
   안내문이 글자 단위로 흘러나오고, "안내문 전문"이 펼쳐진 채로 보인다.

| 나오는 메시지 | 해결 |
|---|---|
| `ERROR: .env가 없습니다` | 3단계의 `printf ...` 명령으로 `.env`를 만든다 |
| `ERROR: OPENAI_API_KEY가 비어있거나…` | 위 2~3번 — 키를 넣고 저장했는지 확인 |
| 답 대신 `401` 오류 | 키가 틀렸거나 삭제된 키다. 새로 만들어 다시 넣는다 |
| 답 대신 `429` 오류 | 결제 수단이 없거나 사용 한도 초과. OpenAI 사이트의 Billing 확인 |

다시 fake 모드로 돌아가려면 터미널 ①에서 `Ctrl + C` 후 `bash sh/run_agent_fake.sh`.

### 다음에 다시 켤 때

3단계(설치)는 건너뛴다. 폴더 이름은 2단계에서 받은 이름으로 바꾼다(VS Code 터미널이면 `cd` 생략).

```bash
# 터미널 ①
cd ~/Downloads/chem_safety_agent
bash sh/run_agent_fake.sh          # 실제 AI로 켜려면 bash sh/run_agent_openai.sh

# 터미널 ② (새 창)
cd ~/Downloads/chem_safety_agent
bash sh/run_frontend.sh
# → 브라우저 http://localhost:8600
```

코드를 받거나 고친 뒤 `requirements.txt`가 바뀌었다면 `bash sh/setup_venv.sh`를 한 번 더 실행한다.

### 자주 막히는 곳

| 증상 | 원인과 해결 |
|---|---|
| `No such file or directory: sh/...` 또는 `can't open file` | 프로젝트 폴더 밖에 있다. `cd ~/Downloads/chem_safety_agent` 후 다시 실행 |
| `cd: no such file or directory` (폴더 이름에 띄어쓰기) | 따옴표로 감싼다: `cd "chem_safety_agent 2"` |
| VS Code에서 `import pytest` 등에 노란 밑줄 | ① `bash sh/setup_venv.sh` 실행 ② `Cmd + Shift + P` → `Python: Select Interpreter` → 경로에 `.venv`가 있는 것 선택 |
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
if (!(Test-Path .env)) { Set-Content .env "CHEM_DATA_MODE=live`nOPENAI_API_KEY=" }
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

> 📘 curl로 직접 호출하기, KOSHA 연결 확인, Ollama/OpenAI 실모델 실행, 서버 재시작 후 대화 기억 확인 등
> 더 자세한 내용은 **[doc/run_guide.md](doc/run_guide.md)**, 질문별 기대 결과는 [doc/test_queries.md](doc/test_queries.md).

## 자동 테스트

코드를 고친 뒤에는 아래 한 줄로 기존 기능이 그대로인지 확인한다(인터넷·API 키 불필요, 약 15초).

```bash
bash sh/run_tests.sh
```
마지막 줄이 `NN passed`면 통과다. 규칙(물질 인식·혼촉·한국어 문구), 검수(지어낸 수치 반려),
조회(PubChem·KOSHA 파서와 캐시), LLM 비동기·스트리밍, 그래프 전체 흐름, 서버+MCP 연결을 확인한다.
자세한 목록은 [doc/run_guide.md의 8번](doc/run_guide.md#8-자동-테스트).

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
| `live` (기본) | ① **안전보건공단 MSDS**(`DATA_GO_KR_API_KEY`가 있을 때, 한국어 원문) → ② **PubChem**(무료, 키 불필요. 한국어 이름은 `data/name_map.json`으로 CAS를 찾아 조회) → ③ 둘 다 실패하면 샘플 6종은 샘플 데이터로 대신한다. 찾은 결과는 `lookup_cache.db`에 30일 캐시 |
| `sample` | `data/substances.json`의 6종만 조회. 인터넷 불필요(오프라인 데모용) |

## 파이프라인 (agent/graph.py)

```
route ─ chat ──────▶ chat_answer ─▶ 끝
  ├ category ─▶ category_suggest(LLM 후보) ─▶ category_verify(H코드가 SDS에 있는 것만) ─▶ 끝
  ├ knowledge ─▶ knowledge_retrieve(RAG: 벡터 DB 검색) ─▶ knowledge_answer ─▶ knowledge_review ─┬▶ knowledge_verify ─▶ knowledge_finalize ─▶ 끝
  │                                                       ▲                              │
  │                                                       └──── 근거 없는 수치·H코드 반려(≤2회) ┘
  └ safety ─▶ plan ─▶ gate ─▶ research ─▶ replan_check ─┬▶ analysis ─▶ write ─▶ validate ─▶ finalize
                                  ▲                     │                ▲           │
                                  └── 지침 부족(1회) ─────┘                └─ 반려(≤3회) ┘
```

| 노드 | 하는 일 | 판단 주체 |
|---|---|---|
| route | LLM이 먼저 분류하고 **코드가 최종 결정**(`decide_route`): 성질+목록 질문 → category, 물질 이름·CAS → safety, 짧은 후속 질문("그럼 보관은?") → safety(앞 물질 이어받기), 화학·안전 질문 → knowledge, 나머지 → chat. 모델이 chat이라 해도 물질 이름이 보이면 safety로 보낸다 | LLM + 코드 |
| category_suggest → category_verify | "자연발화하는 것은?"처럼 **성질로 묻는 질문**. LLM이 후보 물질(CAS)을 제안 → CAS 검증 숫자로 1차 거름 → 후보마다 SDS 조회 → 그 성질의 H코드(자연발화성 H250, 물 반응성 H260 등)가 **자료에 실제로 있는 물질만** 답한다. 이름도 자료에서 가져온다 | LLM 제안 + 코드 확인 |
| knowledge_retrieve | **RAG** — MCP Tool `search_knowledge`로 벡터 DB(`vector_store.db`)에서 질문과 의미가 가까운 지식 문서(`data/knowledge` 12개 + 취급 지침 10개)를 찾는다. 관련도가 낮은 문서는 버린다 | 코드 |
| knowledge_answer | 물질 이름 없는 **일반 화학 질문**("흡습성 물질은?", "산과 염기를 섞으면 왜 열이 나?", "흄후드는 언제 써?"). 고품질 모델이 **검색 문서를 근거로** 설명하고, 언급한 물질을 CAS와 함께 낸다 | LLM |
| knowledge_review | 답에 **근거 문서에 없는** 수치(°C·%·ppm 등)·H코드가 있으면 반려하고 다시 쓰게 한다(최대 2회). 끝까지 남으면 지우지 않고 "확인하지 않은 값" 경고를 붙인다 | 코드 |
| knowledge_verify → knowledge_finalize | 답에 나온 물질을 병렬로 SDS 조회해 "SDS 확인됨/확인 못 함"을 붙이고, "일반 화학 지식 — SDS로 확인한 내용 아님" 표시와 함께 코드가 답을 조립한다 | 코드 |
| plan | 조사할 물질 목록 수립, 섭취·합성 의도 인식. 물질이 없으면 직전 질문의 물질을 이어받는다 | LLM + 코드 보정 |
| gate | 물질 2종 이상이면 혼촉 점검을 반드시 넣고, 1종이면 뺀다 | 코드 |
| research | MCP Tool `get_substance_safety`(SDS)와 Resource `safety-guide://hazard/{type}`(지침)를 병렬 호출, 일시 오류 재시도. 약어는 추측해 조회하지 않는다 | 코드 |
| replan_check | 지침이 없으면 GHS 분류(H코드)에서 유해성 유형을 판정해 유형별 지침 추가 조사 | 코드 |
| analysis | 혼촉 규칙표(14가지)로 물질 쌍 판정 — 산화제×인화성, 산화제×유기물, 락스×산·암모니아·유기용매, 시안화물×산, 물 반응성×산·수용액, 알칼리 금속×할로젠화 용매, 과산화물×케톤, 은 화합물×암모니아, 산×염기 등. 확인하지 못한 물질은 "점검 불가"로 밝힌다 | 코드 |
| write | 수집된 사실만으로 안내문 작성. 하나도 확인하지 못했으면 `[확인 불가]`로 짧게 답한다 | LLM |
| validate | 6개 항목·출처·물질 누락, 섭취 시 119 경고, 합성 시 "합성 절차 미안내" 문구, **수집된 자료에 없는 수치(°C·%·ppm 등)·H코드**를 검사 | 코드 |
| finalize | 안내문 텍스트와 함께 화면용 구조화 데이터(`notice`: 그림문자 코드·신호어·혼촉 쌍·단독 주의·보호구·약어 후보)를 만들어 보낸다 | 코드 |

> knowledge 경로는 이 프로젝트에서 유일하게 **설명 문장을 LLM에 맡기는** 곳이다. 그래서 벡터 DB에서 찾은
> 문서를 근거로 주고(RAG), 화면에 "SDS로 확인한 내용 아님"과 근거 문서를 표시하고, 근거 없는 수치·H코드는
> 반려하며, 언급한 물질만큼은 SDS로 확인해 보여 준다.

## RAG · LLM-as-a-Judge · LangSmith

### RAG — 벡터 DB 의미 검색 (`agent/vector_store.py`)

| 항목 | 내용 |
|---|---|
| 문서 | `data/knowledge/*.md` 12개(흡습성, 중화열, 흄후드, 과산화물, 자연발화성, 물 반응성, GHS, SDS 항목, 폐액, 누출, 보관, 응급) + `data/safety_guides/*.md` 10개 |
| 청크 | 제목 아래 문단 단위로 색인하고, 걸린 문단이 속한 문서 전체를 근거로 돌려준다 |
| 임베딩 | `openai` = text-embedding-3-small(의미 기반) · `local` = 글자 n-gram 해시(키 없이 동작). `EMBEDDING_BACKEND`로 고르고, 비우면 OpenAI 모드에서 openai |
| 저장 | SQLite `vector_store.db`(.gitignore). 문서가 바뀌면 처음 검색할 때 자동으로 다시 색인 |
| 검색 | 코사인 유사도 상위 3개 중 최소 점수와 1등 대비 비율을 넘는 것만 — 관련 없는 문서는 근거로 붙이지 않는다 |
| 연결 | MCP Tool `search_knowledge`, Resource `knowledge://doc/{name}` → 그래프 `knowledge_retrieve` 노드 |

### LLM-as-a-Judge (`agent/judge.py`, `evaluate.py`)

```bash
bash sh/run_eval.sh          # 실제 평가: 에이전트 OpenAI + Judge gpt-4o-mini (과금, 질문 13개)
bash sh/run_eval.sh fake     # 키 없이 흐름 확인: fake LLM + 규칙 Judge
```
`data/eval_cases.json`의 질문 13개(4갈래 경로 모두)를 그래프에 넣고 ① 코드 검사(경로 일치, 필수 문구)
② Judge 채점(근거 충실성·안전성·유용성 1~5점 + 이유)을 해서 **[doc/eval_report.md](doc/eval_report.md)** 를 만든다.
세 점수가 모두 3점 이상이고 코드 검사를 통과해야 "통과"다.

### LangSmith 트레이싱

`.env`에 아래 세 줄을 넣으면(키는 smith.langchain.com → Settings → API Keys) 요청마다 `chat_request` 아래로
노드·LLM·MCP 호출이 계층 트리로 남는다. 평가(`run_eval.sh`)를 돌리면 프로젝트 `chem-safety-eval`에 케이스별
실행과 Judge 점수가 **피드백**으로 붙는다.

```ini
LANGCHAIN_TRACING_V2=true
LANGCHAIN_API_KEY=lsv2_...본인 키
LANGCHAIN_ENDPOINT=https://api.smith.langchain.com
```

fake 백엔드는 원본의 연출 두 가지를 유지한다 — plan이 1종일 때도 혼촉 점검을 끼워 넣는 자기모순(→ gate가
제거), write 1차 초안을 일부러 부실하게(→ validate 반려 후 재작성). 첫 SDS 조회 1회는 일부러
타임아웃을 내 재시도 로직을 보여준다.

## 원본(all_total_vue)과 달라진 점

| 구분 | 원본 (장애 대응) | 이 프로젝트 (화학안전) |
|---|---|---|
| MCP Tool | `get_monitoring_status` | `get_substance_safety` (KOSHA → PubChem → 샘플), `get_kosha_msds`(KOSHA 단독 확인용), `search_knowledge`(RAG) |
| MCP Resource | `runbook://incident-response/{type}` | `safety-guide://hazard/{type}` 10종, `knowledge://doc/{name}` 12개 |
| MCP Prompt | `incident_analysis_prompt` | `safety_notice_prompt` |
| 데이터 | `monitoring.json`, runbook 6종 | `substances.json`, `name_map.json`, `safety_guides/` 10종, `knowledge/` 12개 |
| gate | needs_analysis=False면 analysis 제거 | 물질 수로 혼촉 점검 추가/제거 |
| analysis | LLM이 시스템 패턴 비교 | 코드 규칙표로 혼촉 판정 |
| validate | 30자 이상 + "1)" | 6개 항목·출처·물질 누락·의도 경고 + 근거 없는 수치·H코드 검사 |
| LLM 호출 | 동기 `httpx.post`(응답 기다리는 동안 서버 전체가 멈춤) | 비동기 `httpx.AsyncClient` + OpenAI 스트리밍 |
| RAG | 없음 | 지식 문서 22개를 벡터 DB에 색인, MCP Tool로 의미 검색 |
| 평가 | 없음 | LLM-as-a-Judge(근거·안전·유용 1~5점) + LangSmith 피드백 |
| 테스트 | 없음 | `tests/` pytest 100여 개 |
| 공통 코드 | 프롬프트·계획 보정이 ollama/openai에 복붙 | `agent/prompts.py`로 통합 |
| 도메인 규칙 | 노드 안에 흩어짐 | `agent/chem_rules.py`에 모음 |

## 소스 구조

```
chem_safety_agent/
├── README.md              # 이 문서
├── server.py              # 백엔드 — FastAPI, /health·/chat·/chat/stream·/substances (:8100)
├── web_demo_server.py     # 프론트 서버 — 채팅 화면 제공 + 8100으로 요청 중계 (:8600)
├── web_chat.html          # 채팅 화면 (Vue 3) — 안전 라벨 카드, 진행 막대, 자동완성
├── mcp_server.py          # MCP 서버 "chem" — SDS·KOSHA·RAG 검색 Tool, 취급 지침·지식 문서 Resource, 안내문 Prompt
├── mcp_server_docs.py     # MCP 서버 "docs" — 멀티 서버 탐색 데모용 decoy (사용 안 함)
├── evaluate.py            # LLM-as-a-Judge 평가 실행기 → doc/eval_report.md
├── REPORT.md              # 최종 보고서
├── agent/
│   ├── graph.py           # LangGraph 노드·간선
│   ├── chem_rules.py      # 물질명·약어 인식, 의도 인식, H코드 분류·한국어 문구, 반응성 태그, 혼촉 규칙, 단독 주의, 보호구
│   ├── kosha.py           # 안전보건공단 MSDS Open API 조회·파싱 (DATA_GO_KR_API_KEY가 있을 때)
│   ├── pubchem.py         # PubChem 조회·파싱 (live 모드)
│   ├── cache.py           # 조회 결과 영구 캐시 (SQLite, lookup_cache.db)
│   ├── vector_store.py    # RAG 벡터 DB — 지식 문서 임베딩·SQLite 저장·코사인 검색
│   ├── judge.py           # LLM-as-a-Judge 채점 기준·OpenAI Judge·규칙 Judge
│   ├── prompts.py         # ollama/openai 공용 프롬프트·계획 보정·의도 경고
│   ├── fake_llm.py / ollama_llm.py / openai_llm.py
│   ├── mcp_client.py      # 다중 MCP 세션 + find_prompt/find_resource_template
│   ├── supervisor_v1.py   # legacy 선형 파이프라인 (SUPERVISOR_VERSION=v1)
│   └── deps.py · retry.py · session_store.py
├── data/
│   ├── substances.json    # 샘플 SDS 요약 6종 (sample 모드·오프라인 대체용)
│   ├── name_map.json      # 한국어 이름 → 영문명·CAS 사전 (live 모드 조회용) + 혼촉 판정 tag
│   ├── h_statements_ko.json  # GHS H코드 → 한국어 유해·위험 문구 표
│   ├── knowledge/         # RAG용 화학 안전 지식 문서 12개
│   ├── eval_cases.json    # LLM-as-a-Judge 평가 질문 13개
│   └── safety_guides/     # explosive·pyrophoric·water-reactive·oxidizer·flammable·toxic·corrosive·health-hazard·irritant·general
├── tests/                 # pytest 101개 — 규칙·검수·조회·LLM·그래프·RAG·Judge·서버 (bash sh/run_tests.sh)
├── sh/                    # setup_venv · setup_ollama · run_agent_fake/ollma/openai · run_frontend · run_tests · run_eval · make_student_zip
└── doc/                   # run_guide.md(상세 실행), test_queries.md, eval_report.md(평가 결과), images/
```

## 물질·규칙 추가하기

- **이름 사전에 물질 추가 (가장 쉬움)**: `data/name_map.json`에 한 줄 추가하면 live 모드에서 PubChem으로 조회된다.
```json
  "anisole": {"name_ko": "아니솔", "name_en": "Anisole", "cas": "100-66-3", "aliases": ["아니솔", "anisole"], "tags": ["organic-solvent"]},
```
  `tags`는 혼촉 판정용이다 — `acid`·`oxidizing-acid`·`base`·`ammonia`·`hypochlorite`·`cyanide`·
  `organic-solvent`·`organic`·`halogenated`·`ketone`·`oxidizer`·`peroxide`·`peroxide-former`·`reducer`·
  `water-reactive`·`alkali-metal`·`aqueous`(물이 든 시약)·`silver`·`explosive`.
  live 모드에서는 PubChem의 반응성 분류(Reactive Group)와 H코드에서도 태그를 자동으로 더한다.
  CAS 번호는 반드시 공식 자료에서 확인해 넣는다.
- **약어 추가**: 여러 물질을 가리키는 약어는 `agent/chem_rules.py`의 `AMBIGUOUS_ABBR`에 후보와 함께 넣는다.
- **오프라인 샘플 추가**: `data/substances.json`에 GHS 정보까지 넣으면 인터넷 없이도 조회된다.
- **혼촉 규칙 추가**: `agent/chem_rules.py`의 `INCOMPATIBILITY_RULES`에 (규칙 id, 조건 A, 조건 B, 위험, 권고)를 추가.
  더 구체적인 규칙과 겹치면 `_REDUNDANT`에 등록해 같은 쌍에 중복 경고가 나오지 않게 한다.
- **단독 주의 추가**: `SOLO_CAUTIONS`에 (태그: 문구)를 추가하면 그 태그를 가진 물질에 노란 띠가 뜬다.
- **한국어 H문구 추가**: `data/h_statements_ko.json`에 코드와 공식 문구를 넣는다. 표에 없는 코드는 영어 그대로 나온다.
- **지침 추가**: `data/safety_guides/{유형}.md` 작성 후 `HAZARD_LABELS`·`_H_CODE_RULES`에 유형 등록.
  `보호구:`·`보관:`·`폐기:`로 시작하는 줄은 fake 안내문에 그대로 인용된다.

추가한 뒤에는 터미널 ①의 백엔드를 `Ctrl + C`로 끄고 다시 켜야 반영되고, `bash sh/run_tests.sh`로 기존 기능을 확인한다.

## 안전보건공단(KOSHA) MSDS 켜기

공공데이터포털의 "한국산업안전보건공단_물질안전보건자료(MSDS)" API(개발계정 자동 승인, 무료)를 붙여 두었다.
KOSHA는 **한국어 물질명으로 조회**되고 문구가 한국어 원문이라, 켜면 live 모드에서 PubChem보다 먼저 쓴다.

1. https://www.data.go.kr 에서 활용신청 → 마이페이지에서 인증키 복사
2. `.env`에 `DATA_GO_KR_API_KEY=발급받은키` 한 줄 추가 (코드·문서·깃·화면 캡처에 키를 넣지 않는다)
3. 백엔드를 다시 켠다. 출처가 "안전보건공단 MSDS"로 나오면 동작하는 것이다

> ✅ 실제 KOSHA 서버로 연결을 확인했다(톨루엔: 신호어 위험, H225·H304·H315, 응급조치 눈·피부·흡입·섭취).
> 처음 켤 때 [doc/run_guide.md의 6-3 연결 확인](doc/run_guide.md#6-3-연결-확인)을 한 번 실행해 본인 키로도 확인한다.
> KOSHA가 실패하면 이유를 메모로 남기고 PubChem으로 자동으로 넘어가므로 서비스는 멈추지 않는다.
> 끄려면 `.env`에 `KOSHA_ENABLED=0`.

## 참고

- PubChem PUG-View: `https://pubchem.ncbi.nlm.nih.gov/rest/pug_view/data/compound/{cid}/JSON?heading=...`
  — `agent/pubchem.py`는 `GHS Classification`, `First Aid Measures`, `Handling and Storage`, `Reactive Group`
  네 섹션만 받는다. 호출 빈도 제한이 있어 결과를 SQLite에 캐시한다.
- 공공데이터포털 KOSHA MSDS: https://www.data.go.kr/data/15001197/openapi.do
- 한국어 H문구: 고용노동부 고시 「화학물질의 분류·표시 및 물질안전보건자료에 관한 기준」