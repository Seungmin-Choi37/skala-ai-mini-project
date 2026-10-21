"""ollama_llm.py·openai_llm.py가 함께 쓰는 프롬프트와 코드 레벨 보정 함수.

원본에서는 두 파일에 같은 프롬프트·정규화 코드가 복붙돼 있었다. 도메인을 바꾸면서
한곳으로 모아, 프롬프트를 고칠 때 한 파일만 수정하면 되게 했다.
"""
import re

from . import chem_rules

# route 실패 시 코드 fallback, fake 백엔드, 안전 우선 override가 같이 쓰는 키워드
SAFETY_KEYWORDS = re.compile(
    r"주의|안전|위험|유해|독성|취급|보관|폐기|보호구|응급|혼합|섞|혼촉|사용 전|MSDS|SDS|GHS",
    re.I)


def keyword_route(query: str) -> str:
    """질문에 안전 키워드가 있거나, 물질 이름이 보이면(사전 등록 여부와 무관) safety."""
    if SAFETY_KEYWORDS.search(query) or chem_rules.extract_targets(query):
        return "safety"
    return "chat"


ROUTE_SYSTEM = (
    "당신은 화학 실험실 안전 지원 라우터다. 사용자 요청을 분류하라. "
    "화학물질 이름이 나오거나, 화학물질의 위험성·취급·보관·폐기·보호구·응급조치·혼합 가능 "
    "여부를 묻거나, 화학물질을 사용·합성·섭취하려 한다고 말하면 route=safety, "
    "인사·잡담·일반 질문이면 route=chat. "
    "needs_analysis는 물질이 2종 이상이라 혼촉 점검이 필요할 때만 true."
)
ROUTE_JSON_HINT = ('JSON 객체로만 응답하라. 스키마: '
                   '{"route": "chat" 또는 "safety", "needs_analysis": true 또는 false}')


def plan_system() -> str:
    known = ", ".join(f"{s['name_ko']}({s['name_en']})" for s in chem_rules.SUBSTANCES.values())
    return (
        "당신은 화학안전 Supervisor의 planning 노드다. 표준 절차에 따라 실행 계획(tasks)을 "
        "JSON으로 세워라. 사용자 요청에 등장한 화학물질마다 agent=research task를 하나씩 세워라. "
        "target에는 사용자가 쓴 물질명을 그대로, target_en에는 그 물질의 영문 IUPAC/일반명 또는 "
        "CAS 번호를 넣어라(PubChem 조회용, 모르면 비워라). "
        f"(샘플 등록 물질 예: {known} — 등록되지 않은 물질도 넣어라.) "
        "물질이 2종 이상이면 agent=analysis task를 추가하라."
    )


PLAN_JSON_HINT = ('JSON 객체로만 응답하라. 스키마: {"needs_analysis": boolean, '
                  '"tasks": [{"agent": "research"|"analysis", "target": string(선택), '
                  '"target_en": string(선택)}]}')

CHAT_SYSTEM = ("당신은 화학 실험실 안전 도우미다. 한국어로 한두 문장으로 간결하게 답하라. "
               "대화 이력에서 사용자가 밝힌 이름이 있으면 정확히 기억해 답하라. "
               "모르는 단어·약어·질문에는 추측하지 말고 '알 수 없다'고 말한 뒤 정식 이름이나 "
               "CAS 번호로 다시 물어봐 달라고 하라. "
               "화학물질 안전 정보는 추측해서 말하지 말고, 물질명을 알려주면 안내문을 "
               "작성해 준다고 안내하라.")


def chat_user(query: str, history: list[dict]) -> str:
    rendered = "\n".join(f"{m['role']}: {m['content']}" for m in history[-10:])
    return f"## 지금까지의 대화\n{rendered or '(없음)'}\n\n## 사용자의 새 메시지\n{query}"


def write_user(query: str, collected: list[dict], feedback: str | None) -> str:
    user = f"## 사용자 요청\n{query}\n\n## 수집된 사실\n{render_facts(collected)}"
    if feedback:
        user += f"\n\n## 이전 초안에 대한 검수 반려 사유 (반드시 반영)\n{feedback}"
    return user


