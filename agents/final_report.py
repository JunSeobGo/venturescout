"""Critic 결과와 근거를 운영용 최종 보고서로 조립한다.

DB/contract 구조는 변경하지 않는다. 보고서 상세 내용은 agent_runs.output_json과
State의 final_report처럼 원래부터 느슨하게 정의된 JSON 영역에만 담는다.
"""

from __future__ import annotations

from collections import defaultdict
import os
from typing import Any


AGENT_LABELS = {
    "market": "고객 문제",
    "competitor": "경쟁·대안",
    "bm": "수익모델",
    "tech": "기술",
    "ip": "특허·IP",
}

EXPERIMENT_TEMPLATES = {
    "market": {
        "action": "타깃 고객 인터뷰와 문제 발생 빈도 조사를 수행한다.",
        "success_criteria": "반복 문제를 경험한 응답 비율과 현재 해결 비용을 수치화한다.",
    },
    "competitor": {
        "action": "직접·간접 대안의 기능, 가격, 전환비용을 비교한다.",
        "success_criteria": "고객이 선택할 명확한 차별화 기준을 1개 이상 검증한다.",
    },
    "bm": {
        "action": "구매자와 사용자별 가격 인터뷰 또는 결제 의향 실험을 수행한다.",
        "success_criteria": "예산 출처, 지불 단위, 수용 가격 구간을 확인한다.",
    },
    "tech": {
        "action": "대표 입력으로 핵심 기능 PoC의 품질, 지연시간, 단위비용을 측정한다.",
        "success_criteria": "제품 운영에 사용할 수 있는 go/no-go 수치를 확정한다.",
    },
    "ip": {
        "action": "상위 특허 후보의 독립항과 limitation을 수동 claim chart로 검토한다.",
        "success_criteria": "필수 구성요소 중첩 여부와 회피 설계 가능성을 기록한다.",
    },
}


def _dump(value: Any) -> dict[str, Any]:
    if isinstance(value, dict):
        return value
    if hasattr(value, "model_dump"):
        return value.model_dump()
    return {}


def _score(evidence: dict[str, Any]) -> float:
    return round(
        float(evidence.get("relevance_score") or 0.0)
        * float(evidence.get("reliability_score") or 0.0),
        4,
    )


def _document_reference(
    document_id: str,
    documents: dict[str, Any],
) -> dict[str, Any]:
    document = _dump(documents.get(document_id))
    return {
        "document_id": document_id,
        "source_type": document.get("source_type"),
        "external_id": document.get("ext_id"),
        "title": document.get("title") or f"문서 {document_id}",
        "url": document.get("canonical_url"),
    }


def _enrich_documents_from_db(
    documents: dict[str, Any],
    evidence_items: dict[str, Any],
) -> dict[str, Any]:
    """DB mode에서 보고서 표시용 문서 메타데이터를 읽기 전용으로 보강한다."""

    enriched = dict(documents)
    if os.getenv("AGENT_DATA_PROVIDER", "mock").lower() != "db":
        return enriched

    document_ids = {
        str(evidence.get("document_id"))
        for value in evidence_items.values()
        if (evidence := _dump(value)) and evidence.get("document_id")
    }
    missing_ids = sorted(document_ids - set(enriched))
    if not missing_ids:
        return enriched

    try:
        from db.connection import db_cursor

        with db_cursor() as cur:
            cur.execute(
                """
                SELECT
                    document_id::text AS document_id,
                    source_type,
                    ext_id,
                    title,
                    canonical_url
                FROM public.documents
                WHERE document_id = ANY(%s::uuid[])
                """,
                (missing_ids,),
            )
            for row in cur.fetchall():
                enriched[row["document_id"]] = dict(row)
    except Exception:
        # 보고서 메타데이터 보강 실패가 전체 Agent workflow를 중단시키면 안 된다.
        return enriched

    return enriched


def _related_patents(
    *,
    documents: dict[str, Any],
    evidence_items: dict[str, Any],
    candidates: list[Any],
) -> list[dict[str, Any]]:
    patent_evidence = {
        evidence_id: _dump(value)
        for evidence_id, value in evidence_items.items()
        if _dump(value).get("source_type") == "patent"
    }
    candidates_by_evidence: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for value in candidates:
        candidate = _dump(value)
        candidates_by_evidence[str(candidate.get("evidence_id"))].append(candidate)

    rows = []
    for evidence_id, evidence in patent_evidence.items():
        reference = _document_reference(str(evidence["document_id"]), documents)
        matched = candidates_by_evidence.get(evidence_id, [])
        best_score = max(
            (float(item.get("hybrid_score") or 0.0) for item in matched),
            default=None,
        )
        elements = list(
            dict.fromkeys(
                str(item["plan_technical_element"])
                for item in matched
                if item.get("plan_technical_element")
            )
        )
        if best_score is None:
            risk_band = "document_review"
        elif best_score >= 0.78:
            risk_band = "high_watch"
        elif best_score >= 0.70:
            risk_band = "watch"
        else:
            risk_band = "low_watch"

        rows.append(
            {
                **reference,
                "evidence_id": evidence_id,
                "stance": evidence.get("stance"),
                "evidence_strength": _score(evidence),
                "matched_elements": elements,
                "hybrid_score": best_score,
                "risk_band": risk_band,
                "why_relevant": str(evidence.get("evidence_text") or "")[:500],
                "recommended_review": (
                    "독립항과 관련 limitation을 제품 구성요소별로 대조하고 "
                    "회피 설계 가능성을 확인한다."
                ),
            }
        )

    return sorted(
        rows,
        key=lambda item: (
            item["hybrid_score"] if item["hybrid_score"] is not None else -1.0,
            item["evidence_strength"],
        ),
        reverse=True,
    )[:5]


