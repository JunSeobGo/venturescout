"""
agents/nodes/structuring_node.py

사용자의 원본 아이디어(raw_input)를 VentureScout가 분석 가능한
구조화 데이터로 변환하는 첫 번째 노드(Node).

역할:
1. 사용자 입력 해석
2. 아이디어 핵심 요소 추출
3. 사업 아이디어 구조화
4. 기술 요소 추출
5. 특허 검색 키워드 생성
6. 검증할 가설(Hypothesis) 생성

실서비스에서는 LLM(Claude)이 해당 작업을 수행하고,
호출할 수 없을 때는 원문에 명시된 필드만 보수적으로 추출한다.

흐름:

raw_input
    ↓
Structuring Node
    ↓
title
target_customer
technical_elements
patent_keywords
hypotheses
    ↓
후속 Agent 전달
"""

from agents.nodes.runtime_inputs import structure_raw_input
from agents.state import VentureScoutState


def structuring_node(state: VentureScoutState) -> VentureScoutState:
    """
    아이디어 구조화 노드

    입력:
        사용자의 자유 입력(raw_input)

    출력:
        구조화된 사업 아이디어 정보
        + 초기 가설 목록

    기존 State와 가설의 키 구조를 유지한다.
    """

    # 사용자 원본 입력
    raw = str(state["raw_input"]).strip()
    if not raw:
        raise ValueError("structuring_node에는 비어 있지 않은 raw_input이 필요합니다.")

    # ------------------------------------------------------------------
    # 아이디어 기본 정보 추출
    # Claude 결과를 우선하고 실패 시 원문에 명시된 필드만 사용
    # ------------------------------------------------------------------

    structured = structure_raw_input(raw)

    state["title"] = structured["title"]

    state["idea_type"] = structured["idea_type"]

    state["target_customer"] = structured["target_customer"]

    state["problem_statement"] = structured["problem_statement"]

    state["solution_summary"] = structured["solution_summary"]

    state["business_model_hint"] = structured["business_model_hint"]

    # ------------------------------------------------------------------
    # 핵심 기술 요소
    # 이후 Tech Agent, IP Agent가 활용
    # ------------------------------------------------------------------

    state["technical_elements"] = list(
        structured["technical_elements"]
    )

    # ------------------------------------------------------------------
    # 특허 검색용 키워드
    # 이후 Patent Search Agent 활용
    # ------------------------------------------------------------------

    state["patent_keywords"] = list(
        structured["patent_keywords"]
    )

    # ------------------------------------------------------------------
    # 초기 가설 생성
    #
    # VentureScout는
    # "아이디어 평가"가 아니라
    # "가설 검증" 방식으로 동작
    # ------------------------------------------------------------------

    problem = (
        structured["problem_statement"]
        or "타깃 고객의 핵심 문제"
    )
    technical_elements = (
        ", ".join(structured["technical_elements"])
        or "핵심 기술요소"
    )

    state["hypotheses"] = [

        # --------------------------------------------------------------
        # H1 고객 문제 가설
        # --------------------------------------------------------------
        {
            "hypothesis_id": "H1",

            "code": "H1",

            "axis": "고객문제",

            "statement":
                f"타깃 고객은 '{problem}'를 반복적으로 겪는다.",

            "confidence": "low",

            "next_validation":
                "타깃 고객 인터뷰",

            "supporting_evidence": [],

            "contradicting_evidence": [],
        },

        # --------------------------------------------------------------
        # H4 기술 구현 가능성 가설
        # --------------------------------------------------------------
        {
            "hypothesis_id": "H4",

            "code": "H4",

            "axis": "기술",

            "statement":
                f"'{technical_elements}'는 현재 기술로 프로토타입 구현 가능하다.",

            "confidence": "low",

            "next_validation":
                "대표 입력 기준 처리 품질·시간·비용 측정",

            "supporting_evidence": [],

            "contradicting_evidence": [],
        },

        # --------------------------------------------------------------
        # H5 특허/IP 가설
        # --------------------------------------------------------------
        {
            "hypothesis_id": "H5",

            "code": "H5",

            "axis": "IP",

            "statement":
                "기존 청구항과 직접 중첩하지 않는 구현 경로가 있다.",

            "confidence": "low",

            "next_validation":
                "상위 유사 특허 독립항 검토",

            "supporting_evidence": [],

            "contradicting_evidence": [],
        },
    ]

    # 다음 노드로 전달
    return state
