"""화학물질 도메인 규칙 — 모델에게 다시 묻지 않고 코드가 내리는 결정들을 한곳에 모은다.

- 물질 카탈로그 : data/substances.json(GHS 데이터가 있는 샘플) + data/name_map.json(이름 사전)
- 물질 인식     : 카탈로그 별칭 매칭 + CAS 번호 + 사전에 없는 화학물질 이름 후보 추출
- 의도 인식     : 섭취(마실/먹을)·합성(만들/합성) 의도 — 안내문 맨 앞 경고에 쓰인다
- 유해성 유형   : GHS 유해·위험 문구(H코드)에서 hazard class를 뽑는다
- 한국어 문구   : PubChem 영어 H문구를 data/h_statements_ko.json 표로 바꾼다
- 성질 태그     : H코드·PubChem 'Reactive Group'(CAMEO 반응성 분류)에서 혼촉용 태그를 뽑는다
- 혼촉 위험     : 물질 쌍마다 hazard class·tag 조합 규칙으로 판정
- 단독 주의     : 섞지 않아도 위험한 성질(자연발화, 과산화물 생성, 물 반응성)을 물질별로 알린다
- 성질 질의     : "자연발화하는 물질은 뭐가 있어?"처럼 물질 이름 없이 성질로 묻는 질문을 알아보고,
                  그 성질을 증명하는 GHS H코드(예: 자연발화성 H250)와 기본 후보 목록을 준다
- 화학 질문     : 물질 이름이 없는 일반 화학 질문("흡습성 물질은?")을 알아보는 표현(CHEM_CUE)
- 보호구        : hazard class별 최소 보호구 목록
"""
import json
import re
from itertools import combinations
from pathlib import Path

DATA_DIR = Path(__file__).resolve().parent.parent / "data"


