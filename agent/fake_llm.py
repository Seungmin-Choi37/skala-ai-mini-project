"""결정론적 Fake LLM 2종 (fast/quality) — LLM_BACKEND=fake(기본).

실제 LLM 호출 없이도 파이프라인 전체가 매번 똑같이 재현되도록, 입력에 따라 정해진
응답을 돌려준다. 원본(장애 대응)에서 재현하던 두 가지 연출을 그대로 유지한다.
- plan  : 물질이 1종이어도 analysis task를 끼워 넣는 '모델의 자기모순' → gate가 제거
- write : 1차 초안은 일부러 부실하게 → validate가 반려 → 2차에 형식을 갖춘 안내문

일반 화학 질문(answer)은 벡터 DB가 찾은 문서의 문장을 옮겨 답하고(지식을 지어내지 않음),
찾은 문서가 없으면 "알 수 없습니다"로 답한다.

모든 메서드는 실모델 백엔드(ollama/openai)와 똑같이 async다 — 그래프는 백엔드를 가리지 않고 await한다.
"""
import re

from langsmith import traceable

from . import chem_rules
from .prompts import UNKNOWN_REPLY, _research_task, intent_warnings, keyword_route


class FakeLLM:
    def __init__(self, name: str, usd_per_1k_tokens: float):
        self.name = name
        self.usd_per_1k = usd_per_1k_tokens

    def _usage(self, prompt: str, completion: str) -> dict:
        tokens = (len(prompt) + len(completion)) // 4 + 1
        return {"model": self.name, "tokens": tokens,
                "usd": round(tokens / 1000 * self.usd_per_1k, 6)}


class FastLLM(FakeLLM):
    """분류·계획 등 짧고 정형적인 단계 전담 (저비용)."""

    @traceable(run_type="llm", name="fast_llm.route")
    async def route(self, query: str) -> tuple[dict, dict]:
        result = {"route": keyword_route(query), "needs_analysis": False}
        return result, self._usage(query, str(result))

    @traceable(run_type="llm", name="fast_llm.plan")
    async def plan(self, query: str, procedure_prompt: str) -> tuple[dict, dict]:
        tasks = [_research_task(i, t["target"], t["label"], t["lookup"])
                 for i, t in enumerate(chem_rules.extract_targets(query))]
        tasks.append({"id": "task-analysis", "agent": "analysis",
                      "kind": "incompatibility", "status": "pending"})   # 1종이면 자기모순
        result = {"needs_analysis": False, "tasks": tasks}
        return result, self._usage(query + procedure_prompt, str(result))

    @traceable(run_type="llm", name="fast_llm.suggest")
    async def suggest(self, query: str, prop: str) -> tuple[list[dict], dict]:
        """성질로 물질 찾기 — fake는 코드에 정해 둔 기본 후보를 그대로 낸다."""
        seeds = [{"name": n, "cas": c} for n, c in chem_rules.PROPERTY_QUERIES[prop]["seeds"]]
        return seeds, self._usage(query, str(seeds))

    @traceable(run_type="llm", name="fast_llm.chat")
    async def chat(self, query: str, history: list[dict], on_token=None) -> tuple[str, dict]:
        """히스토리에서 이름을 기억해낸다 (세션 저장 증명용)."""
        reply = None
        if re.search(r"이름이 뭐", query):
            for msg in reversed(history):
                m = re.search(r"제 이름은 (\S+?)입니다", msg.get("content", ""))
                if m:
                    reply = f"{m.group(1)}님이라고 말씀하셨어요. 사용할 화학물질이 있으면 알려주세요."
                    break
        if reply is None:
            m = re.search(r"제 이름은 (\S+?)입니다", query)
            if m or re.search(r"안녕|반가|hello|hi\b", query, re.I):
                reply = (f"안녕하세요, {m.group(1)}님! " if m else "안녕하세요! ") + \
                    "사용 전에 확인하고 싶은 화학물질 이름을 알려주시면 주의사항을 정리해 드릴게요."
            elif re.search(r"고마|감사|thank", query, re.I):
                reply = "도움이 됐다니 다행입니다. 다른 물질도 확인이 필요하면 알려주세요."
            else:   # 모르는 말에 아는 척하지 않는다
                reply = (f"'{query.strip()}'에 대해서는 제가 알 수 없습니다. 이 서비스는 화학물질 사용 전 "
                         "안전 안내만 합니다. 화학물질이라면 정식 이름(한국어·영문)이나 CAS 번호로 "
                         "다시 물어봐 주세요.")
        if on_token is not None:
            on_token(reply)
        return reply, self._usage(query, reply)


