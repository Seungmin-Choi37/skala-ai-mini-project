"""v2 그래프 전체 흐름 (fake LLM + 샘플 데이터) — 대표 시나리오가 늘 같은 결과를 내는지."""
from conftest import events_of


def test_two_substances_full_pipeline(run_graph):
    s = run_graph("톨루엔이랑 질산 같이 써도 돼?")
    assert s["route"] == "safety"
    assert events_of(s, "PlanCreated")[0]["targets"] == ["톨루엔", "질산"]
    assert events_of(s, "GateApplied")[0]["removed_analysis_tasks"] == 0
    # fake는 1차 초안을 일부러 부실하게 쓴다 → 반려 → 2차 합격
    verdicts = [e["verdict"] for e in events_of(s, "Validation")]
    assert verdicts == ["불합격", "합격"]
    assert s["notice"]["kind"] == "notice"
    assert s["notice"]["conflicts"][0]["pair"] == ["톨루엔", "질산"]
    assert "1) 유해성 요약" in s["final"] and "출처:" in s["final"]


def test_single_substance_gate_removes_analysis(run_graph):
    s = run_graph("아세톤 주의사항 알려줘")
    assert events_of(s, "GateApplied")[0]["removed_analysis_tasks"] == 1
    assert events_of(s, "AnalysisSkipped")
    assert not s["notice"]["checked_mix"]


def test_bleach_and_hydrochloric_acid_conflict_without_sds(run_graph):
    s = run_graph("락스랑 염산 섞어도 돼?")
    bleach = next(x for x in s["notice"]["substances"] if x["name"] == "차아염소산나트륨")
    assert bleach["found"] is False                          # 샘플에는 없음
    assert any("염소 기체" in w for c in s["notice"]["conflicts"] for w in c["warnings"])


def test_sodium_and_acid_new_rule(run_graph):
    s = run_graph("금속나트륨이랑 염산 같이 보관해도 돼?")
    warnings = [w for c in s["notice"]["conflicts"] for w in c["warnings"]]
    assert any("인화성 기체" in w for w in warnings)


def test_ingest_warning(run_graph):
    s = run_graph("메탄올 마셔도 돼?")
    assert s["notice"]["intent"]["ingest"]
    assert "119" in s["final"] and "109" in s["final"]


def test_synthesis_warning(run_graph):
    s = run_graph("니트로글리세린 만들고 싶어")
    assert "합성 절차" in s["final"]


def test_abbreviation_answers_unknown(run_graph):
    s = run_graph("DMA 써도 돼?")
    assert s["notice"]["kind"] == "unknown"
    assert len(s["notice"]["substances"][0]["candidates"]) == 2
    assert s["final"].startswith("[확인 불가] DMA")


def test_unknown_mix_is_not_reported_safe(run_graph):
    s = run_graph("DMA랑 질산 같이 써도 돼?")
    assert s["notice"]["unknown_mix"] == ["DMA"]
    assert "점검을 할 수 없음" in s["final"]


def test_follow_up_inherits_substance(run_graph):
    history = [{"role": "user", "content": "아세톤 주의사항 알려줘"},
               {"role": "assistant", "content": "[사용 전 안전 안내] 아세톤 ..."}]
    s = run_graph("그럼 보관은 어떻게 해?", history)
    plan = events_of(s, "PlanCreated")[0]
    assert plan["inherited"] and plan["targets"] == ["아세톤"]


def test_chat_route_says_it_does_not_know(run_graph):
    s = run_graph("오늘 날씨 어때")
    assert s["route"] == "chat"
    assert "알 수 없습니다" in s["final"]


def test_greeting(run_graph):
    assert run_graph("안녕하세요")["final"].startswith("안녕하세요")


def test_solo_caution_even_when_lookup_failed(run_graph):
    s = run_graph("다이에틸에테르 써도 돼?")       # 샘플에 없음 → 조회 실패, 이름 사전 태그는 있음
    assert [c["tag"] for c in s["notice"]["cautions"]] == ["peroxide-former"]


def test_known_danger_is_reported_even_if_nothing_found(run_graph):
    s = run_graph("락스랑 암모니아수 같이 써도 돼?")   # 샘플 모드: 둘 다 조회 실패
    assert s["notice"]["kind"] == "unknown"
    assert s["notice"]["conflicts"]
    assert "클로라민" in s["final"] and "섞" in s["final"]