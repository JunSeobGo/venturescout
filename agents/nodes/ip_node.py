"""
agents/nodes/ip_node.py

IP(Intellectual Property) / 특허 리스크 분석 노드.

역할:
1. IP 관련 가설(H5) 검증
2. 특허 청구항(Claim) 중첩 후보 조회
3. 기술 요소별 특허 중복 위험 분석
4. Design-Around(회피 설계) 전략 도출
5. Grounding 검증 수행
6. IP 분석 결과 저장

중요:
이 노드는 법적 침해 여부를 판단하지 않는다.
"""

from agents.grounding import validate_grounded_output
from agents.mock_repository import MockRepository
from agents.nodes.runtime_inputs import state_ip_candidates
from agents.state import VentureScoutState


# 상세 노드 단독 테스트를 위한 fallback Repository
repo = MockRepository()


def ip_node(state: VentureScoutState) -> VentureScoutState:
    """
    IP / 특허 분석 노드

    입력:
        VentureScoutState

    출력:
        state["ip_result"]

    기존 ip_result와 output_json의 키 구조를 유지한다.
    """

    # ------------------------------------------------------------------
    # H5 관련 특허 후보 조회
    # 실제 State 데이터를 우선하고 없으면 mock fallback 사용
    # ------------------------------------------------------------------

    candidates = state_ip_candidates(
        state,
        repo.get_ip_overlap_candidates(
            state["job_id"],
            "H5"
        ),
    )

    # Grounding 검증용 Evidence ID
    evidence_ids = [
        str(c["evidence_id"])
        for c in candidates
        if c.get("evidence_id")
    ]

    # 중첩 위험 기술 요소
    high_overlap_elements = []

    # 회피 설계 전략
    design_around_options = []

    # ------------------------------------------------------------------
    # 특허 중복 위험 분석
    # ------------------------------------------------------------------

    for candidate in candidates:

        if float(candidate.get("hybrid_score") or 0.0) >= 0.78:

            element = candidate.get("plan_technical_element")
            if element and element not in high_overlap_elements:
                high_overlap_elements.append(str(element))

    # ------------------------------------------------------------------
    # Design Around 전략 생성
    # 특정 제품 도메인이 아니라 검색된 기술요소를 기준으로 생성
    # ------------------------------------------------------------------

    for element in high_overlap_elements:

        design_around_options.append(
            f"'{element}'의 처리 단계·입출력·구현 순서를 후보 청구항과 "
            "다르게 구성할 수 있는지 검토"
        )

    if high_overlap_elements:
        overlap_signal = "high"
        summary = (
            f"고위험 감시 기술요소 {len(high_overlap_elements)}개에서 청구항 "
            "limitation 중첩 신호가 있다. 이는 법적 침해 판단이 아니라 "
            "수동 검토가 필요한 사전 리스크 신호다."
        )
    elif candidates:
        overlap_signal = "low"
        summary = (
            "현재 후보에서는 강한 중첩 신호가 확인되지 않았다. 검색 누락 가능성이 "
            "있으므로 법적으로 안전하다는 의미는 아니다."
        )
    else:
        overlap_signal = "unknown"
        summary = (
            "IP 중첩 후보가 없어 현재 판단할 수 없다. claim limitation 검색 결과를 "
            "먼저 수집해야 한다."
        )

    key_findings = [
        (
            f"{candidate.get('plan_technical_element', '기술요소')}: "
            f"hybrid_score={float(candidate.get('hybrid_score') or 0.0):.4f}"
        )
        for candidate in candidates[:5]
    ]

    # ------------------------------------------------------------------
    # IP Agent 결과 생성
    # 기존 필드 구조는 바꾸지 않고 값만 실제 후보 기반으로 생성
    # ------------------------------------------------------------------

    output = {

        # Agent 정보
        "agent_name": "ip",

        # 검증 대상 가설
        "hypothesis_id": "H5",

        # 상세 분석 수행
        "depth": "full",

        # 현재 신뢰도
        "confidence": (
            "mid"
            if candidates
            else "low"
        ),

        # 사용한 Evidence
        "grounded_on": evidence_ids,

        # 분석 요약
        "summary": summary,

        # 핵심 발견사항
        "key_findings": key_findings,

        # 발견된 리스크
        "risks": [
            "유사도 점수는 법적 침해 판단을 대신할 수 없다.",
            "독립항과 각 limitation 충족 여부를 별도로 검토해야 한다.",
        ],

        # 권장 액션
        "recommendations": [
            "상위 유사 특허 독립항 수동 검토",
            "고위험 기술요소별 design-around 검토",
        ],

        # 추가 조사 필요
        "needs_more_research": True,

        # 상세 분석 결과
        "output_json": {

            # 특허 중복 위험 신호
            "overlap_signal": overlap_signal,

            # 위험 요소
            "high_overlap_elements":
                high_overlap_elements,

            # 회피 설계 전략
            "design_around_options":
                design_around_options,

            # 법적 판단이 아님을 명시
            "legal_guardrail_note":
                "법적 침해 판단이 아니라 청구항 중첩 기반 IP 리스크 신호입니다.",

            # 특허 후보 상세 데이터
            "candidates":
                candidates,
        },
    }

    # ------------------------------------------------------------------
    # Grounding 검증
    # ------------------------------------------------------------------

    ok, errors = validate_grounded_output(
        output,
        evidence_ids
    )

    output["groundedness_score"] = (
        1.0 if ok else 0.0
    )

    output["overclaim_flag"] = not ok

    output["validation_errors"] = errors

    # ------------------------------------------------------------------
    # State 저장
    # ------------------------------------------------------------------

    state["ip_result"] = output

    # ------------------------------------------------------------------
    # Agent 실행 결과 저장
    # ------------------------------------------------------------------

    repo.insert_agent_run(output)

    return state
