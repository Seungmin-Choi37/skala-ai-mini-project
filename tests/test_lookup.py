"""물질 조회 — PubChem 파서·캐시, KOSHA 파서, MCP Tool의 조회 순서(KOSHA → PubChem → 샘플).

실제 인터넷에는 나가지 않는다. httpx.MockTransport로 가짜 응답을 돌려준다.
PubChem 가짜 응답은 실제 응답 구조(확인함)를 따른다. KOSHA 가짜 응답은 공개 명세의 필드명을
따라 만든 것이다 — 실제 서버 응답과 다르면 agent/kosha.py의 parse_* 와 이 파일을 같이 고친다.
"""
import httpx
import pytest

import mcp_server
from agent import kosha, pubchem
from agent.cache import LookupCache


# ── PubChem ──────────────────────────────────────────────────────────
def _info(name, strings=(), extras=None):
    swm = [{"String": "", "Markup": [{"Extra": e} for e in extras]}] if extras \
        else [{"String": s} for s in strings]
    return {"Name": name, "Value": {"StringWithMarkup": swm}} if name else {"Value": {"StringWithMarkup": swm}}


def _record(title, heading, infos):
    return {"Record": {"RecordTitle": title, "Section": [
        {"TOCHeading": "Safety and Hazards", "Section": [{"TOCHeading": heading, "Information": infos}]}]}}


THF_VIEWS = {
    "GHS Classification": _record("Tetrahydrofuran", "GHS Classification", [
        _info("Pictogram(s)", extras=["Flammable", "Irritant"]),
        _info("Signal", ["Danger"]),
        _info("GHS Hazard Statements", ["H225 (100%): Highly Flammable liquid and vapor",
                                        "H319 (98%): Causes serious eye irritation",
                                        "EUH019: May form explosive peroxides"]),
        _info("Signal", ["Warning"]),   # 두 번째 출처 — 무시돼야 한다
    ]),
    "First Aid Measures": _record("Tetrahydrofuran", "First Aid", [
        _info("Inhalation First Aid", ["Fresh air, rest."]),
        _info("Eye First Aid", ["Rinse with plenty of water."]),
    ]),
    "Reactive Group": _record("Tetrahydrofuran", "Reactive Group", [_info("", ["Ethers"])]),
}


def pubchem_transport(calls: list):
    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request.url.path)
        if request.url.path.endswith("/cids/JSON"):
            if "109-99-9" in request.url.path:
                return httpx.Response(200, json={"IdentifierList": {"CID": [8028]}})
            return httpx.Response(404)
        view = THF_VIEWS.get(request.url.params.get("heading"))
        return httpx.Response(200, json=view) if view else httpx.Response(404)
    return httpx.MockTransport(handler)


def test_pubchem_parse_korean_and_tags(tmp_path):
    calls = []
    r = pubchem.fetch_safety("109-99-9", "테트라하이드로퓨란", "thf",
                             transport=pubchem_transport(calls), cache=LookupCache(tmp_path / "c.db", 30))
    assert r["found"] and r["source"] == "pubchem" and r["cid"] == 8028
    assert r["signal_word"] == "Danger"                       # 첫 출처만
    assert r["hazard_statements"][0] == "H225 고인화성 액체 및 증기"
    assert r["hazard_statements_en"][0].startswith("H225 (100%)")
    assert "EUH019 폭발성 과산화물을 생성할 수 있음" in r["hazard_statements"]
    assert r["hazard_classes"] == ["flammable", "irritant"]
    assert "peroxide-former" in r["tags"] and "organic-solvent" in r["tags"]
    assert r["reactive_groups"] == ["Ethers"]
    assert r["first_aid"] == {"흡입": "Fresh air, rest.", "눈": "Rinse with plenty of water."}
    assert r["storage"] is None                               # Handling and Storage 404


def test_pubchem_not_found():
    r = pubchem.fetch_safety("nope", "없는물질", transport=pubchem_transport([]),
                             cache=LookupCache(ttl_days=0))
    assert r == {"query": "없는물질", "found": False, "source": "pubchem",
                 "reason": "PubChem에서 'nope'을(를) 찾지 못함"}


def test_pubchem_sqlite_cache_survives_restart(tmp_path):
    path = tmp_path / "cache.db"
    calls = []
    first = pubchem.fetch_safety("109-99-9", "THF", "thf", transport=pubchem_transport(calls),
                                 cache=LookupCache(path, 30))
    n = len(calls)
    assert n >= 3 and first["cached"] is False

    def broken(request):
        raise AssertionError("캐시가 있으면 네트워크를 부르면 안 된다")

    # 새 LookupCache 객체 = 서버를 다시 켠 것과 같다
    second = pubchem.fetch_safety("109-99-9", "THF", "thf", transport=httpx.MockTransport(broken),
                                  cache=LookupCache(path, 30))
    assert second["cached"] is True
    assert second["hazard_statements"] == first["hazard_statements"]


