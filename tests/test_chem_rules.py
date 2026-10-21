"""chem_rules — 물질 인식·의도·한국어 문구·태그·혼촉 규칙 (코드가 내리는 안전 판단)."""
from agent import chem_rules as cr

S = cr.SUBSTANCES


def _stub(name, tags, classes=()):
    """조회에 실패했거나 이름 사전에만 있는 물질 흉내."""
    return {"query": name, "name_ko": name, "found": False, "tags": list(tags),
            "hazard_classes": list(classes)}


# ── 물질 인식 ────────────────────────────────────────────────────────
def test_resolve_korean_english_cas():
    assert cr.resolve("톨루엔") == "toluene"
    assert cr.resolve("Toluene") == "toluene"
    assert cr.resolve("108-88-3") == "toluene"
    assert cr.resolve("니트로 글리세린") == "nitroglycerin"   # 한글 사이 공백
    assert cr.resolve("없는물질") is None


def test_find_substances_order_and_longest_match():
    assert cr.find_substances("톨루엔이랑 질산 같이 써도 돼?") == ["toluene", "nitric_acid"]
    assert cr.find_substances("질산암모늄 보관법") == ["ammonium_nitrate"]   # '질산'으로 잘리지 않음


def test_silver_nitrate_vs_particle():
    assert cr.find_substances("질산은 위험해?") == ["nitric_acid"]       # 질산 + 조사 '은'
    assert cr.find_substances("질산은 용액 취급법") == ["silver_nitrate"]


def test_extract_targets_cas_and_unknown_korean_name():
    targets = cr.extract_targets("CAS 1330-20-7 주의사항 알려줘")
    assert targets[0]["target"] == "xylene"
    unknown = cr.extract_targets("아니솔을 써도 돼?")
    assert unknown and unknown[0]["target"] == "아니솔" and unknown[0]["lookup"] is None


def test_abbreviation_is_not_guessed():
    targets = cr.extract_targets("DMA 써도 돼?")
    assert targets == [{"target": "DMA", "label": "DMA", "lookup": None}]
    assert len(cr.abbr_candidates("DMA")) == 2
    assert cr.is_abbreviation("XYZQ")
    assert not cr.is_abbreviation("THF")   # 이름 사전에 있는 약어


def test_detect_intent():
    assert cr.detect_intent("메탄올 마셔도 돼?") == {"ingest": True, "synthesis": False}
    assert cr.detect_intent("니트로글리세린 만들고 싶어") == {"ingest": False, "synthesis": True}


# ── 한국어 유해·위험 문구 ────────────────────────────────────────────
def test_korean_hazard_single_and_percentage():
    assert cr.korean_hazard("H225 (100%): Highly Flammable liquid and vapor [Danger]") == \
        "H225 고인화성 액체 및 증기"


def test_korean_hazard_combined_and_suffix():
    assert cr.korean_hazard("H302+H312+H332: Harmful") == \
        "H302+H312+H332 삼키면 유해함 · 피부와 접촉하면 유해함 · 흡입하면 유해함"
    assert cr.korean_hazard("H361d: Suspected of damaging the unborn child").startswith("H361d 태아")


def test_korean_hazard_unknown_code_keeps_original():
    assert cr.korean_hazard("H999: Something new") == "H999: Something new"
    assert cr.korean_hazard("No code here") == "No code here"


def test_h_table_has_common_codes():
    for code in ("H225", "H290", "H300", "H314", "H319", "H335", "H350", "H372", "H410", "EUH019"):
        assert code in cr.H_STATEMENTS_KO


# ── 태그 추론 ────────────────────────────────────────────────────────
def test_tags_from_reactive_groups():
    tags = cr.tags_from_reactive_groups(["Acids, Strong Oxidizing"])
    assert {"acid", "oxidizing-acid"} <= set(tags)
    assert "peroxide-former" in cr.tags_from_reactive_groups(["Ethers"])
    assert {"alkali-metal", "water-reactive"} <= set(
        cr.tags_from_reactive_groups(["Metals, Alkali, Very Active"]))
    assert "aqueous" in cr.tags_from_reactive_groups(["Water and Aqueous Solutions"])
    assert cr.tags_from_reactive_groups(["Something Unknown"]) == []


def test_tags_from_hazard_statements():
    assert cr.tags_from_hazard_statements(["EUH019: May form explosive peroxides"]) == ["peroxide-former"]
    assert "water-reactive" in cr.tags_from_hazard_statements(["H260: In contact with water ..."])


