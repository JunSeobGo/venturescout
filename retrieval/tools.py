"""Track C 에이전트가 사용하는 mock 검색 도구.

운영 환경에서는 Track B가 documents, evidence_items, claim_limitations에 대한
하이브리드 검색으로 본문을 교체한다. 함수 시그니처는 이미 9개 테이블
계약을 반환하므로, 에이전트는 병렬로 개발할 수 있다.
"""

from __future__ import annotations

from agents.mock_data import MOCK_EVIDENCE, MOCK_IP_CANDIDATES
from shared.contracts import EvidenceItem, IPOverlapCandidate


def retrieve(
    hypothesis_id: str,
    query: str,
    *,
    job_id: str = "job_mock_001",
    k: int = 5,
) -> list[EvidenceItem]:
    """가설과 관련된 evidence_items 행을 반환한다."""

    matched = [
        EvidenceItem(
            job_id=job_id,
            **item,
        )
        for item in MOCK_EVIDENCE
        if item["hypothesis_id"] == hypothesis_id
    ]

    return matched[:k]


def vector_search(
    technical_elements: list[str],
    *,
    job_id: str = "job_mock_001",
    hypothesis_id: str = "H5",
    k: int = 10,
) -> list[IPOverlapCandidate]:
    """기계가 생성한 IP 중첩 후보를 반환한다. 법적 판단은 아니다."""

    elements = set(technical_elements)
    matched = [
        IPOverlapCandidate(
            job_id=job_id,
            **{
                key: value
                for key, value in item.items()
                if key != "limitation_text"
            },
        )
        for item in MOCK_IP_CANDIDATES
        if item["hypothesis_id"] == hypothesis_id
        and (not elements or item["plan_technical_element"] in elements)
    ]

    return matched[:k]
