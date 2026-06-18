"""
agents/nodes/critic_node.py

Critic Agent 노드.

역할:
1. 모든 Agent 결과 종합 검토
2. 근거(Evidence) 누락 여부 확인
3. 과장(Overclaim) 표현 검출
4. Low Confidence 결과 식별
5. 반론(Objection) 생성
6. 최종 의사결정(Go / Pivot / Kill / More Research)
7. 최종 보고서 생성

VentureScout에서 가장 중요한 검증 단계이며,
다른 Agent들의 결과를 그대로 믿지 않고
한 번 더 비판적으로 검토한다.

쉽게 말하면:

Market Agent      → 시장 분석
Tech Agent        → 기술 분석
IP Agent          → 특허 분석
BM Agent          → 사업모델 분석

        ↓

      Critic

        ↓

"정말 이 결론을 믿어도 되는가?"

를 검토하는 역할

흐름:

각 Agent 결과
      ↓
Evidence 확인
      ↓
Overclaim 검사
      ↓
Low Confidence 검사
      ↓
반론 생성
      ↓
최종 의사결정
      ↓
Final Report 생성
"""

from agents.state import VentureScoutState
from agents.guardrails import detect_overclaim
from agents.mock_repository import MockRepository

# 테스트용 Repository
repo = MockRepository()


def critic_node(state: VentureScoutState) -> VentureScoutState:
    """
    Critic Agent

    입력:
        VentureScoutState

    출력:
        state["critic_result"]
        state["decision"]
        state["final_report"]

    주요 역할:
    - Agent 결과 검증
    - 반론 생성
    - 최종 판단
    """

    # ------------------------------------------------------------------
    # 각 Agent 결과 수집
    # ------------------------------------------------------------------

    results = [
        state.get("market_result"),
        state.get("competitor_result"),
        state.get("tech_result"),
        state.get("ip_result"),
        state.get("bm_result"),
    ]

    # None 제거
    results = [
        r
        for r in results
        if r
    ]

    # ------------------------------------------------------------------
    # Critic 분석용 변수
    # ------------------------------------------------------------------

    objections = []         # 반론
    overclaim_points = []   # 과장 표현
    missing_evidence = []   # 증거 부족

    grounded_on = []        # 전체 Evidence

    # ------------------------------------------------------------------
    # Agent 결과 검토
    # ------------------------------------------------------------------

    for r in results:

        # Evidence 수집
        grounded_on.extend(
            r.get("grounded_on", [])
        )

        # --------------------------------------------------------------
        # 근거 누락 검사
        # --------------------------------------------------------------

        if not r.get("grounded_on"):

            objections.append(
                f"{r['agent_name']} 결과에 evidence_id가 없습니다."
            )

        # --------------------------------------------------------------
        # 과장 표현 검사
        # --------------------------------------------------------------

        text = str(r)

        found = detect_overclaim(text)

        for phrase in found:

            overclaim_points.append(
                f"{r['agent_name']} overclaim: {phrase}"
            )

        # --------------------------------------------------------------
        # Confidence 검사
        # --------------------------------------------------------------

        if r.get("confidence") == "low":

            missing_evidence.append(
                f"{r['agent_name']} 결과는 "
                f"Low confidence이므로 추가 검증 필요"
            )

    # ------------------------------------------------------------------
    # 최종 의사결정
    #
    # 특정 아이디어에 고정하지 않고 근거·신뢰도·IP 신호로 판단
    # ------------------------------------------------------------------

    low_confidence_count = sum(
        1
        for r in results
        if r.get("confidence") == "low"
    )
    high_ip_signal = any(
        r.get("agent_name") == "ip"
        and (r.get("output_json") or {}).get("overlap_signal") == "high"
        for r in results
    )

    if not results or objections or low_confidence_count >= 3:
        decision = "more_research"
        confidence = "low"
        decision_reason = (
            "근거가 누락됐거나 low confidence 결과가 많아 "
            "추가 검증이 필요하다."
        )
    elif high_ip_signal:
        decision = "pivot"
        confidence = "mid"
        decision_reason = (
            "높은 IP 중첩 감시 신호가 있어 회피 설계 또는 "
            "구현 범위 조정이 필요하다."
        )
    elif overclaim_points:
        decision = "more_research"
        confidence = "low"
        decision_reason = (
            "근거보다 강한 표현이 포함되어 결론 전에 재검증이 필요하다."
        )
    elif low_confidence_count <= 1:
        decision = "go"
        confidence = "mid"
        decision_reason = (
            "근거 연결이 확보되고 low confidence 결과가 제한적이어서 "
            "다음 검증 단계로 진행할 수 있다."
        )
    else:
        decision = "pivot"
        confidence = "mid"
        decision_reason = (
            "근거는 연결됐지만 불확실성이 남아 있어 "
            "범위를 줄여 검증하는 것이 타당하다."
        )

    next_experiments = [
        "low confidence 가설의 추가 근거 수집",
        "핵심 가설별 go/no-go 기준 측정",
        "상위 IP 후보 독립항 수동 검토",
    ]

    # ------------------------------------------------------------------
    # Critic 결과 생성
    # ------------------------------------------------------------------

    output = {

        # Agent 정보
        "agent_name": "critic",

        "hypothesis_id": None,

        "depth": "full",

        "confidence": confidence,

        # 모든 Agent가 사용한 Evidence
        "grounded_on":
            sorted(set(grounded_on)),

        # 최종 판단 요약
        "summary":
            decision_reason,

        # 핵심 발견사항
        "key_findings": [
            f"분석 결과 {len(results)}개를 검토했다.",
            f"low confidence 결과는 {low_confidence_count}개다.",
        ],

        # 발견된 문제점
        "risks":
            objections
            + overclaim_points
            + missing_evidence,

        # 추천 액션
        "recommendations":
            next_experiments,

        # 추가 연구 필요
        "needs_more_research":
            decision == "more_research",

        # 상세 결과
        "output_json": {

            # 최종 결정
            "decision":
                decision,

            # 결정 근거
            "decision_reason":
                decision_reason,

            # 반론
            "objections":
                objections,

            # 과장 표현
            "overclaim_points":
                overclaim_points,

            # 증거 부족
            "missing_evidence":
                missing_evidence,

            # 다음 실험
            "next_experiments":
                next_experiments,
        },
    }

    # ------------------------------------------------------------------
    # State 저장
    # ------------------------------------------------------------------

    state["critic_result"] = output

    # 최종 의사결정 저장
    state["decision"] = decision

    # 최종 보고서 저장
    state["final_report"] = output["output_json"]

    # ------------------------------------------------------------------
    # 실행 결과 저장
    # 실제 서비스에서는 DB 저장
    # ------------------------------------------------------------------

    repo.insert_agent_run(output)

    return state
