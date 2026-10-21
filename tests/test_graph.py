"""v2 그래프 전체 흐름 (fake LLM + 샘플 데이터) — 대표 시나리오가 늘 같은 결과를 내는지."""
import asyncio

import mcp_server
from agent.deps import SupervisorDeps
from agent.fake_llm import FastLLM, QualityLLM
from agent.graph import build_graph, decide_route
from conftest import InProcessMCP, events_of


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


def test_butyllithium_safety_notice_has_pyrophoric_caution(run_graph):
    s = run_graph("n-뷰틸리튬으로 합성할 거야")          # 샘플 모드: 조회 실패여도 이름 사전 태그로 주의
    assert [c["tag"] for c in s["notice"]["cautions"]] == ["pyrophoric", "water-reactive"]
    assert "합성 절차" in s["final"]


# ── 성질로 물질 찾기 ────────────────────────────────────────────────
def test_category_route_sample_mode(run_graph):
    s = run_graph("산화제에는 뭐가 있어?")
    assert s["route"] == "category" and s["notice"]["kind"] == "category"
    assert [c["cas"] for c in s["notice"]["confirmed"]] == ["7697-37-2"]       # 샘플의 질산만 H272 확인
    assert s["notice"]["confirmed"][0]["name"] == "질산"
    assert len(s["notice"]["unconfirmed"]) == 5
    assert "추측이므로 안내하지 않음" in s["final"]


def test_category_does_not_hijack_specific_question(run_graph):
    assert run_graph("톨루엔은 산화제야?")["route"] == "safety"


class _InventiveLLM(FastLLM):
    """실모델이 후보를 제안하는 상황 흉내 — 자연발화성이 아닌 물질과 이름을 잘못 붙인 물질을 섞어 낸다."""

    async def suggest(self, query, prop):
        return [{"name": "n-뷰틸리튬", "cas": "109-72-8"},
                {"name": "자연발화 액체(지어낸 이름)", "cas": "97-93-8"},   # CAS는 실제 트라이에틸알루미늄
                {"name": "물", "cas": "7732-18-5"}], self._usage(query, "")


def test_category_keeps_only_data_confirmed(monkeypatch):
    fake_sds = {
        "109-72-8": {"found": True, "source": "pubchem", "name_ko": "n-뷰틸리튬",
                     "hazard_statements": ["H250 공기에 노출되면 자연발화함", "H260 물과 접촉 시 …"]},
        "97-93-8": {"found": True, "source": "pubchem", "name_en": "Triethylaluminum",
                    "hazard_statements": ["H250 공기에 노출되면 자연발화함"]},
        "7732-18-5": {"found": True, "source": "pubchem", "name_en": "Water", "hazard_statements": []},
    }
    monkeypatch.setattr(mcp_server, "get_substance_safety",
                        lambda name, lookup="": {"query": name, **fake_sds[name]})
    deps = SupervisorDeps(_InventiveLLM("f", 0), QualityLLM("q", 0), InProcessMCP(), None, "v2")
    s = asyncio.run(build_graph(deps).ainvoke({"query": "자연발화하는 물질 뭐가 있어?", "thread_id": "t",
                                               "history": [], "events": [], "cost_usd": 0.0}))
    confirmed = {c["cas"]: c["name"] for c in s["notice"]["confirmed"]}
    assert confirmed == {"109-72-8": "n-뷰틸리튬", "97-93-8": "Triethylaluminum"}   # 이름은 자료에서
    assert [u["cas"] for u in s["notice"]["unconfirmed"]] == ["7732-18-5"]
    assert "지어낸 이름" not in s["final"]


# ── 경로 결정 (코드가 최종 결정) ─────────────────────────────────────
def test_decide_route():
    history = [{"role": "user", "content": "아세톤 주의사항 알려줘"}]
    assert decide_route("공기 중에 수증기를 흡수하는 물질은?", [], "chat") == "knowledge"
    assert decide_route("산과 염기를 섞으면 왜 열이 나?", [], "chat") == "knowledge"
    assert decide_route("흄후드는 언제 써야 해?", [], "chat") == "knowledge"
    assert decide_route("공기 중에 자연발화되는 것은?", [], "chat") == "category"
    assert decide_route("톨루엔 써도 돼?", [], "chat") == "safety"            # 물질 이름은 안전 우선
    assert decide_route("그럼 보관은 어떻게 해?", history, "chat") == "safety"   # 후속 질문은 물질 이어받기
    assert decide_route("산과 염기를 섞으면 왜 열이 나?", history, "safety") == "knowledge"
    assert decide_route("안녕하세요", history, "chat") == "chat"
    assert decide_route("오늘 날씨 어때", [], "chat") == "chat"


