"""Tier 0 스키마 계약에 맞춘 Track C mock LangGraph."""

from __future__ import annotations

from typing import Any

from langgraph.graph import END, START, StateGraph

from agents.mock_data import (
    MOCK_DOCUMENTS,
    MOCK_HYPOTHESES,
    MOCK_IDEA_ID,
    MOCK_JOB_ID,
    MOCK_RAW_INPUT,
    MOCK_STRUCTURED_IDEA,
)
from retrieval.tools import retrieve, vector_search
from shared.contracts import (
    AgentName,
    AgentRun,
    AnalysisJob,
    Confidence,
    CriticResult,
    Depth,
    DocumentRecord,
    EvidenceItem,
    Hypothesis,
    IdeaRecord,
)
from shared.state import VentureScoutState


def _mock_hypotheses(job_id: str, idea_id: str) -> list[Hypothesis]:
    return [
        Hypothesis(job_id=job_id, idea_id=idea_id, **item)
        for item in MOCK_HYPOTHESES
    ]


def _evidence_map(items: list[EvidenceItem]) -> dict[str, EvidenceItem]:
    return {item.evidence_id: item for item in items}


def _stance_counts(evidence: list[EvidenceItem]) -> dict[str, int]:
    return {
        "supports": sum(item.stance == "supports" for item in evidence),
        "contradicts": sum(item.stance == "contradicts" for item in evidence),
        "neutral": sum(item.stance == "neutral" for item in evidence),
    }


def _evidence_strength(evidence: list[EvidenceItem]) -> float:
    if not evidence:
        return 0.0
    scores = [
        item.relevance_score * item.reliability_score
        for item in evidence
    ]
    return round(sum(scores) / len(scores), 3)


def _confidence_from_strength(strength: float) -> Confidence:
    if strength >= 0.75:
        return "high"
    if strength >= 0.45:
        return "mid"
    return "low"


def _validate_structured_idea(idea: IdeaRecord, hypotheses: list[Hypothesis]) -> dict[str, Any]:
    required_fields = [
        "title",
        "target_customer",
        "problem_statement",
        "solution_summary",
        "business_model_hint",
    ]
    missing_fields = [
        field
        for field in required_fields
        if not getattr(idea, field)
    ]
    hypothesis_axes = {hypothesis.axis for hypothesis in hypotheses}
    expected_axes = {
        "customer_problem",
        "competition",
        "business_model",
        "technology",
        "ip",
    }
    missing_axes = sorted(expected_axes - hypothesis_axes)

    return {
        "missing_fields": missing_fields,
        "missing_axes": missing_axes,
        "technical_element_count": len(idea.technical_elements),
        "patent_keyword_count": len(idea.patent_keywords),
        "ready_for_analysis": not missing_fields and not missing_axes,
    }


def _document_map() -> dict[str, DocumentRecord]:
    return {
        item["document_id"]: DocumentRecord(**item)
        for item in MOCK_DOCUMENTS
    }


def _agent_run(
    *,
    job_id: str,
    agent_name: AgentName,
    hypothesis_id: str,
    depth: Depth,
    confidence: Confidence,
    evidence: list[EvidenceItem],
    output_json: dict[str, Any],
) -> AgentRun:
    return AgentRun(
        agent_run_id=f"run_mock_{agent_name}_{hypothesis_id}",
        job_id=job_id,
        hypothesis_id=hypothesis_id,
        agent_name=agent_name,
        model_name="mock",
        depth=depth,
        confidence=confidence,
        grounded_on=[item.evidence_id for item in evidence],
        output_json=output_json,
        groundedness_score=1.0 if evidence else 0.0,
        overclaim_flag=False,
        status="done",
    )