class QualityLLM(FakeLLM):
    """최종 안내문 작성 전담 (고품질·고비용)."""

    @traceable(run_type="llm", name="quality_llm.write")
    async def write(self, query: str, collected: list[dict], attempt: int,
                    feedback: str | None, on_token=None) -> tuple[str, dict]:
        if attempt == 1:
            draft = "장갑 끼고 조심해서 사용하세요."
            if on_token is not None:
                on_token(draft)
            return draft, self._usage(query, draft)
        draft = compose_notice(collected)
        if on_token is not None:
            on_token(draft)
        return draft, self._usage(query + str(feedback), draft)

    @traceable(run_type="llm", name="quality_llm.answer")
    async def answer(self, query: str, history: list[dict], feedback: str | None,
                     docs: list[dict] | None = None) -> tuple[dict, dict]:
        """일반 화학 질문 — fake에는 화학 지식이 없으므로, 벡터 DB가 찾아 준 문서의 문장만 옮겨 답한다.
        찾은 문서가 없으면 지어내지 않고 모른다고 답한다."""
        if not docs:
            text = (UNKNOWN_REPLY.split(".")[0] + " — 관련 문서를 찾지 못했고, fake 모드에는 LLM이 없어 일반 "
                    "지식으로 답하지 않습니다. 실제 AI 모드(OpenAI·Ollama)로 켜면 답합니다. 특정 물질의 사용 전 "
                    "안전 안내는 물질 이름이나 CAS 번호로 물어보세요.")
            return {"answer": text, "substances": []}, self._usage(query, text)
        top = docs[0]
        sentences = re.split(r"(?<=[.다])\s+", top["text"].replace("\n", " "))
        text = f"참고 문서 「{top['title']}」에 따르면 — " + " ".join(sentences[:4]).strip()
        if len(docs) > 1:
            text += "\n함께 볼 문서: " + ", ".join(f"「{d['title']}」" for d in docs[1:])
        return {"answer": text, "substances": []}, self._usage(query, text)


def _guide_lines(guides: list[str], prefix: str) -> list[str]:
    lines = []
    for g in guides:
        for line in g.splitlines():
            if line.startswith(prefix):
                lines.append(line[len(prefix):].strip())
    return list(dict.fromkeys(lines))


def _guide_steps(guides: list[str], limit: int = 3) -> list[str]:
    steps = []
    for g in guides:
        steps += [m.group(1) for line in g.splitlines() if (m := re.match(r"^\d+\.\s+(.*)", line))][:2]
    return list(dict.fromkeys(steps))[:limit]


