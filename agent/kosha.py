"""안전보건공단(KOSHA) 물질안전보건자료(MSDS) Open API 조회 — 공공데이터포털(data.go.kr).

⚠️ 이 파일은 공개된 명세를 보고 만들었고, 실제 서버에 연결해 본 적은 없다(개발 환경에서
   외부 접속이 막혀 있었음). 처음 켤 때는 doc/run_guide.md의 '6-3. 연결 확인'을 한 번 실행할 것.
   응답 모양이 다르면 이 파일의 parse_* 함수만 고치면 된다.

켜는 법: .env에 DATA_GO_KR_API_KEY=발급받은키 (키는 코드·문서·깃에 절대 넣지 않는다)
끄는 법: .env에 KOSHA_ENABLED=0

명세(2026년 기준, 공공데이터포털 15001197 '안전보건공단_물질안전보건자료(MSDS)'):
  기본 주소 : https://apis.data.go.kr/B552468/msdschem1
  목록 조회 : getChemList001     serviceKey, searchWrd(검색어), searchCnd(0 국문명 | 1 CAS No |
                                 2 UN No | 3 KE No | 4 EN No), numOfRows, pageNo
              → item: casNo, chemId, chemNameKor, enNo, keNo, unNo, koshaConfirm, lastDate, openYn
  상세 조회 : getChemDetail{NN}1  serviceKey, chemId    (NN = MSDS 항목 01~16, 예: 02 → getChemDetail021)
              → item: lev, msdsItemCode, upMsdsItemCode, msdsItemNameKor, msdsItemNo, ordrIdx, itemDetail
  응답 형식 : XML (<response><header><resultCode>…</resultCode></header><body><items><item>…)
예전 주소(B552468/msdschem + getChemList/getChemDetail02)를 써야 하면 .env에서
KOSHA_BASE, KOSHA_LIST_OP, KOSHA_DETAIL_OP를 바꾼다.

여기서 쓰는 MSDS 항목: 02 유해성·위험성, 04 응급조치 요령, 07 취급 및 저장방법, 09 물리화학적 특성(인화점).
"""
import json
import os
import re
import xml.etree.ElementTree as ET
from urllib.parse import unquote

import httpx

from . import chem_rules
from .cache import LookupCache, default_cache

BASE = os.environ.get("KOSHA_BASE", "https://apis.data.go.kr/B552468/msdschem1")
LIST_OP = os.environ.get("KOSHA_LIST_OP", "getChemList001")
DETAIL_OP = os.environ.get("KOSHA_DETAIL_OP", "getChemDetail{section:02d}1")
SITE_URL = "https://msds.kosha.or.kr/"
CACHE_VERSION = "v1"
_CAS = re.compile(r"^\d{2,7}-\d{2}-\d$")
_EMPTY = {"", "자료없음", "자료 없음", "해당없음", "해당 없음", "-", "없음"}


class KoshaError(Exception):
    """인증 실패·호출 한도 초과 등 서버가 오류 코드를 돌려준 경우."""


def enabled() -> bool:
    return bool(os.environ.get("DATA_GO_KR_API_KEY")) and os.environ.get("KOSHA_ENABLED", "1") != "0"


def _service_key() -> str:
    """포털은 '인코딩 키'와 '디코딩 키'를 둘 다 준다. httpx가 주소를 다시 인코딩하므로
    인코딩 키(%2B 같은 문자가 든 것)를 넣었으면 한 번 풀어서 쓴다 — 어느 쪽을 넣어도 동작."""
    key = os.environ.get("DATA_GO_KR_API_KEY", "").strip()
    return unquote(key) if "%" in key else key


def _clean(text: str | None) -> str:
    text = re.sub(r"<br\s*/?>", "\n", text or "", flags=re.I)
    text = re.sub(r"<[^>]+>", "", text)
    return re.sub(r"[ \t]+", " ", text).strip()