WRITE_SYSTEM = (
    "당신은 화학 실험실 안전 담당자다. '수집된 사실'에 있는 정보만 사용해 한국어로 "
    "사용 전 안전 안내문을 작성하라. 수집된 사실에 없는 수치(인화점, 노출기준, 농도 등)나 "
    "H코드·유해 문구는 절대 지어내지 마라 — 단위가 붙은 수치(°C·%·ppm 등)와 H코드는 검수 코드가 "
    "수집된 사실과 대조해 없으면 반려한다. 합성 경로·반응 조건·시약 비율·제조 절차는 어떤 경우에도 "
    "쓰지 마라. 반드시 아래 형식을 지켜라.\n"
    "(의도 경고가 있으면 맨 앞에 '⚠️ ' 로 시작하는 경고 문단)\n"
    "[사용 전 안전 안내] 물질명들\n"
    "1) 유해성 요약 — 물질별 신호어, 그림문자, 주요 유해·위험 문구\n"
    "2) 보호구 — 필요한 보호구\n"
    "3) 취급 주의 — 물질별 취급 방법('단독 주의'가 있으면 여기에 반드시 쓴다)\n"
    "4) 혼용 주의 — 혼촉 점검 결과(없으면 '확인된 혼촉 위험 없음')\n"
    "5) 응급조치 — 흡입·피부·눈·섭취\n"
    "6) 보관·폐기 — 보관 및 폐기 방법\n"
    "출처: 데이터 출처\n"
    "조회에 실패한 물질은 '정보를 확인하지 못함 — 공식 SDS 확인 필요'라고 쓰고 실패 이유를 덧붙여라.\n"
    "모든 물질이 조회에 실패했다면 1)~6) 형식을 쓰지 말고 '[확인 불가] 물질명'과 실패 이유, "
    "정식 이름·CAS 번호를 다시 요청하는 문장, '공식 SDS' 확인 안내만 짧게 써라. 약어의 뜻을 추측하지 마라. "
    "단, 혼촉 점검(코드 판정)에 위험이 있으면 그 조합은 섞거나 함께 보관하지 말라고 반드시 쓴다.\n"
    "의도 경고 규칙: 섭취 의도면 '마시거나 먹으면 안 된다, 이미 삼켰다면 구토를 유도하지 말고 즉시 "
    "119에 연락, 스스로를 해치려는 생각이 든다면 자살예방상담전화 109(24시간)'를 반드시 쓴다. "
    "합성 의도면 '이 서비스는 합성 절차를 안내하지 않으며 아래는 물질 취급 안전 정보'라고 쓴다. "
    "폭발성 물질이면 허가 없는 제조가 법으로 금지된다는 점도 쓴다."
)


def intent_warnings(intent: dict, sds_list: list[dict]) -> list[str]:
    """의도별 경고 문단 — fake 안내문과 검수 기준이 같이 쓴다."""
    names = " · ".join(chem_rules.display_name(s) for s in sds_list) or "화학물질"
    out = []
    if intent.get("ingest"):
        out.append(f"⚠️ 섭취 금지 — {names}은(는) 마시거나 먹으면 안 됩니다. 이미 삼켰다면 구토를 "
                   "유도하지 말고 즉시 119에 연락하세요. 혹시 스스로를 해치려는 생각이 든다면 "
                   "자살예방상담전화 109(24시간)에서 이야기를 들어줍니다.")
    if intent.get("synthesis"):
        msg = ("⚠️ 이 서비스는 합성 절차·반응 조건을 안내하지 않습니다. 아래는 해당 물질을 다룰 때의 "
               "안전 정보입니다. 합성은 지도교수·안전관리자의 승인과 검증된 절차서에 따라 진행하세요.")
        if chem_rules.is_explosive(sds_list):
            msg += (" 특히 폭발성 물질은 관련 법령(총포·도검·화약류 등의 안전관리에 관한 법률 등)에 따라 "
                    "허가 없이 제조할 수 없습니다.")
        out.append(msg)
    return out


