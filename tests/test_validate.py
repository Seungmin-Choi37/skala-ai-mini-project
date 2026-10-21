"""validate 검수 기준 — 형식·필수 문구, 그리고 근거 없는 수치·H코드(모델이 지어낸 값) 차단."""
from agent import chem_rules as cr
from agent.fake_llm import compose_notice
from agent.graph import check_draft, ungrounded

TOLUENE = {**cr.SUBSTANCES["toluene"], "query": "톨루엔", "found": True, "source": "sample"}


def _collected(*sds, intent=None, analysis=None):
    out = [{"source": "intent", "data": intent or {"ingest": False, "synthesis": False}}]
    out += [{"source": "sds", "data": s} for s in sds]
    if analysis:
        out.append({"source": "analysis", "data": analysis})
    return out


def test_compose_notice_passes_its_own_check():
    draft = compose_notice(_collected(TOLUENE))
    assert check_draft(draft, [TOLUENE]) == []
    assert "약 4 °C" in draft          # 자료에 있는 인화점은 그대로 써도 된다


def test_invented_flash_point_is_rejected():
    draft = compose_notice(_collected(TOLUENE)).replace("약 4 °C", "약 12 °C")
    problems = check_draft(draft, [TOLUENE])
    assert any("12 °C" in p and "수치" in p for p in problems)


def test_invented_h_code_is_rejected():
    draft = compose_notice(_collected(TOLUENE)) + "\nH350 암을 일으킬 수 있음"
    problems = check_draft(draft, [TOLUENE])
    assert any("H350" in p for p in problems)


def test_exposure_limit_without_source_is_rejected():
    numbers, codes = ungrounded("노출기준 50 ppm, 농도 30% 이하로 희석", "근거 자료에는 숫자가 없음")
    assert numbers == ["50 ppm", "30%"] and codes == []


def test_plain_numbers_are_not_checked():
    # 섹션 번호, 119·109, '15분' 같은 숫자는 단위가 붙은 수치가 아니므로 검사 대상이 아니다
    numbers, codes = ungrounded("1) 유해성 2) 보호구 — 119에 연락, 물로 15분 이상 씻기", "")
    assert numbers == [] and codes == []


def test_minus_sign_variants_match():
    assert ungrounded("인화점 −20 °C", '"flash_point": "약 -20 °C"') == ([], [])
    assert ungrounded("인화점 20 °C", '"flash_point": "약 -20 °C"')[0] == ["20 °C"]


def test_evidence_from_guides_counts():
    assert ungrounded("물로 15분, 70% 에탄올", "지침: 70% 에탄올로 닦는다") == ([], [])


def test_short_draft_rejected_for_format():
    problems = check_draft("장갑 끼고 조심해서 사용하세요.", [TOLUENE])
    assert any("번호 형식" in p for p in problems) and any("출처" in p for p in problems)


def test_ingest_requires_119():
    intent = {"ingest": True, "synthesis": False}
    draft = compose_notice(_collected(TOLUENE)).replace("119", "")
    assert any("119" in p for p in check_draft(draft, [TOLUENE], intent))
    good = compose_notice(_collected(TOLUENE, intent=intent))
    assert check_draft(good, [TOLUENE], intent) == []


def test_all_unknown_short_answer():
    unknown = {"query": "DMA", "found": False, "reason": "약어라 확정할 수 없음"}
    draft = compose_notice(_collected(unknown))
    assert draft.startswith("[확인 불가] DMA")
    assert check_draft(draft, [unknown]) == []


def test_all_unknown_must_still_mention_known_mix_danger():
    bleach = {"query": "락스", "found": False, "reason": "샘플에 없음", "tags": ["hypochlorite"]}
    ammonia = {"query": "암모니아수", "found": False, "reason": "샘플에 없음", "tags": ["base", "ammonia"]}
    short = "[확인 불가] 락스 · 암모니아수\n공식 SDS를 확인하세요."
    assert any("혼촉" in p for p in check_draft(short, [bleach, ammonia]))
    assert check_draft(compose_notice(_collected(bleach, ammonia)), [bleach, ammonia]) == []