def structuring_node(state: VentureScoutState) -> dict:
    """raw_input에서 ideas, analysis_jobs, hypotheses 행을 만든다."""

    job_id = state.get("job_id", MOCK_JOB_ID)
    idea_id = state.get("idea_id", MOCK_IDEA_ID)
    raw_input = state.get("raw_input", MOCK_RAW_INPUT)
    idea_payload = {
        **MOCK_STRUCTURED_IDEA,
        "idea_id": idea_id,
        "raw_input": raw_input,
    }

    idea = IdeaRecord(**idea_payload)

    hypotheses = _mock_hypotheses(job_id, idea_id)
    structuring_quality = _validate_structured_idea(idea, hypotheses)

    analysis_job = AnalysisJob(
        job_id=job_id,
        idea_id=idea_id,
        status="running" if structuring_quality["ready_for_analysis"] else "failed",
        current_stage="structuring",
        progress_pct=20 if structuring_quality["ready_for_analysis"] else 0,
    )

    if not structuring_quality["ready_for_analysis"]:
        raise ValueError(f"Structuring mock data is incomplete: {structuring_quality}")

    return {
        "idea": idea,
        "analysis_job": analysis_job,
        "hypotheses": hypotheses,
        "documents": _document_map(),
    }


def market_node(state: VentureScoutState) -> dict:
    job_id = state["analysis_job"].job_id
    evidence = retrieve("H1", "meeting follow-up pain", job_id=job_id)
    return {
        "evidence_items": _evidence_map(evidence),
        "agent_runs": [
            _agent_run(
                job_id=job_id,
                agent_name="market",
                hypothesis_id="H1",
                depth="full",
                confidence="low",
                evidence=evidence,
                output_json={
                    "summary": "Mock market signal exists but needs direct interviews.",
                    "key_findings": ["Evidence is seeded, not user-validated."],
                    "risks": ["Pain intensity and buyer urgency are unproven."],
                    "recommendations": ["Interview 10 target customers."],
                },
            )
        ],
    }


def competitor_node(state: VentureScoutState) -> dict:
    job_id = state["analysis_job"].job_id
    evidence = retrieve("H2", "adjacent meeting tools", job_id=job_id)
    return {
        "evidence_items": _evidence_map(evidence),
        "agent_runs": [
            _agent_run(
                job_id=job_id,
                agent_name="competitor",
                hypothesis_id="H2",
                depth="light",
                confidence="low",
                evidence=evidence,
                output_json={
                    "summary": "Adjacent tools exist; differentiation is not yet proven.",
                    "key_findings": ["Competition requires workflow-level positioning."],
                    "risks": ["Generic summarization is crowded."],
                    "recommendations": ["Narrow to one vertical workflow."],
                },
            )
        ],
    }


def tech_node(state: VentureScoutState) -> dict:
    job_id = state["analysis_job"].job_id
    evidence = retrieve("H4", "STT LLM summarization latency cost", job_id=job_id)
    stance_counts = _stance_counts(evidence)
    strength = _evidence_strength(evidence)
    confidence = _confidence_from_strength(strength)
    feasibility_signal = "mid" if stance_counts["contradicts"] else confidence
    supporting_ids = [
        item.evidence_id
        for item in evidence
        if item.stance == "supports"
    ]
    risk_ids = [
        item.evidence_id
        for item in evidence
        if item.stance == "contradicts"
    ]

    return {
        "evidence_items": _evidence_map(evidence),
        "agent_runs": [
            _agent_run(
                job_id=job_id,
                agent_name="tech",
                hypothesis_id="H4",
                depth="light",
                confidence=confidence,
                evidence=evidence,
                output_json={
                    "summary": (
                        "STT와 LLM 조합으로 프로토타입 경로는 열려 있지만, "
                        "긴 회의에서 지연시간과 단위 비용을 검증해야 한다."
                    ),
                    "feasibility_signal": feasibility_signal,
                    "evidence_strength": strength,
                    "stance_counts": stance_counts,
                    "supporting_evidence": supporting_ids,
                    "risk_evidence": risk_ids,
                    "architecture_assumption": [
                        "음성 파일은 STT API로 텍스트화한다.",
                        "요약과 액션 아이템 추출은 LLM API를 분리 호출한다.",
                        "Slack/Notion 동기화는 비동기 worker로 처리한다.",
                    ],
                    "required_models_or_apis": [
                        "STT API",
                        "LLM summarization API",
                        "LLM action-item extraction prompt",
                        "Slack/Notion integration API",
                    ],
                    "risk_register": [
                        {
                            "risk": "긴 회의 처리 지연",
                            "why_it_matters": "사용자가 회의 직후 결과를 기대하면 UX를 해칠 수 있다.",
                            "mitigation": "구간별 요약, 비동기 처리, 진행률 표시를 실험한다.",
                        },
                        {
                            "risk": "토큰/전사 비용 증가",
                            "why_it_matters": "좌석 단위 SaaS 마진을 갉아먹을 수 있다.",
                            "mitigation": "회의 길이별 원가표와 사용량 제한 정책을 만든다.",
                        },
                        {
                            "risk": "회의 데이터 보안",
                            "why_it_matters": "B2B 고객 도입의 핵심 구매 기준이다.",
                            "mitigation": "저장 최소화, 암호화, tenant 분리를 MVP 요구사항에 포함한다.",
                        },
                    ],
                    "validation_plan": [
                        "30분 회의 10건으로 STT+요약 end-to-end 지연시간 측정",
                        "회의 1시간당 전사 비용과 LLM 토큰 비용 산출",
                        "액션 아이템 precision/recall을 수동 라벨 30개로 비교",
                    ],
                    "go_no_go_metrics": {
                        "p95_latency_minutes": "<= 5",
                        "cost_per_meeting_usd": "<= 0.50",
                        "action_item_precision": ">= 0.80",
                    },
                    "recommendations": [
                        "먼저 회의 요약보다 액션 아이템 정확도를 제품 차별화 기준으로 잡는다.",
                        "비용 검증 전에는 무제한 요금제를 가정하지 않는다.",
                    ],
                },
            )
        ],
    }