def _related_business_signals(
    *,
    documents: dict[str, Any],
    evidence_items: dict[str, Any],
) -> list[dict[str, Any]]:
    excluded = {"patent", "seed_tech"}
    rows = []
    for evidence_id, value in evidence_items.items():
        evidence = _dump(value)
        source_type = str(evidence.get("source_type") or "unknown")
        if source_type in excluded:
            continue

        reference = _document_reference(str(evidence["document_id"]), documents)
        if "competitor" in source_type:
            signal_type = "경쟁·대안"
            suggested_action = "기능·가격·고객군을 비교해 실제 차별화 지점을 검증한다."
        elif "pricing" in source_type:
            signal_type = "가격·수익모델"
            suggested_action = "구매자 인터뷰와 가격 실험으로 지불 의사를 검증한다."
        elif "review" in source_type:
            signal_type = "고객 문제"
            suggested_action = "원문 출처와 실제 고객 인터뷰로 문제 강도를 교차 확인한다."
        else:
            signal_type = "시장 참고"
            suggested_action = "출처의 최신성과 직접 경쟁 관계를 추가 확인한다."

        rows.append(
            {
                **reference,
                "evidence_id": evidence_id,
                "signal_type": signal_type,
                "stance": evidence.get("stance"),
                "evidence_strength": _score(evidence),
                "why_relevant": str(evidence.get("evidence_text") or "")[:500],
                "suggested_action": suggested_action,
            }
        )

    return sorted(
        rows,
        key=lambda item: item["evidence_strength"],
        reverse=True,
    )[:5]


def _hypothesis_assessment(
    hypotheses: list[Any],
    agent_runs: list[Any],
) -> list[dict[str, Any]]:
    runs_by_hypothesis = {
        str(run.get("hypothesis_id")): run
        for value in agent_runs
        if (run := _dump(value)) and run.get("hypothesis_id")
    }
    rows = []
    for value in hypotheses:
        hypothesis = _dump(value)
        hypothesis_id = str(hypothesis.get("hypothesis_id"))
        run = runs_by_hypothesis.get(hypothesis_id)
        if not run:
            rows.append(
                {
                    "code": hypothesis.get("code"),
                    "axis": hypothesis.get("axis"),
                    "statement": hypothesis.get("statement"),
                    "status": "미검증",
                    "confidence": "low",
                    "summary": "담당 에이전트 실행 결과가 없다.",
                    "evidence_ids": [],
                }
            )
            continue

        output = run.get("output_json") or {}
        rows.append(
            {
                "code": hypothesis.get("code"),
                "axis": hypothesis.get("axis"),
                "statement": hypothesis.get("statement"),
                "status": "분석 완료",
                "confidence": run.get("confidence"),
                "summary": output.get("summary"),
                "evidence_ids": run.get("grounded_on") or [],
            }
        )
    return rows


def _recommendations(
    *,
    agent_runs: list[Any],
    high_ip_candidates: list[str],
) -> list[dict[str, Any]]:
    rows = []
    priority = 1
    runs_by_agent = {
        str(run.get("agent_name")): run
        for value in agent_runs
        if (run := _dump(value)) and run.get("agent_name")
    }
    ordered_agents = (
        ["ip", "market", "competitor", "bm", "tech"]
        if high_ip_candidates
        else ["market", "competitor", "bm", "tech", "ip"]
    )

    for agent_name in ordered_agents:
        run = runs_by_agent.get(agent_name)
        if not run:
            continue
        agent_name = str(run.get("agent_name") or "")
        if run.get("confidence") != "low" and not (
            agent_name == "ip" and high_ip_candidates
        ):
            continue

        template = EXPERIMENT_TEMPLATES[agent_name]
        rows.append(
            {
                "priority": priority,
                "area": AGENT_LABELS[agent_name],
                "action": template["action"],
                "reason": (
                    f"{agent_name} 분석 신뢰도가 낮거나 중요한 위험 신호가 남아 있다."
                ),
                "success_criteria": template["success_criteria"],
                "evidence_ids": run.get("grounded_on") or [],
            }
        )
        priority += 1

    if not rows:
        rows.append(
            {
                "priority": 1,
                "area": "제한적 실행",
                "action": "가장 불확실한 가설 하나를 선택해 소규모 MVP로 검증한다.",
                "reason": "현재 근거가 다음 단계 진행을 허용하지만 운영 수치 검증은 남아 있다.",
                "success_criteria": "사전에 합의한 핵심 지표를 실제 사용자 데이터로 충족한다.",
                "evidence_ids": [],
            }
        )
    return rows