def test_hazard_classes_from_codes():
    classes = cr.classes_from_hazard_statements(["H225 x", "H314 y", "H300 z"])
    assert classes == ["flammable", "corrosive", "toxic"]


# ── 혼촉 규칙 ────────────────────────────────────────────────────────
def _rules(conflicts):
    return {c["rule"] for c in conflicts}


def test_oxidizing_acid_with_solvent():
    rules = _rules(cr.check_incompatibility([S["toluene"], S["nitric_acid"]]))
    assert {"oxidizer-flammable", "oxidizing-acid-organic"} <= rules
    assert "oxidizer-organic" not in rules   # 더 구체적인 규칙이 있으면 일반 규칙은 뺀다


def test_bleach_and_acid_even_when_lookup_failed():
    bleach = _stub("락스", ["hypochlorite", "aqueous"])
    assert "hypochlorite-acid" in _rules(cr.check_incompatibility([bleach, S["hydrochloric_acid"]]))


def test_sodium_with_acid_and_halogenated_solvent():
    sodium = _stub("금속 나트륨", ["water-reactive", "alkali-metal"])
    chloroform = _stub("클로로포름", ["organic-solvent", "halogenated"])
    rules = _rules(cr.check_incompatibility([sodium, S["hydrochloric_acid"], chloroform]))
    assert "water-reactive-acid" in rules
    assert "water-reactive-aqueous" not in rules   # 산 규칙이 이미 걸려 중복 제거
    assert "alkali-halogenated" in rules


def test_sodium_with_aqueous_only():
    sodium = _stub("금속 나트륨", ["water-reactive", "alkali-metal"])
    ammonia = _stub("암모니아수", ["base", "ammonia", "aqueous"])
    assert "water-reactive-aqueous" in _rules(cr.check_incompatibility([sodium, ammonia]))


def test_peroxide_ketone_and_silver_ammonia():
    h2o2 = _stub("과산화수소", ["oxidizer", "peroxide", "aqueous"])
    assert "peroxide-ketone" in _rules(cr.check_incompatibility([h2o2, S["acetone"]]))
    silver = _stub("질산은", ["oxidizer", "silver"])
    ammonia = _stub("암모니아수", ["base", "ammonia", "aqueous"])
    assert "silver-ammonia" in _rules(cr.check_incompatibility([silver, ammonia]))


def test_oxidizer_with_non_flammable_organic():
    kmno4 = _stub("과망가니즈산칼륨", ["oxidizer"])
    glycerol = _stub("글리세롤", ["organic"])
    assert _rules(cr.check_incompatibility([kmno4, glycerol])) == {"oxidizer-organic"}


def test_acid_base_gets_soft_advice():
    conflicts = cr.check_incompatibility([S["hydrochloric_acid"], S["sodium_hydroxide"]])
    assert [c["rule"] for c in conflicts] == ["acid-base"]
    assert conflicts[0]["advice"] != cr.KEEP_APART


def test_no_conflict_between_two_solvents():
    assert cr.check_incompatibility([S["toluene"], S["acetone"]]) == []


def test_catalog_tags_are_known():
    known = {"acid", "oxidizing-acid", "base", "ammonia", "hypochlorite", "cyanide", "organic-solvent",
             "organic", "halogenated", "ketone", "oxidizer", "peroxide", "peroxide-former", "reducer",
             "water-reactive", "alkali-metal", "aqueous", "silver", "explosive"}
    for sid, entry in cr.CATALOG.items():
        assert set(entry.get("tags", [])) <= known, sid


# ── 단독 주의·보호구·그림문자 ─────────────────────────────────────────
def test_solo_cautions():
    ether = {"name_ko": "다이에틸에테르", "found": True, "tags": ["organic-solvent", "peroxide-former"]}
    cautions = cr.solo_cautions([ether, S["toluene"]])
    assert [(c["name"], c["tag"]) for c in cautions] == [("다이에틸에테르", "peroxide-former")]


def test_ppe_and_pictograms():
    ppe = cr.ppe_for([{**S["nitric_acid"], "found": True}])
    assert ppe[0].startswith("공통:") and any(p.startswith("부식성") for p in ppe)
    assert cr.pictogram_codes(["GHS02 인화성", "Flammable", "Corrosive"]) == ["GHS02", "GHS05"]