def ip_node(state: VentureScoutState) -> dict:
    job_id = state["analysis_job"].job_id
    idea = state["idea"]
    evidence = retrieve("H5", "meeting summarization patent limitations", job_id=job_id)
    candidates = vector_search(
        idea.technical_elements,
        job_id=job_id,
        hypothesis_id="H5",
    )
    stance_counts = _stance_counts(evidence)
    strength = _evidence_strength(evidence)
    candidate_rows = []
    for candidate in candidates:
        if candidate.hybrid_score >= 0.78:
            risk_band = "high_watch"
        elif candidate.hybrid_score >= 0.70:
            risk_band = "watch"
        else:
            risk_band = "low_watch"

        candidate_rows.append(
            {
                **candidate.model_dump(),
                "risk_band": risk_band,
                "agent_interpretation": (
                    "수동 claim chart 검토 우선순위가 높다."
                    if risk_band == "high_watch"
                    else "보조 후보로 보되 직접 중첩 단정은 금물이다."
                ),
            }
        )

    high_overlap = [
        row["plan_technical_element"]
        for row in candidate_rows
        if row["risk_band"] == "high_watch"
    ]
    overlap_signal = "mid" if high_overlap else "low"
    confidence = "mid" if candidates and evidence else "low"

    return {
        "evidence_items": _evidence_map(evidence),
        "ip_overlap_candidates": candidates,
        "agent_runs": [
            _agent_run(
                job_id=job_id,
                agent_name="ip",
                hypothesis_id="H5",
                depth="full",
                confidence=confidence,
                evidence=evidence,
                output_json={
                    "summary": (
                        "시그니처 검색 후보에서 일부 claim limitation 중첩 신호가 보인다. "
                        "이는 법적 침해 판단이 아니라, 수동 검토와 회피 설계를 위한 우선순위 신호다."
                    ),
                    "overlap_signal": overlap_signal,
                    "evidence_strength": strength,
                    "stance_counts": stance_counts,
                    "high_overlap_elements": high_overlap,
                    "design_around_options": [
                        "범용 회의 요약 대신 특정 직무/산업 workflow 후속 조치로 범위를 좁힌다.",
                        "요약 생성 자체보다 action item 상태 추적, 담당자 배정, 완료 검증을 핵심 차별점으로 둔다.",
                        "claim chart에서 speech-to-text, summary generation, task extraction 구성요소를 분리해 검토한다.",
                    ],
                    "claim_review_queue": [
                        {
                            "candidate_id": row["candidate_id"],
                            "element": row["plan_technical_element"],
                            "hybrid_score": row["hybrid_score"],
                            "risk_band": row["risk_band"],
                            "evidence_id": row["evidence_id"],
                        }
                        for row in candidate_rows
                    ],
                    "legal_guardrail_note": (
                        "특허 침해 여부를 단정하지 않는다. 현재 출력은 claim limitation 유사도와 "
                        "evidence_id에 기반한 사전 리스크 신호다."
                    ),
                    "manual_review_questions": [
                        "독립항 기준으로 필수 구성요소가 모두 제품 구현에 들어가는가?",
                        "요약 생성과 action item 추출이 같은 claim family에 묶이는가?",
                        "workflow-specific 후속 조치 중심으로 claim 요소를 회피할 수 있는가?",
                    ],
                    "candidates": candidate_rows,
                },
            )
        ],
    }


