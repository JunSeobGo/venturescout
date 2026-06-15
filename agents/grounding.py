"""
agents/grounding.py

Agent 출력이 실제 근거(Evidence)에 기반하는지 검증하는 모듈.

역할:
1. grounded_on 필드 검증
2. 존재하지 않는 Evidence 참조 탐지
3. 근거 없는 주장 방지
4. Guardrail 기반 과장 표현 검사
5. 최종 Agent Output 품질 검증

검증 항목:

1. grounded_on 비어있지 않은가?
2. 존재하는 evidence_id만 참조하는가?
3. 과장 표현(overclaim)이 포함되지 않았는가?
"""

from typing import Iterable

from agents.guardrails import detect_overclaim


def validate_grounded_output(
    output: dict,
    allowed_evidence_ids: Iterable[str]
) -> tuple[bool, list[str]]:
    """
    Agent 출력 검증

    Parameters
    ----------
    output
        Agent가 생성한 결과 JSON

    allowed_evidence_ids
        현재 Agent가 사용할 수 있는
        Evidence ID 목록

    Returns
    -------
    (성공여부, 에러목록)

    예:

    (
        False,
        [
            "grounded_on is empty",
            "invalid evidence_id found: ['ev_999']"
        ]
    )
    """

    errors = []

    # 사용 가능한 Evidence ID 집합
    allowed = set(allowed_evidence_ids)

    # Agent가 실제로 참조한 Evidence
    grounded_on = set(
        output.get("grounded_on", [])
    )

    # --------------------------------------------------------------
    # 1. grounded_on 검증
    # --------------------------------------------------------------

    if not grounded_on:
        errors.append(
            "grounded_on is empty"
        )

    # --------------------------------------------------------------
    # 2. 존재하지 않는 Evidence 사용 여부 확인
    # --------------------------------------------------------------

    invalid = grounded_on - allowed

    if invalid:
        errors.append(
            f"invalid evidence_id found: {sorted(invalid)}"
        )

    # --------------------------------------------------------------
    # 3. 과장 표현 검증
    # --------------------------------------------------------------

    text = str(output)

    overclaims = detect_overclaim(text)

    for phrase in overclaims:
        errors.append(
            f"overclaim phrase detected: {phrase}"
        )

    # --------------------------------------------------------------
    # 검증 결과 반환
    # --------------------------------------------------------------

    return (
        len(errors) == 0,
        errors
    )