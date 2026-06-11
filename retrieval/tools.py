"""
Track B — 검색 tool (에이전트가 호출).
Day 1엔 mock 반환 → C/D가 B 완성 전에 병렬 개발.
실제 구현 시 시그니처(반환 타입)는 유지하고 내부만 pgvector+tsvector로 교체.
"""
from __future__ import annotations
from shared.contracts import EvidenceItem, OverlapCandidate

# TODO(B): pgvector + tsvector 하이브리드, rerank, 임베딩 모델 연결


def retrieve(hypothesis_id: str, query: str, k: int = 5) -> list[EvidenceItem]:
    """가설별 찬반 근거 회수. 지금은 mock."""
    return [
        EvidenceItem(
            evidence_id="ev_mock_0001",
            hypothesis_id=hypothesis_id,
            document_id="doc_mock_0001",
            source_type="seed_competitor",
            evidence_text="[MOCK] 유료 경쟁 도구 다수 존재",
            stance="contradicts",
            reliability_score=0.6,
        )
    ]


def vector_search(technical_elements: list[str], k: int = 10) -> list[OverlapCandidate]:
    """시그니처: 기술요소 ↔ 특허 limitation 매칭 후보. 지금은 mock."""
    return [
        OverlapCandidate(
            candidate_id="cand_mock_0001",
            limitation_id="lim_mock_0001",
            evidence_id="ev_mock_0002",
            plan_technical_element=technical_elements[0] if technical_elements else "STT",
            hybrid_score=0.86,
            rank=1,
        )
    ]