def compose_notice(collected: list[dict]) -> str:
    """수집된 사실만으로 안내문을 조립한다 (fake 백엔드의 '정답' 형식)."""
    intent = next((c["data"] for c in collected if c["source"] == "intent"), {})
    sds = [c["data"] for c in collected if c["source"] == "sds"]
    guides = [c["data"] for c in collected if c["source"] == "guide"]
    analysis = next((c["data"] for c in collected if c["source"] == "analysis"), None)
    ok = [s for s in sds if s.get("found")]
    missing = [s for s in sds if not s.get("found")]

    out = intent_warnings(intent, sds)

    if sds and not ok:   # 조회한 물질을 하나도 확인하지 못함 — 모른다고 짧게 답하고 되묻는다
        out.append("[확인 불가] " + " · ".join(s.get("query", "?") for s in sds))
        out += [f"- {s.get('query')}: {s.get('reason')}" for s in missing]
        # 자료는 못 찾았어도 이름만으로 아는 위험 조합(락스+암모니아 등)은 반드시 알린다
        for c in chem_rules.check_incompatibility(sds):
            out.append(f"- 같이 쓸 때 위험(이름으로 판정): {' + '.join(c['pair'])} — {c['warning']}. "
                       f"{c['advice']}.")
        out.append("- 확인되지 않은 물질에 대해 유해성·취급법을 추측해서 안내하지 않습니다. "
                   "사용 전에 반드시 공식 SDS(KOSHA MSDS·제조사 SDS)를 확인하세요.")
        return "\n".join(out)

    if not sds:
        out += ["[사용 전 안전 안내]",
                "1) 유해성 요약 — 질문에서 물질 이름을 찾지 못했습니다. 물질명(한국어·영문·CAS 번호)을 "
                "알려주시면 SDS를 조회해 안내문을 작성합니다.",
                "2) 보호구 — " + "; ".join(_guide_lines(guides, "보호구:")),
                "3) 취급 주의 — 처음 쓰는 물질은 SDS를 먼저 확인하세요.",
                "4) 혼용 주의 — 물질이 확인되지 않아 점검하지 못했습니다.",
                "5) 응급조치 — SDS의 응급조치 항목을 따르고, 필요하면 119에 연락하세요.",
                "6) 보관·폐기 — " + "; ".join(_guide_lines(guides, "보관:")),
                "출처: 일반 취급 지침(data/safety_guides) — 공식 SDS 확인 필요"]
        return "\n".join(out)

    out.append("[사용 전 안전 안내] " + " · ".join(chem_rules.display_name(s) for s in sds))

    hz = []
    for s in ok:
        flash = f", 인화점 {s['flash_point']}" if s.get("flash_point") else ""
        pics = "·".join(p.split()[0] if p.startswith("GHS") else p
                        for p in s.get("pictograms", [])) or "정보 없음"
        statements = "; ".join(s.get("hazard_statements", [])[:4]) or "GHS 유해·위험 문구 정보 없음"
        hz.append(f"{chem_rules.display_name(s)}: {s.get('signal_word') or '신호어 정보 없음'}, "
                  f"그림문자 {pics}{flash} — {statements}")
    for s in missing:
        hz.append(f"{s.get('query')}: 정보를 확인하지 못함 — 공식 SDS 확인 필요 ({s.get('reason')})")
    out.append("1) 유해성 요약 — " + " / ".join(hz))

    out.append("2) 보호구 — " + "; ".join(chem_rules.ppe_for(ok)))

    handling = [f"{chem_rules.display_name(s)}: {s['handling']}" for s in ok if s.get("handling")]
    if len(handling) < len(ok):   # PubChem에 취급 항목이 없으면 유형별 지침으로 보완
        handling += [f"공통: {step}" for step in _guide_steps(guides)]
    handling += [f"{c['name']} 단독 주의: {c['text']}" for c in chem_rules.solo_cautions(sds)]
    out.append("3) 취급 주의 — " + (" / ".join(handling) or "공식 SDS 7항(취급 및 저장)을 확인하세요."))

    if len(sds) < 2:
        out.append("4) 혼용 주의 — 단일 물질 사용(다른 물질과 섞어 쓰면 그 물질까지 함께 확인하세요)")
    else:
        out.append("4) 혼용 주의 — " + (analysis or "확인된 혼촉 위험 없음"))

    fa = []
    for s in ok:
        items = "; ".join(f"{k}: {v}" for k, v in s.get("first_aid", {}).items())
        fa.append(f"[{chem_rules.display_name(s)}] {items or '공식 SDS 4항(응급조치) 확인'}")
    out.append("5) 응급조치 — " + " ".join(fa or ["SDS의 응급조치 항목을 따르고, 필요하면 119에 연락하세요."]))

    storage = [f"{chem_rules.display_name(s)}: {s['storage']}" for s in ok if s.get("storage")]
    storage += _guide_lines(guides, "보관:") if len(storage) < len(ok) else []
    disposal = _guide_lines(guides, "폐기:")
    out.append("6) 보관·폐기 — " + (" / ".join(storage) or "공식 SDS 7항 확인") +
               (f" / 폐기: {' '.join(disposal)}" if disposal else ""))

    src = []
    if any(s.get("source") == "sample" for s in ok):
        src.append("샘플 데이터(data/substances.json, 교육용 요약)")
    if any(s.get("source") == "kosha" for s in ok):
        src.append("안전보건공단 MSDS(msds.kosha.or.kr)")
    src += [s["source_url"] for s in ok if s.get("source_url") and s.get("source") != "kosha"]
    src += ["취급 지침(data/safety_guides)"] if guides else []
    out.append("출처: " + ", ".join(src or ["(조회된 자료 없음)"]) +
               " — 실제 사용 전 공식 SDS(KOSHA MSDS·제조사 SDS)로 반드시 확인하세요.")
    return "\n".join(out)