def build_professional_final_report(
    *,
    idea: Any,
    hypotheses: list[Any],
    documents: dict[str, Any],
    evidence_items: dict[str, Any],
    agent_runs: list[Any],
    candidates: list[Any],
    critic: Any,
    scorecard: dict[str, Any],
    decision_rule: str,
) -> dict[str, Any]:
    """현재 수집된 근거만 사용해 추적 가능한 운영용 보고서를 만든다."""

    idea_row = _dump(idea)
    critic_row = _dump(critic)
    documents = _enrich_documents_from_db(documents, evidence_items)
    related_patents = _related_patents(
        documents=documents,
        evidence_items=evidence_items,
        candidates=candidates,
    )
    business_signals = _related_business_signals(
        documents=documents,
        evidence_items=evidence_items,
    )
    high_ip_candidates = list(scorecard.get("high_ip_candidates") or [])
    recommendations = _recommendations(
        agent_runs=agent_runs,
        high_ip_candidates=high_ip_candidates,
    )

    decision_reasons = []
    if scorecard.get("invalid_grounding"):
        decision_reasons.append("일부 분석이 존재하지 않는 evidence_id를 참조했다.")
    if scorecard.get("uncovered_hypotheses"):
        decision_reasons.append("검증되지 않은 가설이 남아 있다.")
    if scorecard.get("low_confidence_agents"):
        decision_reasons.append(
            "낮은 신뢰도의 분석: "
            + ", ".join(scorecard["low_confidence_agents"])
        )
    if scorecard.get("contradicting_evidence"):
        decision_reasons.append("가설을 반박하는 근거가 확인됐다.")
    if high_ip_candidates:
        decision_reasons.append("수동 검토가 필요한 높은 IP 중첩 후보가 있다.")
    if not decision_reasons:
        decision_reasons.append("근거 연결과 가설 커버리지가 현재 기준을 충족했다.")

    research_gaps = []
    if not related_patents:
        research_gaps.append(
            "관련 특허 후보가 없다. claim limitation 검색 범위와 query 품질을 확인해야 한다."
        )
    if not business_signals:
        research_gaps.append(
            "관련 경쟁사·시장·가격 근거가 없다. 웹/경쟁사/가격 자료를 추가 수집해야 한다."
        )

    return {
        "report_version": "1.0",
        "executive_summary": {
            "idea_title": idea_row.get("title"),
            "decision": critic_row.get("decision"),
            "confidence": critic_row.get("confidence"),
            "conclusion": critic_row.get("summary"),
            "decision_reasons": decision_reasons,
        },
        "idea_snapshot": {
            "target_customer": idea_row.get("target_customer"),
            "problem_statement": idea_row.get("problem_statement"),
            "solution_summary": idea_row.get("solution_summary"),
            "business_model_hint": idea_row.get("business_model_hint"),
            "technical_elements": idea_row.get("technical_elements") or [],
        },
        "hypothesis_assessment": _hypothesis_assessment(hypotheses, agent_runs),
        "related_patents": related_patents,
        "related_business_signals": business_signals,
        "strategic_options": [
            {
                "option": "근거 보강 후 진행",
                "when_to_choose": "고객·가격·기술 가설의 confidence가 낮을 때",
                "proposal": "가장 불확실한 가설부터 짧은 검증 실험을 수행한다.",
            },
            {
                "option": "범위 축소 또는 포지셔닝 조정",
                "when_to_choose": "경쟁 또는 IP 중첩 신호가 높을 때",
                "proposal": "고객군·업무흐름·구현 구성요소 중 하나를 좁혀 차별화한다.",
            },
            {
                "option": "제한적 MVP 진행",
                "when_to_choose": "치명적 반박이 없고 핵심 가설의 근거가 충분할 때",
                "proposal": "운영 지표와 중단 기준을 먼저 정한 뒤 소규모로 출시한다.",
            },
        ],
        "priority_recommendations": recommendations,
        "research_gaps": research_gaps,
        "objections": critic_row.get("objections") or [],
        "missing_evidence": critic_row.get("missing_evidence") or [],
        "traceability": {
            "evidence_ids": critic_row.get("grounded_on") or [],
            "scorecard": scorecard,
            "decision_rule": decision_rule,
        },
        "limitations": [
            "관련 특허와 사업 신호는 현재 수집된 documents/evidence 범위에서만 선별했다.",
            "검색 결과가 없다는 사실은 특허 비침해, 시장 부재 또는 경쟁 부재를 의미하지 않는다.",
            "특허 결과는 법률 의견이 아니며 독립항 중심의 전문가 검토가 필요하다.",
            "시장·가격 제안은 실제 고객 인터뷰와 결제 행동으로 재검증해야 한다.",
        ],
    }