def test_cache_expiry_and_disable(tmp_path):
    c = LookupCache(tmp_path / "x.db", ttl_days=1)
    c.set("k", {"a": 1})
    assert c.get("k") == {"a": 1}
    c.ttl = -1                       # 유효 기간이 지난 것처럼
    assert c.get("k") is None
    off = LookupCache(tmp_path / "y.db", ttl_days=0)
    off.set("k", {"a": 1})
    assert off.get("k") is None and not (tmp_path / "y.db").exists()


# ── KOSHA ────────────────────────────────────────────────────────────
def _xml_items(items: list[dict]) -> str:
    body = "".join("<item>" + "".join(f"<{k}>{v}</{k}>" for k, v in it.items()) + "</item>"
                   for it in items)
    return ("<?xml version='1.0' encoding='UTF-8'?><response><header><resultCode>00</resultCode>"
            f"<resultMsg>NORMAL SERVICE.</resultMsg></header><body><items>{body}</items>"
            f"<totalCount>{len(items)}</totalCount></body></response>")


KOSHA_DETAIL = {
    2: [{"msdsItemNameKor": "신호어", "itemDetail": "위험"},
        {"msdsItemNameKor": "그림문자", "itemDetail": "GHS02|GHS07|GHS08"},
        {"msdsItemNameKor": "유해·위험문구",
         "itemDetail": "H225 고인화성 액체 및 증기|H304 삼켜서 기도로 유입되면 치명적일 수 있음|H315 피부에 자극을 일으킴"}],
    4: [{"msdsItemNameKor": "눈에 들어갔을 때", "itemDetail": "물로 15분 이상 씻어낸다"},
        {"msdsItemNameKor": "피부에 접촉했을 때", "itemDetail": "오염된 옷을 벗고 비누와 물로 씻는다"},
        {"msdsItemNameKor": "흡입했을 때", "itemDetail": "신선한 공기가 있는 곳으로 옮긴다"},
        {"msdsItemNameKor": "먹었을 때", "itemDetail": "구토를 유도하지 않는다"}],
    7: [{"msdsItemNameKor": "안전취급요령", "itemDetail": "점화원을 멀리한다"},
        {"msdsItemNameKor": "안전한 저장방법", "itemDetail": "서늘하고 환기가 잘 되는 곳에 보관"}],
    9: [{"msdsItemNameKor": "인화점", "itemDetail": "4 ℃"},
        {"msdsItemNameKor": "pH", "itemDetail": "자료없음"}],
}


def kosha_transport(seen: list, list_items=None):
    list_items = list_items if list_items is not None else [
        {"chemId": "000123", "chemNameKor": "톨루엔 다이아이소사이아네이트", "casNo": "584-84-9"},
        {"chemId": "000777", "chemNameKor": "톨루엔", "casNo": "108-88-3"}]

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        op = request.url.path.rsplit("/", 1)[-1]
        if op == "getChemList001":
            return httpx.Response(200, text=_xml_items(list_items))
        section = int(op[len("getChemDetail"):-1])     # getChemDetail021 → 2
        assert request.url.params["chemId"] == "000777"
        return httpx.Response(200, text=_xml_items(KOSHA_DETAIL.get(section, [])))
    return httpx.MockTransport(handler)


def test_kosha_fetch_parses_sections(monkeypatch):
    monkeypatch.setenv("DATA_GO_KR_API_KEY", "abc%2Bdef%3D%3D")   # 인코딩 키
    seen = []
    r = kosha.fetch_kosha("톨루엔", "톨루엔", "toluene", transport=kosha_transport(seen),
                          cache=LookupCache(ttl_days=0))
    first = seen[0].url
    assert first.params["searchCnd"] == "0" and first.params["searchWrd"] == "톨루엔"
    assert first.params["serviceKey"] == "abc+def=="               # 한 번 풀어서 보낸다
    assert r["found"] and r["source"] == "kosha" and r["kosha_chem_id"] == "000777"
    assert r["signal_word"] == "위험"
    assert r["pictograms"] == ["GHS02 인화성", "GHS07 경고", "GHS08 건강 유해성"]
    assert r["hazard_statements"][0] == "H225 고인화성 액체 및 증기"
    assert r["hazard_classes"] == ["flammable", "health-hazard", "irritant"]
    assert set(r["first_aid"]) == {"눈", "피부", "흡입", "섭취"}
    assert r["handling"] == "점화원을 멀리한다" and r["storage"].startswith("서늘")
    assert r["flash_point"] == "4 ℃"
    assert "organic-solvent" in r["tags"]                         # 이름 사전 태그 유지


def test_kosha_search_by_cas_and_no_partial_match():
    seen = []
    r = kosha.fetch_kosha("108-88-3", "CAS 108-88-3", cas="108-88-3",
                          transport=kosha_transport(seen), cache=LookupCache(ttl_days=0))
    assert seen[0].url.params["searchCnd"] == "1" and r["found"]
    only_partial = [{"chemId": "000123", "chemNameKor": "톨루엔 다이아이소사이아네이트", "casNo": "584-84-9"}]
    r2 = kosha.fetch_kosha("톨루엔", "톨루엔", transport=kosha_transport([], only_partial),
                           cache=LookupCache(ttl_days=0))
    assert not r2["found"] and "일치하는 물질" in r2["reason"]


