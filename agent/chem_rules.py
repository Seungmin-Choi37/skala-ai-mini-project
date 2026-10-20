"""화학물질 도메인 규칙 — 모델에게 다시 묻지 않고 코드가 내리는 결정들을 한곳에 모은다.

- 물질 카탈로그 : data/substances.json(GHS 데이터가 있는 샘플) + data/name_map.json(이름 사전)
- 물질 인식     : 카탈로그 별칭 매칭 + CAS 번호 + 사전에 없는 화학물질 이름 후보 추출
- 의도 인식     : 섭취(마실/먹을)·합성(만들/합성) 의도 — 안내문 맨 앞 경고에 쓰인다
- 유해성 유형   : GHS 유해·위험 문구(H코드)에서 hazard class를 뽑는다
- 혼촉 위험     : 물질 쌍마다 hazard class·tag 조합 규칙으로 판정
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

# 유해성 유형(= safety guide Resource 이름)과 화면 표시용 한국어 이름.
# 순서가 우선순위다 — replan_check가 지침을 고를 때 위험한 것부터 고른다.
HAZARD_LABELS = {
    "explosive": "폭발성",
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
    "water-reactive": "물·습기 차단, 건조한 불활성 분위기, 금속 화재용(D급) 소화기 준비",
    "corrosive": "보안경+안면보호구, 내산·내알칼리 장갑(니트릴/네오프렌)",
    "oxidizer": "보안경(튐 위험 시 안면보호구), 내화학 장갑",
    "toxic": "흄후드 필수, 내화학 장갑(필요 시 이중 착용)",
    "flammable": "면 또는 난연 실험복(합성섬유 피함), 점화원 제거",
    "health-hazard": "흄후드 사용, 노출 시간 최소화",
}
BASE_PPE = "보안경, 실험복, 니트릴 장갑"

# 혼촉 규칙: (조건 A, 조건 B, 위험, 권고). 조건은 hazard class 또는 tag 이름.
KEEP_APART = "함께 섞거나 같은 곳에 보관하지 말 것"
INCOMPATIBILITY_RULES = [
    ("oxidizer", "flammable", "산화제와 인화성 물질이 만나면 화재·폭발 위험이 크다", KEEP_APART),
    ("oxidizing-acid", "organic-solvent", "산화성 산(질산 등)과 유기용매는 격렬하게 반응할 수 있다",
     KEEP_APART),
    ("hypochlorite", "acid", "차아염소산염(락스 등)과 산이 만나면 유독한 염소 기체가 생긴다", KEEP_APART),
    ("hypochlorite", "ammonia", "차아염소산염과 암모니아가 만나면 유독한 클로라민 기체가 생긴다",
     KEEP_APART),
    ("cyanide", "acid", "시안화물과 산이 만나면 맹독성 시안화수소 기체가 생긴다", KEEP_APART),
    ("acid", "base", "산과 염기를 섞으면 큰 중화열이 나고 튈 수 있다",
     "보관은 분리하고, 중화 실험처럼 일부러 섞을 때는 묽힌 용액을 소량씩 저으며(필요하면 "
     "냉각하며) 천천히 더할 것"),
]

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
              "중화", "반응", "희석", "가열", "장소", "학교", "회사", "집", "우리", "모두", "다음"}

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
    return set(sds.get("hazard_classes", [])) | set(sds.get("tags", []))


def check_incompatibility(sds_list: list[dict]) -> list[dict]:
    """물질 쌍마다 혼촉 규칙을 검사한다. SDS 조회에 실패한 물질도 이름 사전의 tags
    (산·염기·차아염소산염 등)가 있으면 검사에 포함한다 — 위험 조합을 놓치지 않기 위해."""
    usable = [s for s in sds_list if _traits(s)]
    conflicts = []
    for a, b in combinations(usable, 2):
        ta, tb = _traits(a), _traits(b)
        for x, y, warning, advice in INCOMPATIBILITY_RULES:
            if (x in ta and y in tb) or (y in ta and x in tb):
                conflicts.append({"pair": [display_name(a), display_name(b)],
                                  "warning": warning, "advice": advice})
    return conflicts


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
