"""
agents/nodes/tech_node.py

기술성(Technical Feasibility) 검증 노드.

역할:
1. 기술 관련 가설(H4) 검증
2. Repository 또는 State에서 기술 증거(Evidence) 조회
3. 기술 구현 가능성 평가
4. 기술 리스크 및 비용 리스크 분석
5. Grounding 검증 수행
6. Agent 실행 결과 저장

검증 대상 가설:

H4:
"핵심 기능은 현재 기술로 프로토타입 구현 가능하다."
"""

from agents.grounding import validate_grounded_output
from agents.mock_repository import MockRepository
from agents.nodes.runtime_inputs import state_evidence
from agents.state import VentureScoutState


# 상세 노드 단독 테스트를 위한 fallback Repository
repo = MockRepository()


def tech_node(state: VentureScoutState) -> VentureScoutState:
    """
    기술성 분석 노드

    입력:
        VentureScoutState

    출력:
        state["tech_result"]

    기존 tech_result와 output_json의 키 구조를 유지한다.
    """

    # ------------------------------------------------------------------
    # H4(기술 가설) 관련 Evidence 조회
    # 실제 State 데이터를 우선하고 없으면 mock fallback 사용
    # ------------------------------------------------------------------

    evidence = state_evidence(
        state,
        "H4",
        repo.get_evidence_for_hypothesis("H4"),
    )

    # Grounding 검증에 사용할 허용 Evidence ID
    allowed_ids = [
        str(e["evidence_id"])
        for e in evidence
        if e.get("evidence_id")
    ]

    technical_elements = [
        str(element)
        for element in state.get("technical_elements", [])
        if str(element).strip()
    ]
    supports = sum(1 for item in evidence if item.get("stance") == "supports")
    contradicts = sum(
        1 for item in evidence if item.get("stance") == "contradicts"
    )

    if technical_elements and evidence:
        summary = (
            f"핵심 기술요소 {len(technical_elements)}개와 관련 근거 "
            f"{len(evidence)}개를 확인했다. 프로토타입의 품질·비용·지연을 "
            "실제 입력으로 검증해야 한다."
        )
        feasibility_signal = "mid" if contradicts <= supports else "low"
    else:
        summary = (
            "핵심 기술요소 또는 관련 근거가 부족해 현재 구현 가능성을 "
            "판단하기 어렵다."
        )
        feasibility_signal = "low"

    key_findings = [
        str(item.get("evidence_text") or "")[:180]
        for item in evidence[:2]
        if item.get("evidence_text")
    ]
    if not key_findings:
        key_findings = ["기술성 판단을 위한 실제 근거 수집이 필요하다."]

    # ------------------------------------------------------------------
    # Tech Agent 분석 결과 생성
    # 기존 필드 구조는 바꾸지 않고 값만 실제 입력 기반으로 생성
    # ------------------------------------------------------------------

    output = {

        # Agent 식별 정보
        "agent_name": "tech",

        # 검증 대상 가설
        "hypothesis_id": "H4",

        # 빠른 분석 모드
        "depth": "light",

        # 현재 판단 신뢰도
        "confidence": (
            "mid"
            if len(evidence) >= 2 and technical_elements
            else "low"
        ),

        # 어떤 Evidence를 근거로 사용했는가
        "grounded_on": allowed_ids,

        # 기술성 분석 요약
        "summary": summary,

        # 핵심 발견사항
        "key_findings": key_findings,

        # 주요 리스크
        "risks": [
            "목표 성능과 품질 기준 미정",
            "처리 비용과 지연 변동",
            "실제 사용자 데이터의 보안·개인정보 요구",
        ],

        # 권장 검증 액션
        "recommendations": [
            "대표 입력 샘플로 정확도·처리시간·단위비용 측정",
            "핵심 기술요소별 실패 조건과 대체 구현 경로 확인",
        ],

        # 추가 조사 필요 여부
        "needs_more_research": True,

        # Agent 전용 상세 결과
        "output_json": {

            # 기술 구현 가능성 신호
            "feasibility_signal": feasibility_signal,

            # 필요한 외부 서비스
            "required_models_or_apis": technical_elements,

            # 비용 리스크
            "cost_risks": [
                "외부 모델/API 사용량 기반 비용",
                "처리량 증가에 따른 인프라 비용",
            ],
        },
    }

    # ------------------------------------------------------------------
    # Grounding 검증
    # ------------------------------------------------------------------

    ok, errors = validate_grounded_output(
        output,
        allowed_ids
    )

    output["groundedness_score"] = (
        1.0 if ok else 0.0
    )

    output["overclaim_flag"] = not ok

    output["validation_errors"] = errors

    # ------------------------------------------------------------------
    # State 저장
    # ------------------------------------------------------------------

    state["tech_result"] = output

    # ------------------------------------------------------------------
    # 실행 결과 저장
    # ------------------------------------------------------------------

    repo.insert_agent_run(output)

    return state