def test_kosha_gateway_error_raises():
    err = ("<OpenAPI_ServiceResponse><cmmMsgHeader><errMsg>SERVICE ERROR</errMsg>"
           "<returnAuthMsg>SERVICE_KEY_IS_NOT_REGISTERED_ERROR</returnAuthMsg>"
           "<returnReasonCode>30</returnReasonCode></cmmMsgHeader></OpenAPI_ServiceResponse>")
    with pytest.raises(kosha.KoshaError, match="SERVICE_KEY_IS_NOT_REGISTERED"):
        kosha.parse_items(err)
    with pytest.raises(kosha.KoshaError, match="22"):
        kosha.parse_items("<response><header><resultCode>22</resultCode><resultMsg>LIMITED</resultMsg>"
                          "</header></response>")


def test_kosha_json_response_also_works():
    items = kosha.parse_items('{"response": {"header": {"resultCode": "00"}, "body": {"items": '
                              '{"item": {"chemId": "1", "chemNameKor": "벤젠"}}}}}')
    assert items == [{"chemId": "1", "chemNameKor": "벤젠"}]


def test_kosha_disabled_without_key(monkeypatch):
    monkeypatch.delenv("DATA_GO_KR_API_KEY", raising=False)
    monkeypatch.setenv("KOSHA_ENABLED", "1")
    assert not kosha.enabled()
    assert mcp_server.get_kosha_msds("톨루엔")["available"] is False


# ── MCP Tool 조회 순서 ───────────────────────────────────────────────
def test_tool_sample_mode():
    r = mcp_server.get_substance_safety("톨루엔")
    assert r["found"] and r["source"] == "sample"
    r = mcp_server.get_substance_safety("자일렌")
    assert not r["found"] and "샘플 데이터" in r["reason"] and r["tags"] == ["organic-solvent"]


def test_tool_abbreviations():
    r = mcp_server.get_substance_safety("DMA")
    assert not r["found"] and len(r["candidates"]) == 2
    assert "약어" in mcp_server.get_substance_safety("QXZ")["reason"]


def test_tool_live_prefers_kosha(monkeypatch):
    monkeypatch.setenv("CHEM_DATA_MODE", "live")
    monkeypatch.setenv("DATA_GO_KR_API_KEY", "dummy")
    monkeypatch.setenv("KOSHA_ENABLED", "1")
    asked = {}

    def fake_kosha(word, display, catalog_key=None, cas=None, **kw):
        asked.update(word=word, cas=cas)
        return {"query": display, "found": True, "source": "kosha", "name_ko": display}

    monkeypatch.setattr(kosha, "fetch_kosha", fake_kosha)
    monkeypatch.setattr(pubchem, "fetch_safety", lambda *a, **k: pytest.fail("PubChem까지 가면 안 됨"))
    r = mcp_server.get_substance_safety("톨루엔", "108-88-3")
    assert r["source"] == "kosha" and asked == {"word": "108-88-3", "cas": "108-88-3"}


def test_tool_live_kosha_error_falls_back_to_pubchem(monkeypatch):
    monkeypatch.setenv("CHEM_DATA_MODE", "live")
    monkeypatch.setenv("DATA_GO_KR_API_KEY", "dummy")
    monkeypatch.setenv("KOSHA_ENABLED", "1")

    def boom(*a, **k):
        raise kosha.KoshaError("22 LIMITED NUMBER OF SERVICE REQUESTS EXCEEDS ERROR")

    monkeypatch.setattr(kosha, "fetch_kosha", boom)
    monkeypatch.setattr(pubchem, "fetch_safety", lambda lookup, display, sid=None, **k: {
        "query": display, "found": True, "source": "pubchem", "name_ko": display})
    r = mcp_server.get_substance_safety("자일렌")
    assert r["source"] == "pubchem" and "KOSHA 조회 실패" in r["note"]


def test_tool_live_everything_fails_uses_sample(monkeypatch):
    monkeypatch.setenv("CHEM_DATA_MODE", "live")

    def offline(*a, **k):
        raise httpx.ConnectError("offline")

    monkeypatch.setattr(pubchem, "fetch_safety", offline)
    r = mcp_server.get_substance_safety("톨루엔")
    assert r["source"] == "sample" and "ConnectError" in r["note"]
    r = mcp_server.get_substance_safety("자일렌")
    assert not r["found"] and "인터넷 연결" in r["reason"] and r["tags"] == ["organic-solvent"]


def test_kosha_errors_never_show_the_key(monkeypatch):
    monkeypatch.setenv("DATA_GO_KR_API_KEY", "SECRET-KEY-123")
    for handler in (lambda req: httpx.Response(500, text="oops"),
                    lambda req: (_ for _ in ()).throw(httpx.ConnectError("boom", request=req))):
        with pytest.raises(kosha.KoshaError) as info:
            kosha.fetch_kosha("톨루엔", "톨루엔", transport=httpx.MockTransport(handler),
                              cache=LookupCache(ttl_days=0))
        assert "SECRET" not in str(info.value)
        assert info.value.__cause__ is None