"""PubChem 실데이터 조회 (CHEM_DATA_MODE=live일 때 MCP Tool이 사용).

API 키가 필요 없는 공개 API다. 응답이 매우 커지는 'Safety and Hazards' 전체 대신,
필요한 작은 섹션 3개만 따로 받는다.
  ① 이름/CAS → CID : /rest/pug/compound/name/{name}/cids/JSON
  ② CID → GHS 분류  : /rest/pug_view/data/compound/{cid}/JSON?heading=GHS Classification
  ③ CID → 응급조치  : ...?heading=First Aid Measures
  ④ CID → 취급·보관 : ...?heading=Handling and Storage   (없는 물질도 많다 — 404면 건너뜀)

실제 응답 구조(확인함):
- GHS Classification 섹션의 Information 항목 Name이 "Pictogram(s)", "Signal",
  "GHS Hazard Statements", "Precautionary Statement Codes"이고, 출처(기관)마다 한 벌씩 반복된다.
  그림문자 이름은 StringWithMarkup[].Markup[].Extra에 있다("Flammable", "Irritant" …).
- 응급조치는 'First Aid' 섹션의 Information 항목 Name이 "Inhalation First Aid",
  "Skin First Aid", "Eye First Aid", "Ingestion First Aid"인 형태다(하위 섹션이 아님).

PubChem 이용 정책상 호출 빈도 제한이 있어(대략 초당 5회) 결과를 프로세스 메모리에 캐시한다.
"""
import os
import re
from urllib.parse import quote

import httpx

from . import chem_rules

# 테스트 때 가짜 PubChem 서버로 바꿀 수 있게 환경변수로 덮어쓸 수 있다
BASE = os.environ.get("PUBCHEM_BASE", "https://pubchem.ncbi.nlm.nih.gov/rest")
_CACHE: dict[str, dict] = {}
_FIRST_AID_NAMES = {"Inhalation First Aid": "흡입", "Skin First Aid": "피부",
                    "Eye First Aid": "눈", "Ingestion First Aid": "섭취"}


def _strings(info: dict) -> list[str]:
    out = []
    for swm in info.get("Value", {}).get("StringWithMarkup", []):
        text = re.sub(r"<[^>]+>", "", swm.get("String", "")).strip()
        if text:
            out.append(text)
    return out


def _pictograms(info: dict) -> list[str]:
    return [mk["Extra"] for swm in info.get("Value", {}).get("StringWithMarkup", [])
            for mk in swm.get("Markup", []) if mk.get("Extra")]


def _infos(record_json: dict) -> list[tuple[str, dict]]:
    """Section 트리를 재귀로 내려가며 (속한 TOCHeading, Information) 쌍을 모은다."""
    out: list[tuple[str, dict]] = []

    def walk(section: dict):
        for info in section.get("Information", []):
            out.append((section.get("TOCHeading", ""), info))
        for child in section.get("Section", []):
            walk(child)

    for sec in record_json.get("Record", {}).get("Section", []):
        walk(sec)
    return out


def parse_ghs(record_json: dict) -> dict:
    signal, pictograms, hazards, precautions = None, [], [], []
    for heading, info in _infos(record_json):
        if heading != "GHS Classification":
            continue
        name = info.get("Name", "")
        if name == "Signal" and signal is None:
            signal = (_strings(info) or [None])[0]
        elif name == "Pictogram(s)" and not pictograms:
            pictograms = _pictograms(info)
        elif name == "GHS Hazard Statements" and not hazards:
            hazards = _strings(info)
        elif name == "Precautionary Statement Codes" and not precautions:
            precautions = _strings(info)
    hazards = hazards[:12]
    return {"signal_word": signal, "pictograms": pictograms, "hazard_statements": hazards,
            "precautionary": precautions[:2],
            "hazard_classes": chem_rules.classes_from_hazard_statements(hazards)}


def parse_first_aid(record_json: dict) -> dict:
    first_aid: dict[str, str] = {}
    general: list[str] = []
    for _, info in _infos(record_json):
        route = _FIRST_AID_NAMES.get(info.get("Name", ""))
        texts = _strings(info)
        if route and texts and route not in first_aid:
            first_aid[route] = texts[0][:300]
        elif not route and texts and len(general) < 2:
            general.append(texts[0][:300])
    if not first_aid and general:
        first_aid["일반"] = " / ".join(general)
    return first_aid


def parse_handling(record_json: dict) -> dict:
    handling, storage = [], []
    for heading, info in _infos(record_json):
        texts = _strings(info)
        if not texts:
            continue
        if "Storage" in heading and len(storage) < 2:
            storage.append(texts[0][:250])
        elif "Handling" in heading and len(handling) < 2:
            handling.append(texts[0][:250])
    return {"handling": " / ".join(dict.fromkeys(handling)) or None,
            "storage": " / ".join(dict.fromkeys(storage)) or None}


def _view(client: httpx.Client, cid: int, heading: str) -> dict | None:
    r = client.get(f"{BASE}/pug_view/data/compound/{cid}/JSON", params={"heading": heading})
    if r.status_code == 404:
        return None
    r.raise_for_status()
    return r.json()


def fetch_safety(lookup: str, display: str, catalog_key: str | None = None,
                 timeout: float = 10.0) -> dict:
    """lookup(영문명 또는 CAS)으로 PubChem을 조회해 sample 모드와 같은 모양의 dict로 돌려준다."""
    if lookup in _CACHE:
        return _CACHE[lookup]
    with httpx.Client(timeout=timeout) as client:
        r = client.get(f"{BASE}/pug/compound/name/{quote(lookup, safe='')}/cids/JSON")
        if r.status_code == 404:
            return {"query": display, "found": False, "source": "pubchem",
                    "reason": f"PubChem에서 '{lookup}'을(를) 찾지 못함"}
        r.raise_for_status()
        cid = r.json()["IdentifierList"]["CID"][0]
        ghs_json = _view(client, cid, "GHS Classification")
        aid_json = _view(client, cid, "First Aid Measures")
        hs_json = _view(client, cid, "Handling and Storage")
        title = (ghs_json or aid_json or {}).get("Record", {}).get("RecordTitle")

    ghs = parse_ghs(ghs_json) if ghs_json else {
        "signal_word": None, "pictograms": [], "hazard_statements": [], "precautionary": [],
        "hazard_classes": []}
    if not ghs_json and not aid_json:
        return {"query": display, "found": False, "source": "pubchem", "cid": cid,
                "reason": "PubChem에 GHS 분류·응급조치 정보가 없음"}
    entry = chem_rules.CATALOG.get(catalog_key or "", {})
    result = {
        "query": display,
        "found": True,
        "source": "pubchem",
        "source_url": f"https://pubchem.ncbi.nlm.nih.gov/compound/{cid}#section=Safety-and-Hazards",
        "cid": cid,
        "name_ko": entry.get("name_ko"),
        "name_en": entry.get("name_en") or title or lookup,
        "cas": entry.get("cas"),
        **ghs,
        "tags": entry.get("tags", []),
        "flash_point": None,
        "first_aid": parse_first_aid(aid_json) if aid_json else {},
        **(parse_handling(hs_json) if hs_json else {"handling": None, "storage": None}),
    }
    if not ghs["hazard_statements"]:
        result["note"] = "PubChem에 GHS 유해·위험 문구가 없음 — 공식 SDS 확인 필요"
    _CACHE[lookup] = result
    return result