def bm_node(state: VentureScoutState) -> dict:
    job_id = state["analysis_job"].job_id
    evidence = retrieve("H3", "per-seat SaaS pricing willingness", job_id=job_id)
    return {
        "evidence_items": _evidence_map(evidence),
        "agent_runs": [
            _agent_run(
                job_id=job_id,
                agent_name="bm",
                hypothesis_id="H3",
                depth="light",
                confidence="low",
                evidence=evidence,
                output_json={
                    "summary": "Per-seat SaaS is plausible but unvalidated.",
                    "key_findings": ["Pricing evidence is only a placeholder."],
                    "risks": ["Buyer willingness and budget owner are unknown."],
                    "recommendations": ["Run pricing interviews."],
                },
            )
        ],
    }


def critic_node(state: VentureScoutState) -> dict:
    """agent_runs를 모아 grounding을 확인하고 최종 결정을 기록한다."""

    job_id = state["analysis_job"].job_id
    agent_runs = state.get("agent_runs", [])
    evidence_items = state.get("evidence_items", {})
    candidates = state.get("ip_overlap_candidates", [])
    evidence_ids = set(evidence_items)
    grounded_on = sorted({eid for run in agent_runs for eid in run.grounded_on})
    missing_evidence = [
        f"{run.agent_name} has no grounded_on evidence"
        for run in agent_runs
        if not run.grounded_on
    ]
    invalid_grounding = [
        {
            "agent_name": run.agent_name,
            "invalid_evidence_ids": sorted(set(run.grounded_on) - evidence_ids),
        }
        for run in agent_runs
        if set(run.grounded_on) - evidence_ids
    ]
    low_confidence = [
        run.agent_name
        for run in agent_runs
        if run.agent_name != "critic" and run.confidence == "low"
    ]
    agent_hypotheses = {
        run.hypothesis_id
        for run in agent_runs
        if run.hypothesis_id
    }
    expected_hypotheses = {
        hypothesis.hypothesis_id
        for hypothesis in state.get("hypotheses", [])
    }
    uncovered_hypotheses = sorted(expected_hypotheses - agent_hypotheses)
    contradicting_evidence = [
        evidence.evidence_id
        for evidence in evidence_items.values()
        if evidence.stance == "contradicts"
    ]
    high_ip_candidates = [
        candidate.candidate_id
        for candidate in candidates
        if candidate.hybrid_score >= 0.78
    ]

    scorecard = {
        "agent_run_count": len(agent_runs),
        "evidence_count": len(evidence_items),
        "grounded_claim_count": len(grounded_on),
        "low_confidence_agents": low_confidence,
        "uncovered_hypotheses": uncovered_hypotheses,
        "contradicting_evidence": contradicting_evidence,
        "high_ip_candidates": high_ip_candidates,
        "invalid_grounding": invalid_grounding,
    }

    if missing_evidence or invalid_grounding or uncovered_hypotheses:
        decision = "more_research"
        summary = "근거 연결 또는 가설 커버리지에 빈틈이 있어 추가 검증이 필요하다."
        confidence: Confidence = "low"
    elif len(low_confidence) >= 3:
        decision = "more_research"
        summary = "대부분의 핵심 가설이 low confidence라 고객/가격/기술 근거를 더 수집해야 한다."
        confidence = "low"
    elif high_ip_candidates:
        decision = "pivot"
        summary = "IP 시그니처 후보가 있어 범용 회의 요약보다 vertical workflow 중심으로 좁혀 검증하는 편이 낫다."
        confidence = "mid"
    elif not contradicting_evidence and len(low_confidence) <= 1:
        decision = "go"
        summary = "현재 근거 기준으로 치명적 반박이 적어 제한된 MVP 진행이 가능하다."
        confidence = "mid"
    else:
        decision = "pivot"
        summary = "근거는 있으나 반박 신호가 있어 포지셔닝과 검증 범위를 좁혀야 한다."
        confidence = "mid"

    objections = []
    if low_confidence:
        objections.append(f"Low-confidence agent runs: {', '.join(low_confidence)}")
    if contradicting_evidence:
        objections.append(
            f"Contradicting evidence exists: {', '.join(contradicting_evidence)}"
        )
    if high_ip_candidates:
        objections.append(
            f"IP signature candidates require manual review: {', '.join(high_ip_candidates)}"
        )

    critic = CriticResult(
        decision=decision,
        confidence=confidence,
        summary=summary,
        grounded_on=grounded_on,
        objections=objections,
        missing_evidence=missing_evidence
        + [
            f"{item['agent_name']} cites unknown evidence ids: {item['invalid_evidence_ids']}"
            for item in invalid_grounding
        ]
        + [
            f"No agent run covered hypothesis {hypothesis_id}"
            for hypothesis_id in uncovered_hypotheses
        ],
        next_experiments=[
            "H1: 타깃 고객 10명에게 회의 후속 업무 pain intensity를 인터뷰한다.",
            "H3: 구매 담당자 기준 좌석당 지불 의사와 예산 출처를 확인한다.",
            "H4: 30분 회의 10건으로 지연시간, 전사 비용, LLM 비용을 측정한다.",
            "H5: high_watch IP 후보에 대해 claim chart를 수동 작성한다.",
        ],
    )

    critic_run = AgentRun(
        agent_run_id="run_mock_critic",
        job_id=job_id,
        hypothesis_id=None,
        agent_name="critic",
        model_name="mock",
        depth="full",
        confidence=critic.confidence,
        grounded_on=grounded_on or ["ev_mock_handoff"],
        output_json={
            **critic.model_dump(),
            "scorecard": scorecard,
            "decision_rule": (
                "missing/invalid grounding 또는 uncovered hypothesis가 있으면 more_research; "
                "low confidence가 3개 이상이면 more_research; "
                "high IP candidate가 있으면 pivot; "
                "반박 근거가 적고 low confidence가 1개 이하이면 go."
            ),
        },
        groundedness_score=1.0 if grounded_on else 0.0,
        overclaim_flag=False,
        status="done",
    )

    analysis_job = state["analysis_job"].model_copy(
        update={
            "status": "done",
            "current_stage": "completed",
            "progress_pct": 100,
            "decision": critic.decision,
            "decision_summary": critic.summary,
        }
    )

    return {
        "critic": critic,
        "agent_runs": [critic_run],
        "analysis_job": analysis_job,
        "decision": critic.decision,
        "final_report": critic.model_dump(),
    }


def build_graph():
    graph = StateGraph(VentureScoutState)
    for name, fn in [
        ("structuring", structuring_node),
        ("market", market_node),
        ("competitor", competitor_node),
        ("tech", tech_node),
        ("ip", ip_node),
        ("bm", bm_node),
        ("critic", critic_node),
    ]:
        graph.add_node(name, fn)

    graph.add_edge(START, "structuring")
    for node in ["market", "competitor", "tech", "ip", "bm"]:
        graph.add_edge("structuring", node)
        graph.add_edge(node, "critic")
    graph.add_edge("critic", END)
    return graph.compile()


if __name__ == "__main__":
    app = build_graph()
    print(app.invoke({"raw_input": "AI meeting automation SaaS"}))
