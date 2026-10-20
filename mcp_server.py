"""화학안전 MCP 서버("chem") — Tool / Resource / Prompt 3대 capability.

FastAPI 백엔드(server.py)가 lifespan에서 이 파일을 subprocess(stdio)로 1회 기동해
서버 수명 동안 재사용한다(원본 all_total의 mcp_server.py와 같은 구조).

- Tool     get_substance_safety : 물질명 → SDS 요약 (호출 시점에 조회하는 동적 데이터)
             CHEM_DATA_MODE=sample → data/substances.json 6종만
             CHEM_DATA_MODE=live   → PubChem 실시간 조회(이름 사전으로 한국어명→CAS 변환),
                                     실패 시 샘플이 있으면 샘플로 폴백
- Tool     get_kosha_msds       : 안전보건공단 MSDS Open API 자리 (명세 확인 후 구현)
- Resource safety-guide://hazard/{hazard_type} : 유해성 유형별 취급 지침(정적 문서)
- Prompt   safety_notice_prompt : 사용 전 안전 안내문 작성 표준 절차
"""
import os
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))

from fastmcp import FastMCP  # noqa: E402

from agent import chem_rules  # noqa: E402

mcp = FastMCP(name="chem-safety")
GUIDE_DIR = ROOT / "data" / "safety_guides"


def _from_sample(name: str) -> dict | None:
    sid = chem_rules.resolve(name)
    if not sid or sid not in chem_rules.SUBSTANCES:
        return None
    s = chem_rules.SUBSTANCES[sid]
    return {"query": s["name_ko"], "found": True, "source": "sample",
            "source_url": None, **{k: v for k, v in s.items() if k != "aliases"}}


def _not_found(display: str, reason: str, entry: dict | None = None) -> dict:
    """조회 실패도 카탈로그의 tags(산·염기·락스 등)는 실어 보낸다 — 혼촉 점검은 GHS 데이터가
    없어도 할 수 있어야 한다(예: 락스+염산 → 염소 기체)."""
    entry = entry or {}
    return {"query": display, "found": False, "reason": reason,
            "name_ko": entry.get("name_ko"), "tags": entry.get("tags", [])}


@mcp.tool()
def get_substance_safety(name: str, lookup: str = "") -> dict:
    """물질 SDS 핵심 정보(GHS 분류, 응급조치, 취급, 보관)를 조회합니다.

    name   : 물질명(한국어·영어·CAS) 또는 카탈로그 키
    lookup : PubChem 조회에 쓸 영문명이나 CAS (없으면 카탈로그에서 찾거나 name을 그대로 시도)
    """
    mode = os.environ.get("CHEM_DATA_MODE", "live")
    if name.upper() in chem_rules.AMBIGUOUS_ABBR:
        candidates = " / ".join(chem_rules.AMBIGUOUS_ABBR[name.upper()])
        return _not_found(name, f"'{name}'은(는) 여러 물질의 약어라 어떤 물질인지 확정할 수 없습니다 "
                                f"(후보: {candidates}) — 정식 이름이나 CAS 번호로 다시 물어봐 주세요")
    if chem_rules.is_abbreviation(name):
        return _not_found(name, f"'{name}'이(가) 어떤 물질의 약어인지 알 수 없습니다 — 추측하지 않고 "
                                "정식 이름이나 CAS 번호로 다시 물어봐 주시길 요청합니다")
    sid = chem_rules.resolve(name)
    entry = chem_rules.CATALOG.get(sid or "", {})
    display = entry.get("name_ko") or entry.get("name_en") or name
    sample = _from_sample(name)

    if mode != "live":
        if sample:
            return sample
        return _not_found(display, "샘플 데이터(6종)에 없는 물질 — .env의 CHEM_DATA_MODE=live로 "
                                   "바꾸면 PubChem에서 조회합니다", entry)

    query = lookup or entry.get("cas") or entry.get("name_en")
    if not query and re.fullmatch(r"[A-Za-z0-9 ,()\-]+", name):
        query = name          # 영문명·CAS를 직접 입력한 경우
    if not query:
        return sample or _not_found(
            display, "PubChem은 한국어 이름을 모릅니다 — 영문명이나 CAS 번호로 다시 물어봐 주세요", entry)
    from agent.pubchem import fetch_safety
    try:
        result = fetch_safety(query, display, sid)
    except Exception as exc:   # 네트워크 오류 등
        if sample:
            sample["note"] = f"PubChem 호출 오류({type(exc).__name__}) → 샘플 데이터 사용"
            return sample
        return _not_found(display, f"PubChem 호출 오류({type(exc).__name__}) — 인터넷 연결 확인", entry)
    if not result.get("found"):
        if sample:
            sample["note"] = f"{result.get('reason')} → 샘플 데이터 사용"
            return sample
        result.update(name_ko=entry.get("name_ko"), tags=entry.get("tags", []))
    return result


@mcp.tool()
def get_kosha_msds(name: str) -> dict:
    """안전보건공단 MSDS Open API 조회 (확장 지점 — 아직 파이프라인에서 호출하지 않음).

    공공데이터포털 활용가이드의 요청주소·파라미터를 확인한 뒤 구현한다.
    API 키는 .env의 DATA_GO_KR_API_KEY에서 읽는다(코드에 직접 쓰지 않음)."""
    if not os.environ.get("DATA_GO_KR_API_KEY"):
        return {"query": name, "available": False,
                "reason": "DATA_GO_KR_API_KEY가 설정되지 않음"}
    return {"query": name, "available": False,
            "reason": "KOSHA API 요청 명세 확인 후 구현 예정 (README의 '확장: KOSHA' 참고)"}


@mcp.resource("safety-guide://hazard/{hazard_type}")
def get_safety_guide(hazard_type: str) -> str:
    """유해성 유형(flammable, oxidizer, corrosive, toxic, health-hazard, irritant, general)별 취급 지침."""
    path = GUIDE_DIR / f"{hazard_type}.md"
    if not path.exists():
        path = GUIDE_DIR / "general.md"
    return path.read_text(encoding="utf-8")


@mcp.prompt()
def safety_notice_prompt(substances: str, purpose: str) -> str:
    """사용 전 안전 안내문 표준 절차 — planning 노드가 계획을 세울 때 이 절차를 따른다."""
    return f"""다음 화학물질의 사용 전 안전 안내문을 작성하기 위한 표준 절차를 수행하세요.

## 요청 정보
- 대상 물질: {substances}
- 사용 목적/상황: {purpose}

## 절차 (반드시 순서대로 수행)
1. get_substance_safety Tool로 물질마다 SDS 핵심 정보를 조회하세요.
2. GHS 분류에서 확인된 유해성 유형별 취급 지침(safety-guide://hazard/...)을 확인하세요.
3. 물질이 2종 이상이면 함께 사용·보관할 때의 혼촉 위험을 점검하세요.
4. 조회된 정보만 사용해 유해성·보호구·취급·혼용·응급조치·보관/폐기 순서로 안내문을 작성하세요.
"""


if __name__ == "__main__":
    try:
        mcp.run(show_banner=False)
    except TypeError:   # 구버전 fastmcp 호환
        mcp.run()