def render_facts(collected: list[dict]) -> str:
    facts = []
    for c in collected:
        d = c["data"]
        if c["source"] == "intent":
            flags = [k for k, v in (("섭취 의도", d.get("ingest")), ("합성 의도", d.get("synthesis"))) if v]
            facts.append(f"- [의도] {', '.join(flags) or '일반 사용'}")
        elif c["source"] == "sds":
            if not d.get("found"):
                facts.append(f"- [SDS 조회 실패] {d.get('query')}: {d.get('reason')}")
                for caution in chem_rules.solo_cautions([d]):
                    facts.append(f"  단독 주의(코드 판정): {caution['text']}")
                continue
            fa = "; ".join(f"{k}: {v}" for k, v in d.get("first_aid", {}).items())
            facts.append(
                f"- [SDS] {chem_rules.display_name(d)} (영문 {d.get('name_en')}, "
                f"CAS {d.get('cas')}, 출처 {d.get('source')} {d.get('source_url') or ''})\n"
                f"  신호어: {d.get('signal_word')} / 그림문자: {', '.join(d.get('pictograms', []))}\n"
                f"  유해·위험 문구: {' | '.join(d.get('hazard_statements', []))}\n"
                f"  성질 태그: {', '.join(d.get('tags', []))} / 인화점: {d.get('flash_point')}\n"
                f"  응급조치: {fa}\n"
                f"  취급: {d.get('handling')}\n  보관: {d.get('storage')}")
            for caution in chem_rules.solo_cautions([d]):
                facts.append(f"  단독 주의(코드 판정): {caution['text']}")
        elif c["source"] == "guide":
            facts.append(f"- [취급 지침]\n{d.strip()}")
        elif c["source"] == "analysis":
            facts.append(f"- [혼촉 점검(코드 판정)] {d.strip()}")
    return "\n".join(facts) or "(수집된 사실 없음)"


def _research_task(i: int, target: str, label: str, lookup: str | None) -> dict:
    return {"id": f"task-{i}", "agent": "research", "kind": "sds", "target": target,
            "label": label, "lookup": lookup or "", "status": "pending"}


def normalize_plan(raw: dict, query: str) -> dict:
    """모델이 세운 계획을 실행 가능한 형태로 보정한다(모델 출력을 그대로 믿지 않는다).
    - 카탈로그에 있는 물질은 표준 키로 바꾼다
    - 쿼리에 등장하지 않는 target은 버린다(모델이 지어낸 물질 차단)
    - 코드가 쿼리에서 찾은 물질을 모델이 빠뜨렸다면 복구한다"""
    picked: list[tuple[str, str, str | None]] = []
    wants_analysis = False
    squashed = query.replace(" ", "").lower()
    for t in raw.get("tasks", []) or []:
        agent = t.get("agent")
        if agent == "research":
            target = (t.get("target") or "").strip()
            target_en = (t.get("target_en") or "").strip()
            sid = chem_rules.resolve(target) or chem_rules.resolve(target_en)
            if sid:
                s = chem_rules.CATALOG[sid]
                picked.append((sid, chem_rules.label_of(sid), s.get("cas") or s.get("name_en")))
            elif target and target.replace(" ", "").lower() in squashed:
                ascii_en = target_en if re.fullmatch(r"[A-Za-z0-9 ,()\-]+", target_en) else None
                if chem_rules.is_abbreviation(target) or target.upper() in chem_rules.AMBIGUOUS_ABBR:
                    ascii_en = None   # 약어를 모델이 임의로 풀어 쓴 이름은 믿지 않는다
                picked.append((target, target, ascii_en))
        elif agent == "analysis":
            wants_analysis = True
    for t in chem_rules.extract_targets(query):
        picked.append((t["target"], t["label"], t["lookup"]))

    tasks, seen = [], set()
    for target, label, lookup in picked:
        if target.lower() in seen:
            continue
        seen.add(target.lower())
        tasks.append(_research_task(len(tasks), target, label, lookup))
    if wants_analysis:
        tasks.append({"id": "task-analysis", "agent": "analysis",
                      "kind": "incompatibility", "status": "pending"})
    return {"needs_analysis": bool(raw.get("needs_analysis", False)), "tasks": tasks}