def parse_items(body: str) -> list[dict]:
    """XML(또는 JSON) 응답에서 item 목록을 꺼낸다. 오류 코드면 KoshaError."""
    body = (body or "").strip()
    if body.startswith("{"):
        data = json.loads(body)
        resp = data.get("response", data)
        code = str(resp.get("header", {}).get("resultCode", "00"))
        if code not in ("00", "0", "000"):
            raise KoshaError(f"{code} {resp.get('header', {}).get('resultMsg', '')}".strip())
        items = (resp.get("body", {}).get("items") or {})
        items = items.get("item", []) if isinstance(items, dict) else items
        return [items] if isinstance(items, dict) else list(items or [])
    try:
        root = ET.fromstring(body)
    except ET.ParseError as exc:
        raise KoshaError(f"응답을 읽을 수 없음({exc})") from exc
    # 게이트웨이 오류(키 미등록·한도 초과 등)는 다른 모양으로 온다
    auth = root.findtext(".//returnAuthMsg") or root.findtext(".//errMsg")
    reason = root.findtext(".//returnReasonCode")
    if auth or reason:
        raise KoshaError(f"{reason or ''} {auth or ''}".strip())
    code = (root.findtext(".//resultCode") or "00").strip()
    if code not in ("00", "0", "000"):
        raise KoshaError(f"{code} {(root.findtext('.//resultMsg') or '').strip()}".strip())
    return [{child.tag: (child.text or "").strip() for child in item}
            for item in root.iter("item")]


def _get(client: httpx.Client, op: str, params: dict) -> list[dict]:
    """오류 메시지에 요청 주소를 넣지 않는다 — 주소에 serviceKey(API 키)가 들어 있어서,
    httpx 기본 오류 문구를 그대로 쓰면 화면·로그에 키가 찍힌다."""
    try:
        r = client.get(f"{BASE}/{op}", params={"serviceKey": _service_key(), **params})
    except httpx.HTTPError as exc:
        raise KoshaError(f"접속 실패({type(exc).__name__})") from None
    if r.status_code >= 400:
        raise KoshaError(f"HTTP {r.status_code}")
    return parse_items(r.text)


def search(client: httpx.Client, word: str) -> list[dict]:
    cnd = "1" if _CAS.match(word) else "0"   # CAS 번호면 CAS 검색, 아니면 국문명 검색
    return _get(client, LIST_OP, {"searchWrd": word, "searchCnd": cnd,
                                  "numOfRows": "10", "pageNo": "1"})


def pick(items: list[dict], word: str, cas: str | None) -> dict | None:
    """검색 결과에서 같은 물질을 고른다 — CAS가 같거나 이름이 정확히 같은 것만 인정한다.
    '톨루엔'으로 검색해 '톨루엔 다이아이소사이아네이트'를 고르는 실수를 막기 위해서다."""
    squash = lambda s: re.sub(r"\s+", "", s or "")
    for it in items:
        if cas and it.get("casNo") == cas:
            return it
    for it in items:
        if _CAS.match(word) and it.get("casNo") == word:
            return it
        if squash(it.get("chemNameKor")) == squash(word):
            return it
    return None


def detail(client: httpx.Client, chem_id: str, section: int) -> list[dict]:
    return _get(client, DETAIL_OP.format(section=section), {"chemId": chem_id})


def _named(items: list[dict]) -> list[tuple[str, str]]:
    return [(it.get("msdsItemNameKor") or "", _clean(it.get("itemDetail")))
            for it in items if _clean(it.get("itemDetail")) not in _EMPTY]


def parse_hazards(items: list[dict]) -> dict:
    """02 유해성·위험성 → 신호어, 그림문자(GHS 코드), 유해·위험 문구."""
    named = _named(items)
    signal = next((v for n, v in named if "신호어" in n), None)
    pic_text = " ".join(v for n, v in named if "그림문자" in n) or " ".join(v for _, v in named)
    pictograms = [f"{c} {chem_rules.PICTOGRAMS[c]}"
                  for c in dict.fromkeys(re.findall(r"GHS0[1-9]", pic_text))]
    lines: list[str] = []
    for n, v in named:
        if "유해" in n and "문구" in n:
            lines += [x.strip() for x in re.split(r"[\n|]", v) if re.match(r"\s*(EUH|H)\d{3}", x)]
    if not lines:   # 문구 항목을 못 찾으면 02항 전체에서 H코드만 모은다(뜻은 한국어 표로)
        codes = dict.fromkeys(re.findall(r"(?:EUH|H)\d{3}", " ".join(v for _, v in named)))
        lines = list(codes)
    statements = list(dict.fromkeys(chem_rules.korean_hazard(x) for x in lines))[:12]
    return {"signal_word": signal, "pictograms": pictograms, "hazard_statements": statements,
            "hazard_classes": chem_rules.classes_from_hazard_statements(statements),
            "hazard_tags": chem_rules.tags_from_hazard_statements(statements)}