# ── 일반 화학 질문(knowledge) ──────────────────────────────────────
def test_knowledge_rag_fake_answers_from_retrieved_doc(run_graph):
    s = run_graph("공기 중에 수증기를 흡수하는 물질은?")
    assert s["route"] == "knowledge"
    retrieved = events_of(s, "KnowledgeRetrieved")[0]
    assert retrieved["titles"][0].startswith("흡습성과 조해성") and retrieved["embedder"] == "local-ngram-512"
    assert s["final"].startswith("[일반 화학 지식]") and "참고 문서 「흡습성과 조해성" in s["final"]
    assert s["notice"]["docs"][0]["doc"] == "knowledge/hygroscopic.md"
    assert [e["type"] for e in s["events"]][-4:] == ["KnowledgeRetrieved", "KnowledgeAnswered",
                                                     "KnowledgeReviewed", "Finalize"]


def test_knowledge_without_docs_says_unknown(run_graph):
    s = run_graph("고분자 결정화 속도론을 설명해 줘")       # 화학 질문이지만 지식 문서에 없음
    assert s["route"] == "knowledge" and events_of(s, "KnowledgeRetrieved")[0]["count"] == 0
    assert s["final"].startswith("알 수 없습니다") and s.get("notice") is None


class _KnowingLLM(QualityLLM):
    """실모델 흉내 — 1차 답에는 수치를 넣고(반려되어야 함), 2차에는 뺀다."""

    def __init__(self, keep_number=False):
        super().__init__("q", 0)
        self.calls = []
        self.keep_number = keep_number

    async def answer(self, query, history, feedback, docs=None):
        self.calls.append(feedback)
        text = "흡습성 물질은 공기 중의 수분을 끌어당깁니다. 수산화나트륨·염화칼슘이 대표적입니다."
        if not self.calls[1:] or self.keep_number:
            text += " 수산화나트륨은 20 °C에서도 빠르게 흡습합니다."
        return {"answer": text, "substances": [{"name": "수산화나트륨", "cas": "1310-73-2"},
                                               {"name": "염화칼슘", "cas": "10043-52-4"}]}, self._usage(query, text)


def _run_with(quality):
    deps = SupervisorDeps(FastLLM("f", 0), quality, InProcessMCP(), None, "v2")
    return asyncio.run(build_graph(deps).ainvoke({"query": "공기 중에 수증기를 흡수하는 물질은?",
                                                  "thread_id": "t", "history": [], "events": [],
                                                  "cost_usd": 0.0}))


def test_knowledge_review_retries_and_verifies_substances():
    llm = _KnowingLLM()
    s = _run_with(llm)
    assert len(llm.calls) == 2 and "20 °C" in llm.calls[1]            # 반려 사유가 2차 호출에 전달됨
    reviews = events_of(s, "KnowledgeReviewed")
    assert [r["ok"] for r in reviews] == [False, True]
    n = s["notice"]
    assert n["kind"] == "knowledge" and n["unverified"] == []
    found = {c["cas"]: c["found"] for c in n["substances"]}
    assert found == {"1310-73-2": True, "10043-52-4": False}           # 샘플에는 수산화나트륨만 있음
    assert n["substances"][0]["name"] == "수산화나트륨"
    assert "SDS로 확인한 내용이 아닙니다" in s["final"] and "SDS 확인 못 함" in s["final"]


def test_knowledge_keeps_warning_when_number_remains():
    s = _run_with(_KnowingLLM(keep_number=True))
    assert len(events_of(s, "KnowledgeReviewed")) == 2                 # 최대 2회 후 진행
    assert s["notice"]["unverified"] == ["20 °C"]
    assert "SDS로 확인하지 않은 값" in s["final"]