def _load(name: str) -> dict:
    try:
        raw = json.loads((DATA_DIR / name).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    return {k: v for k, v in raw.items() if not k.startswith("_")}


SUBSTANCES = _load("substances.json")      # GHS 데이터까지 있는 샘플 (sample 모드 조회 대상)
CATALOG = {**_load("name_map.json"), **SUBSTANCES}   # 이름 인식용 전체 목록 (샘플이 우선)
H_STATEMENTS_KO = _load("h_statements_ko.json")       # H코드 → 한국어 유해·위험 문구

# 유해성 유형(= safety guide Resource 이름)과 화면 표시용 한국어 이름.
# 순서가 우선순위다 — replan_check가 지침을 고를 때 위험한 것부터 고른다.
HAZARD_LABELS = {
    "explosive": "폭발성",
    "pyrophoric": "자연발화성",
    "water-reactive": "물 반응성",
    "oxidizer": "산화성",
    "flammable": "인화성",
    "toxic": "급성 독성",
    "corrosive": "부식성",
    "health-hazard": "건강 유해성",
    "irritant": "자극성",
    "general": "일반",
}

# GHS H코드 → hazard class (PubChem 실데이터를 분류할 때 사용)
_H_CODE_RULES = [
    (re.compile(r"H20[0-5]"), "explosive"),
    (re.compile(r"H250"), "pyrophoric"),
    (re.compile(r"H26[01]"), "water-reactive"),
    (re.compile(r"H2(2[0-8])"), "flammable"),
    (re.compile(r"H2(7[0-2])"), "oxidizer"),
    (re.compile(r"H(290|314|318)"), "corrosive"),
    (re.compile(r"H(300|301|310|311|330|331)"), "toxic"),
    (re.compile(r"H(34[0-1]|35[0-1]|36[0-2]|37[0-3]|304)"), "health-hazard"),
    (re.compile(r"H(302|312|315|317|319|332|335|336)"), "irritant"),
]

PPE_BY_CLASS = {
    "explosive": "담당자 승인 후 취급, 충격·마찰·열·정전기 금지, 안면보호구·방호 차폐막",
    "pyrophoric": "난연 실험복 필수, 보안경+안면보호구, 불활성 기체(질소·아르곤) 분위기, D급 소화기·마른 모래 준비",
    "water-reactive": "물·습기 차단, 건조한 불활성 분위기, 금속 화재용(D급) 소화기 준비",
    "corrosive": "보안경+안면보호구, 내산·내알칼리 장갑(니트릴/네오프렌)",
    "oxidizer": "보안경(튐 위험 시 안면보호구), 내화학 장갑",
    "toxic": "흄후드 필수, 내화학 장갑(필요 시 이중 착용)",
    "flammable": "면 또는 난연 실험복(합성섬유 피함), 점화원 제거",
    "health-hazard": "흄후드 사용, 노출 시간 최소화",
}
BASE_PPE = "보안경, 실험복, 니트릴 장갑"

# 혼촉 규칙: (규칙 id, 조건 A, 조건 B, 위험, 권고). 조건은 hazard class 또는 tag 이름.
KEEP_APART = "함께 섞거나 같은 곳에 보관하지 말 것"
INCOMPATIBILITY_RULES = [
    ("oxidizer-flammable", "oxidizer", "flammable",
     "산화제와 인화성 물질이 만나면 화재·폭발 위험이 크다", KEEP_APART),
    ("oxidizing-acid-organic", "oxidizing-acid", "organic-solvent",
     "산화성 산(질산 등)과 유기용매는 격렬하게 반응할 수 있다", KEEP_APART),
    ("oxidizer-organic", "oxidizer", "organic",
     "산화제가 유기물(글리세롤·글리콜·페놀 등)과 만나면 발열하거나 저절로 불이 붙을 수 있다", KEEP_APART),
    ("reducer-oxidizer", "reducer", "oxidizer",
     "강한 환원제(알칼리 금속 등)와 산화제가 만나면 격렬하게 반응해 불이 붙을 수 있다", KEEP_APART),
    ("hypochlorite-acid", "hypochlorite", "acid",
     "차아염소산염(락스 등)과 산이 만나면 유독한 염소 기체가 생긴다", KEEP_APART),
    ("hypochlorite-ammonia", "hypochlorite", "ammonia",
     "차아염소산염과 암모니아가 만나면 유독한 클로라민 기체가 생긴다", KEEP_APART),
    ("hypochlorite-organic", "hypochlorite", "organic-solvent",
     "차아염소산염과 일부 유기용매(아세톤·에탄올 등)가 만나면 클로로포름 같은 유해 물질이 생길 수 있다",
     KEEP_APART),
    ("cyanide-acid", "cyanide", "acid",
     "시안화물과 산이 만나면 맹독성 시안화수소 기체가 생긴다", KEEP_APART),
    ("water-reactive-acid", "water-reactive", "acid",
     "물 반응성 물질(나트륨 등)과 산이 만나면 수소 같은 인화성 기체와 열이 격렬하게 생긴다", KEEP_APART),
    ("water-reactive-aqueous", "water-reactive", "aqueous",
     "물 반응성 물질이 수용액(물이 든 시약)과 닿으면 인화성 기체와 열이 생겨 불이 붙을 수 있다", KEEP_APART),
    ("alkali-halogenated", "alkali-metal", "halogenated",
     "알칼리 금속(나트륨 등)과 할로젠화 용매(클로로포름·다이클로로메테인)는 폭발적으로 반응할 수 있다",
     KEEP_APART),
    ("peroxide-ketone", "peroxide", "ketone",
     "과산화수소 같은 과산화물과 아세톤 같은 케톤이 만나면 충격에 민감한 폭발성 과산화물이 생길 수 있다",
     KEEP_APART),
    ("silver-ammonia", "silver", "ammonia",
     "은 화합물(질산은 등)과 암모니아 용액을 섞어 두면 마른 뒤 폭발하는 은 화합물이 생길 수 있다",
     KEEP_APART),
    ("acid-base", "acid", "base", "산과 염기를 섞으면 큰 중화열이 나고 튈 수 있다",
     "보관은 분리하고, 중화 실험처럼 일부러 섞을 때는 묽힌 용액을 소량씩 저으며(필요하면 "
     "냉각하며) 천천히 더할 것"),
]
# 같은 쌍에서 앞의(더 구체적인) 규칙이 이미 걸렸으면 뒤의 일반 규칙은 겹치는 말이라 뺀다
_REDUNDANT = {
    "oxidizer-organic": {"oxidizer-flammable", "oxidizing-acid-organic"},
    "water-reactive-aqueous": {"water-reactive-acid"},
}
# 태그가 다른 태그를 함께 뜻하는 경우 (유기용매는 유기물, 알칼리 금속은 물 반응성·환원제)
_IMPLIES = {
    "organic-solvent": {"organic"},
    "alkali-metal": {"water-reactive", "reducer"},
    "oxidizing-acid": {"acid"},
}

# 섞지 않아도 그 물질 하나로 생기는 위험 — 안내문 '취급 주의'와 라벨 카드에 따로 표시한다
SOLO_CAUTIONS = {
    "pyrophoric": "공기와 닿으면 저절로 불이 붙음 — 질소·아르곤 같은 불활성 기체 아래에서 다루고, 옮길 때는 "
                  "밀폐된 주사기·캐뉼라를 쓰며, 주변 가연물을 치우고 혼자 작업하지 말 것",
    "peroxide-former": "공기·빛에 오래 두면 폭발성 과산화물이 생길 수 있음 — 용기에 개봉일을 적고, "
                       "오래됐거나 뚜껑 주변에 결정이 보이면 열거나 농축하지 말고 안전관리자에게 알릴 것",
    "water-reactive": "물·습기와 닿으면 인화성 기체가 생길 수 있음 — 물로 씻거나 물 소화기를 쓰지 말고, "
                      "건조한 곳에서 다루며 금속 화재용(D급) 소화기나 마른 모래를 준비할 것",
}

# H코드에서 바로 알 수 있는 태그
_H_CODE_TAGS = [
    (re.compile(r"H250"), "pyrophoric"),
    (re.compile(r"EUH019"), "peroxide-former"),
    (re.compile(r"H26[01]|EUH014"), "water-reactive"),
    (re.compile(r"H27[0-2]"), "oxidizer"),
]
# PubChem 'Reactive Group'(CAMEO Chemicals 반응성 분류) 이름 → 태그. 부분 문자열로 비교한다.
_REACTIVE_GROUP_TAGS = [
    ("acids, strong oxidizing", ["acid", "oxidizing-acid"]),
    ("acids", ["acid"]),
    ("salts, acidic", ["acid"]),
    ("bases", ["base"]),
    ("salts, basic", ["base"]),
    ("amines", ["base", "organic"]),
    ("ethers", ["peroxide-former", "organic"]),
    ("peroxides", ["peroxide", "oxidizer"]),
    ("oxidizing agents", ["oxidizer"]),
    ("nitrate and nitrite compounds, inorganic", ["oxidizer"]),
    ("reducing agents", ["reducer"]),
    ("metals, alkali", ["alkali-metal", "water-reactive", "reducer"]),
    ("water-reactive", ["water-reactive"]),
    ("water and aqueous solutions", ["aqueous"]),
    ("cyanides", ["cyanide"]),
    ("halogenated organic", ["halogenated", "organic"]),
    ("hydrocarbons", ["organic"]),
    ("alcohols", ["organic"]),
    ("ketones", ["organic", "ketone"]),
    ("aldehydes", ["organic"]),
    ("esters", ["organic"]),
    ("nitriles", ["organic"]),
    ("phenols", ["organic"]),
    ("explosives", ["explosive"]),
]


def tags_from_hazard_statements(statements: list[str]) -> list[str]:
    text = " ".join(statements or [])
    return list(dict.fromkeys(tag for pattern, tag in _H_CODE_TAGS if pattern.search(text)))


def tags_from_reactive_groups(groups: list[str]) -> list[str]:
    tags: list[str] = []
    for g in groups or []:
        low = g.lower()
        for key, mapped in _REACTIVE_GROUP_TAGS:
            if key in low:
                tags += mapped
    return list(dict.fromkeys(tags))


# ── 성질로 물질 찾기 ("자연발화하는 물질은 뭐가 있어?") ─────────────────
# 성질마다: 화면 이름, 그 성질을 '증명'하는 GHS 코드, 질문에서 알아볼 표현, 기본 후보.
# LLM이 후보를 더 제안하지만, 최종 답에는 KOSHA·PubChem 자료에서 이 코드가 실제로 확인된 물질만 나간다.
# 기본 후보는 LLM이 없거나(fake) 후보를 너무 적게 낼 때 쓰는 출발점일 뿐, 이것도 똑같이 자료로 확인한다.
PROPERTY_QUERIES = {
    "pyrophoric": {
        "label": "공기 중 자연발화성", "codes": ["H250"],
        "pattern": r"자연\s*발화|공기.{0,10}(불이|불 붙|발화|타는|타버)|pyrophoric",
        "seeds": [("n-뷰틸리튬", "109-72-8"), ("tert-뷰틸리튬", "594-19-4"), ("트라이에틸알루미늄", "97-93-8"),
                  ("트라이메틸알루미늄", "75-24-1"), ("다이에틸아연", "557-20-0"), ("백린(황린)", "12185-10-3"),
                  ("실레인", "7803-62-5")],
    },
    "water-reactive": {
        "label": "물 반응성(금수성)", "codes": ["H260", "H261", "EUH014"],
        "pattern": r"금수성|물.{0,8}(반응|닿으면|만나면|접촉|불이|폭발)|water.?reactive",
        "seeds": [("금속 나트륨", "7440-23-5"), ("금속 칼륨", "7440-09-7"), ("금속 리튬", "7439-93-2"),
                  ("수소화나트륨", "7646-69-7"), ("수소화알루미늄리튬", "16853-85-3"), ("탄화칼슘", "75-20-7")],
    },
    "peroxide-former": {
        "label": "과산화물 생성", "codes": ["EUH019"],
        "pattern": r"과산화물.{0,6}(생성|생기|생길|만들|형성)|peroxide.?form",
        "seeds": [("다이에틸에테르", "60-29-7"), ("테트라하이드로퓨란", "109-99-9"), ("1,4-다이옥세인", "123-91-1"),
                  ("다이아이소프로필에테르", "108-20-3")],
    },
    "oxidizer": {
        "label": "산화성", "codes": ["H270", "H271", "H272"],
        "pattern": r"산화제|산화성",
        "seeds": [("질산", "7697-37-2"), ("과산화수소", "7722-84-1"), ("과망가니즈산칼륨", "7722-64-7"),
                  ("질산암모늄", "6484-52-2"), ("다이크로뮴산칼륨", "7778-50-9"), ("염소산나트륨", "7775-09-9")],
    },
    "carcinogen": {
        "label": "발암성", "codes": ["H350", "H351"],
        "pattern": r"발암|암을\s*일으|carcinogen",
        "seeds": [("벤젠", "71-43-2"), ("폼알데하이드", "50-00-0"), ("다이크로뮴산칼륨", "7778-50-9"),
                  ("클로로포름", "67-66-3"), ("다이클로로메테인", "75-09-2")],
    },
}
# 목록을 묻는 표현 — "톨루엔은 산화제야?"처럼 특정 물질을 묻는 질문과 구분하기 위해 함께 있어야 한다
_LIST_CUE = re.compile(
    r"뭐|뭘|뭔|무엇|어떤|어느|종류|예시|예를|목록|리스트|있어|있나|있니|있을까|있는지|알려|대표|"
    r"(물질|것|시약|화합물|용매|금속|기체|약품)(은|는|들|이|에는)?\s*[?？]?\s*$|"
    r"which|what|list|examples", re.I)

# 화학·실험실 질문인지 알아보는 표현 — 물질 이름이 없어도 일반 화학 질문(knowledge)으로 보낸다.
# 이 표현이 없고 안전 키워드도 없으면 인사·잡담(chat)으로 본다.
CHEM_CUE = re.compile(
    r"물질|화합물|화학|원소|분자|원자|이온|반응|용액|용매|용질|시약|산성|염기|알칼리|중화|흡습|조해|증기|"
    r"기체|가스|산화|환원|촉매|농도|pH|녹는점|끓는점|어는점|녹|끓|증발|승화|실험|흄\s*후드|후드|폐액|"
    r"결정|침전|증류|정제|추출|크로마토|적정|몰|금속|유기|무기|고분자|폭발|인화|발화|부식|비커|플라스크|"
    r"피펫|뷰렛|chemical|reaction|solvent|acid|base", re.I)


def detect_property(query: str) -> str | None:
    """물질 이름 없이 성질로 물질을 묻는 질문이면 성질 키, 아니면 None."""
    if extract_targets(query) or not _LIST_CUE.search(query):
        return None
    for key, spec in PROPERTY_QUERIES.items():
        if re.search(spec["pattern"], query, re.I):
            return key
    return None


def cas_valid(cas: str) -> bool:
    """CAS 번호 검증 숫자(마지막 자리)가 맞는지 — LLM이 그럴듯하게 지어낸 번호를 1차로 거른다."""
    m = re.fullmatch(r"(\d{2,7})-(\d{2})-(\d)", (cas or "").strip())
    if not m:
        return False
    digits = (m.group(1) + m.group(2))[::-1]
    return sum((i + 1) * int(d) for i, d in enumerate(digits)) % 10 == int(m.group(3))


def has_codes(sds: dict, codes: list[str]) -> str | None:
    """조회된 SDS의 유해·위험 문구에 codes 중 하나가 있으면 그 문구를, 없으면 None."""
    if not sds.get("found"):
        return None
    pattern = re.compile(r"(?<![A-Za-z0-9])(" + "|".join(map(re.escape, codes)) + r")(?!\d)")
    for text in sds.get("hazard_statements", []) + sds.get("hazard_statements_en", []):
        if pattern.search(text):
            return text
    return None


# ── 한국어 유해·위험 문구 ────────────────────────────────────────────
_H_PREFIX = re.compile(r"^\s*((?:EUH|H)\d{3}[A-Za-z]{0,2}(?:\s*\+\s*(?:EUH|H)\d{3}[A-Za-z]{0,2})*)"
                       r"\s*(?:\([^)]*\))?\s*[:：]?\s*(.*)$")


def korean_hazard(statement: str) -> str:
    """'H225 (100%): Highly Flammable liquid and vapor [Danger ...]' → 'H225 고인화성 액체 및 증기'.
    'H302+H312+H332'처럼 묶인 코드는 각 문구를 ' · '로 잇는다. 표에 없는 코드가 하나라도
    있으면 뜻을 지어내지 않고 원문을 그대로 돌려준다."""
    m = _H_PREFIX.match(statement or "")
    if not m:
        return statement
    codes = [c.strip() for c in m.group(1).split("+")]
    texts = []
    for code in codes:
        base = re.match(r"(EUH|H)\d{3}", code).group(0)
        if base not in H_STATEMENTS_KO:
            return statement
        texts.append(H_STATEMENTS_KO[base])
    return f"{'+'.join(codes)} {' · '.join(texts)}"


# ── 의도 인식 ────────────────────────────────────────────────────────
_INGEST = re.compile(r"마시|마실|마셔|마셨|먹|삼키|삼켜|삼켰|음용|섭취|drink|swallow", re.I)
_SYNTH = re.compile(r"합성|제조|만들|만드|만들어|synthes|manufactur", re.I)
_ACTION = re.compile(
    r"쓸|쓰|써|사용|다루|다룰|취급|섞|희석|가열|실험|보관|버리|버릴|폐기|옮기|따르|녹이|녹일|"
    r"마시|마실|먹|삼키|합성|제조|만들|만드|반응")


def detect_intent(query: str) -> dict:
    return {"ingest": bool(_INGEST.search(query)), "synthesis": bool(_SYNTH.search(query))}


# ── 물질 인식 ────────────────────────────────────────────────────────
def _squash(text: str) -> str:
    """한글 사이 공백 제거 — '니트로 글리세린'도 '니트로글리세린'으로 매칭되게."""
    return re.sub(r"(?<=[가-힣])\s+(?=[가-힣])", "", text)


def resolve(name: str) -> str | None:
    """물질명(한국어·영어·CAS·화학식) 또는 카탈로그 키를 카탈로그 키로 바꾼다."""
    if not name:
        return None
    key = _squash(name.strip()).lower()
    if key in CATALOG:
        return key
    for sid, s in CATALOG.items():
        if key in (_squash(a).lower() for a in s.get("aliases", [])) or key == s.get("cas"):
            return sid
    return None


# 조사와 헷갈리는 별칭: '질산은 위험해?'(질산+은)와 '질산은 용액'(AgNO3)을 구분한다.
# 여기 있는 별칭은 뒤에 이어지는 말이 패턴과 맞을 때만 그 물질로 인정한다.
_AMBIGUOUS_ALIASES = {"질산은": r"(?=을|를|이|과|와|으로|랑|\s*수?용액)"}


def _catalog_matches(query: str) -> list[tuple[int, str]]:
    """카탈로그 별칭을 긴 것부터 매칭한다(‘질산암모늄’이 ‘질산’보다 먼저 잡히게).

    한글 사이 공백을 지운 문장에서 찾으므로 '니트로 글리세린'도 잡힌다. 다만 3글자 이하
    한글 별칭은 원문에도 그대로 있어야 인정한다 — '확인 산성'에서 '인산'이 잡히는 것 방지."""
    text = _squash(query)
    taken: list[tuple[int, int]] = []
    found = []
    pairs = sorted(((_squash(a), sid) for sid, s in CATALOG.items() for a in s.get("aliases", [])),
                   key=lambda p: -len(p[0]))
    for alias, sid in pairs:
        if re.fullmatch(r"[A-Za-z0-9,()\- ]+", alias):   # 영문·화학식은 단어 경계 확인
            pattern = re.compile(rf"(?<![A-Za-z0-9]){re.escape(alias)}(?![A-Za-z0-9])", re.I)
        else:
            if len(alias) < 4 and alias not in query:
                continue
            pattern = re.compile(re.escape(alias) + _AMBIGUOUS_ALIASES.get(alias, ""))
        for m in pattern.finditer(text):
            span = (m.start(), m.end())
            if any(span[0] < e and st < span[1] for st, e in taken):
                continue
            taken.append(span)
            found.append((m.start(), sid))
    return found


def find_substances(query: str) -> list[str]:
    """쿼리에 등장하는 카탈로그 물질 키를 등장 순서대로 (중복 제거)."""
    found = _catalog_matches(query)
    ordered: list[str] = []
    for _, sid in sorted(found):
        if sid not in ordered:
            ordered.append(sid)
    return ordered


_CAS = re.compile(r"(?<![\d-])(\d{2,7}-\d{2}-\d)(?![\d-])")
# 조사(을/를/이랑…) 앞의 단어 중 화학물질 이름처럼 끝나는 것만 후보로 본다
_CANDIDATE = re.compile(r"([가-힣A-Za-z0-9\-]{2,20}?)(?:을|를|이랑|랑|과|와|하고|이나|은|는|으로|로)(?=[\s,.?!]|$)")
_CHEM_SUFFIX = re.compile(
    r"(산|올|엔|렌|젠|텐|펜|린|딘|핀|논|톤|트|드|민|륨|늄|소|염|란|탄|판|졸|놀|롤|솔|릴|닐|"
    r"에테르|아민|알코올|가스|용액|화합물|ine|ane|ene|ol|one|ide|ate|ite|acid|ium)$", re.I)
_STOPWORDS = {"실험", "주의사항", "주의", "사용", "물질", "시약", "용액", "내일", "오늘", "이것", "그것",
              "저것", "이거", "그거", "합성", "제조", "결과", "방법", "보관", "폐기", "안전", "위험",
              "중화", "반응", "희석", "가열", "장소", "학교", "회사", "집", "우리", "모두", "다음",
              # 실험 기구·보호구 — 이름이 '-드·-린' 등으로 끝나 물질로 잘못 잡히지 않게
              "흄후드", "후드", "비커", "플라스크", "피펫", "뷰렛", "시험관", "장갑", "보안경", "실험복",
              "마스크", "데시케이터", "오븐", "핫플레이트", "글러브박스", "샤워", "세안기"}

# 여러 물질을 가리키는 약어 — 추측하지 않고 후보를 보여주며 되묻는다
AMBIGUOUS_ABBR = {
    "DMA": ["N,N-다이메틸아세트아마이드(DMAc, CAS 127-19-5)", "다이메틸아민(CAS 124-40-3)"],
    "TEA": ["트라이에틸아민(CAS 121-44-8)", "트라이에탄올아민(CAS 102-71-6)"],
    "DME": ["1,2-다이메톡시에테인(CAS 110-71-4)", "다이메틸에테르(CAS 115-10-6)"],
}
# 화학물질이 아닌 흔한 영문 약어 (물질 후보에서 제외)
_ABBR_STOPWORDS = {"SDS", "MSDS", "GHS", "CAS", "PPE", "OK", "NMR", "HPLC", "TLC", "GC", "LC", "MS",
                   "IR", "UV", "PH", "AI", "API", "LLM", "PDF", "URL", "ID", "QA"}
_LATIN_TOKEN = re.compile(r"(?<![A-Za-z0-9])([A-Z][A-Za-z0-9]{1,7})(?![A-Za-z0-9])")
_FORMULA = re.compile(r"(?:[A-Z][a-z]?\d*){2,}")


def is_abbreviation(name: str) -> bool:
    """대문자로만 된 2~6자 약어(DMA, MEK …) — 카탈로그에 없으면 뜻을 확정할 수 없다."""
    return bool(re.fullmatch(r"[A-Z]{2,6}", name or "")) and not resolve(name)

def extract_targets(query: str) -> list[dict]:
    """질문에서 조사 대상 물질을 뽑는다 — fake 백엔드와 실모델의 누락 복구가 같이 쓴다.

    반환: [{"target": 키 또는 원문, "label": 화면 표시명, "lookup": PubChem용 영문/CAS 또는 None}]
    """
    targets: list[dict] = []
    seen: set[str] = set()

    def add(target: str, label: str, lookup: str | None):
        if target.lower() not in seen:
            seen.add(target.lower())
            targets.append({"target": target, "label": label, "lookup": lookup})

    for _, sid in sorted(_catalog_matches(query)):
        s = CATALOG[sid]
        add(sid, s.get("name_ko") or s.get("name_en"), s.get("cas") or s.get("name_en"))
    known_aliases = [_squash(a).lower() for t in targets for a in CATALOG[t["target"]]["aliases"]]
    for m in _CAS.finditer(query):
        sid = resolve(m.group(1))
        if sid:
            add(sid, label_of(sid), m.group(1))
        else:
            add(m.group(1), f"CAS {m.group(1)}", m.group(1))
    if _ACTION.search(query):
        for m in _CANDIDATE.finditer(query):
            word = m.group(1)
            if (word in _STOPWORDS or not _CHEM_SUFFIX.search(word) or resolve(word)
                    or any(a in word.lower() or word.lower() in a for a in known_aliases)):
                continue
            add(word, word, word if re.fullmatch(r"[A-Za-z0-9\-]+", word) else None)
      # 영문 약어(DMA)·화학식(KCl): 약어는 뜻을 확정할 수 없으므로 lookup 없이 넘긴다
    for m in _LATIN_TOKEN.finditer(query):
        tok = m.group(1)
        if tok.upper() in _ABBR_STOPWORDS or resolve(tok) or tok.lower() in seen:
            continue
        if is_abbreviation(tok):
            add(tok, tok, None)
        elif _FORMULA.fullmatch(tok) and re.search(r"[a-z\d]", tok):
            add(tok, tok, tok)

    # 물질 이름 한 단어만 보낸 경우('아니솔') — 동사가 없어도 후보로 본다
    word = query.strip().rstrip("?!. ")
    if (not targets and re.fullmatch(r"[가-힣]{2,15}", word) and _CHEM_SUFFIX.search(word)
            and word not in _STOPWORDS):
        add(word, word, None)
    return targets


def label_of(target: str) -> str:
    s = CATALOG.get(target)
    return (s.get("name_ko") or s.get("name_en")) if s else target


def display_name(sds: dict) -> str:
    return sds.get("name_ko") or sds.get("name_en") or sds.get("query", "?")


# ── 유해성·혼촉·보호구 ───────────────────────────────────────────────
def classes_from_hazard_statements(statements: list[str]) -> list[str]:
    text = " ".join(statements)
    classes = [cls for pattern, cls in _H_CODE_RULES if pattern.search(text)]
    return list(dict.fromkeys(classes))


def hazard_types_for_guides(sds_list: list[dict], limit: int = 4) -> list[str]:
    """수집된 SDS들의 hazard class를 모아 우선순위 순으로 지침 목록을 만든다."""
    present = {c for s in sds_list if s.get("found") for c in s.get("hazard_classes", [])}
    present |= {t for s in sds_list if s.get("found") for t in s.get("tags", [])
                if t in HAZARD_LABELS}
    ordered = [h for h in HAZARD_LABELS if h in present]
    return ordered[:limit] or ["general"]


def _traits(sds: dict) -> set[str]:
    traits = set(sds.get("hazard_classes", [])) | set(sds.get("tags", []))
    for tag in list(traits):
        traits |= _IMPLIES.get(tag, set())
    return traits


def check_incompatibility(sds_list: list[dict]) -> list[dict]:
    """물질 쌍마다 혼촉 규칙을 검사한다. SDS 조회에 실패한 물질도 이름 사전의 tags
    (산·염기·차아염소산염 등)가 있으면 검사에 포함한다 — 위험 조합을 놓치지 않기 위해."""
    usable = [s for s in sds_list if _traits(s)]
    conflicts = []
    for a, b in combinations(usable, 2):
        ta, tb = _traits(a), _traits(b)
        hit = [r for r in INCOMPATIBILITY_RULES
               if (r[1] in ta and r[2] in tb) or (r[2] in ta and r[1] in tb)]
        ids = {r[0] for r in hit}
        for rule_id, _, _, warning, advice in hit:
            if _REDUNDANT.get(rule_id, set()) & ids:
                continue
            conflicts.append({"rule": rule_id, "pair": [display_name(a), display_name(b)],
                              "warning": warning, "advice": advice})
    return conflicts


def solo_cautions(sds_list: list[dict]) -> list[dict]:
    """섞지 않아도 주의할 성질 — 물질별로 [{name, tag, text}]."""
    out = []
    for s in sds_list:
        traits = _traits(s)
        for tag, text in SOLO_CAUTIONS.items():
            if tag in traits:
                out.append({"name": display_name(s), "tag": tag, "text": text})
    return out


def is_explosive(sds_list: list[dict]) -> bool:
    return any("explosive" in _traits(s) for s in sds_list)


# ── 화면 표시용 ──────────────────────────────────────────────────────
# GHS 그림문자 코드와 한국어 이름. PubChem은 영어 라벨("Flammable")로, 샘플은 "GHS02 인화성"으로 준다.
PICTOGRAMS = {
    "GHS01": "폭발성", "GHS02": "인화성", "GHS03": "산화성", "GHS04": "고압가스",
    "GHS05": "부식성", "GHS06": "급성 독성", "GHS07": "경고", "GHS08": "건강 유해성",
    "GHS09": "수생환경 유해성",
}
_PUBCHEM_PICTOGRAMS = {
    "explosive": "GHS01", "flammable": "GHS02", "oxidizer": "GHS03", "compressed gas": "GHS04",
    "corrosive": "GHS05", "acute toxic": "GHS06", "irritant": "GHS07", "health hazard": "GHS08",
    "environmental hazard": "GHS09",
}


def pictogram_codes(labels: list[str]) -> list[str]:
    codes = []
    for label in labels or []:
        m = re.match(r"(GHS0\d)", label.strip())
        code = m.group(1) if m else _PUBCHEM_PICTOGRAMS.get(label.strip().lower())
        if code and code not in codes:
            codes.append(code)
    return codes


def catalog_list() -> list[dict]:
    """자동완성용 물질 목록 — 샘플 6종이 먼저, 그다음 이름 사전."""
    items = []
    for sid, s in sorted(CATALOG.items(), key=lambda kv: (kv[0] not in SUBSTANCES, kv[1].get("name_ko") or "")):
        items.append({"key": sid, "name_ko": s.get("name_ko"), "name_en": s.get("name_en"),
                      "cas": s.get("cas"), "sample": sid in SUBSTANCES})
    return items


def abbr_candidates(name: str) -> list[dict]:
    """여러 뜻이 있는 약어의 후보를 화면에서 버튼으로 다시 물을 수 있게 이름·CAS로 나눈다."""
    out = []
    for text in AMBIGUOUS_ABBR.get((name or "").upper(), []):
        m = re.search(r"CAS (\d{2,7}-\d{2}-\d)", text)
        out.append({"label": re.sub(r"\s*\(.*\)$", "", text), "cas": m.group(1) if m else None,
                     "detail": text})
    return out


def ppe_for(sds_list: list[dict]) -> list[str]:
    """공통 보호구 + 확인된 유해성 유형별 추가 보호구 (유형 이름을 붙여 읽기 쉽게)."""
    traits = set().union(*(_traits(s) for s in sds_list if s.get("found"))) if sds_list else set()
    classes = [h for h in HAZARD_LABELS if h in traits]
    return [f"공통: {BASE_PPE}"] + [f"{HAZARD_LABELS[c]}: {PPE_BY_CLASS[c]}"
                                   for c in classes if c in PPE_BY_CLASS]