_AID_ROUTES = [("흡입", "흡입"), ("피부", "피부"), ("눈", "눈"), ("먹었", "섭취"), ("섭취", "섭취"),
               ("삼켰", "섭취")]


def parse_first_aid(items: list[dict]) -> dict:
    out: dict[str, str] = {}
    for n, v in _named(items):
        route = next((r for key, r in _AID_ROUTES if key in n), None)
        if route and route not in out:
            out[route] = v[:300]
    return out


def parse_handling(items: list[dict]) -> dict:
    handling = [v[:250] for n, v in _named(items) if "취급" in n]
    storage = [v[:250] for n, v in _named(items) if "저장" in n or "보관" in n]
    return {"handling": " / ".join(handling[:2]) or None, "storage": " / ".join(storage[:2]) or None}


def parse_flash_point(items: list[dict]) -> str | None:
    return next((v[:60] for n, v in _named(items) if "인화점" in n), None)


def _fetch_raw(word: str, cas: str | None, client: httpx.Client) -> dict:
    item = pick(search(client, word), word, cas)
    if not item:
        return {"missing": f"KOSHA MSDS에서 '{word}'과(와) 일치하는 물질을 찾지 못함"}
    chem_id = item.get("chemId")
    hazards = parse_hazards(detail(client, chem_id, 2))
    first_aid = parse_first_aid(detail(client, chem_id, 4))
    if not hazards["hazard_statements"] and not first_aid:
        return {"missing": "KOSHA MSDS에 유해성·응급조치 정보가 없음", "chem_id": chem_id}
    return {"chem_id": chem_id, "name_ko": item.get("chemNameKor"), "cas": item.get("casNo"),
            **hazards, "first_aid": first_aid,
            **parse_handling(detail(client, chem_id, 7)),
            "flash_point": parse_flash_point(detail(client, chem_id, 9))}


def fetch_kosha(word: str, display: str, catalog_key: str | None = None, cas: str | None = None,
                timeout: float = 10.0, transport: httpx.BaseTransport | None = None,
                cache: LookupCache | None = None) -> dict:
    """국문명 또는 CAS로 KOSHA MSDS를 조회해 sample·pubchem과 같은 모양의 dict로 돌려준다."""
    cache = cache or default_cache()
    key = f"kosha:{CACHE_VERSION}:{(cas or word).strip().lower()}"
    raw = cache.get(key)
    cached = raw is not None
    if raw is None:
        with httpx.Client(timeout=timeout, transport=transport) as client:
            raw = _fetch_raw(word, cas, client)
        if "missing" not in raw:
            cache.set(key, raw)
    if "missing" in raw:
        return {"query": display, "found": False, "source": "kosha", "reason": raw["missing"]}

    entry = chem_rules.CATALOG.get(catalog_key or "", {})
    result = {
        "query": display,
        "found": True,
        "source": "kosha",
        "source_url": SITE_URL,
        "kosha_chem_id": raw["chem_id"],
        "name_ko": entry.get("name_ko") or raw.get("name_ko"),
        "name_en": entry.get("name_en"),
        "cas": entry.get("cas") or raw.get("cas"),
        "signal_word": raw.get("signal_word"),
        "pictograms": raw.get("pictograms", []),
        "hazard_statements": raw.get("hazard_statements", []),
        "hazard_classes": raw.get("hazard_classes", []),
        "tags": list(dict.fromkeys(entry.get("tags", []) + raw.get("hazard_tags", []))),
        "flash_point": raw.get("flash_point"),
        "first_aid": raw.get("first_aid", {}),
        "handling": raw.get("handling"),
        "storage": raw.get("storage"),
        "cached": cached,
    }
    if not result["hazard_statements"]:
        result["note"] = "KOSHA MSDS에 유해·위험 문구가 없음 — 공식 SDS 확인 필요"
    return result