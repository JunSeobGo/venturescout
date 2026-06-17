"""
Track B — 검색 tool (에이전트가 호출).
shared.contracts의 EvidenceItem / OverlapCandidate를 반환 — 시그니처(반환 타입) 고정.
내부는 pgvector + tsvector 하이브리드 검색(search/hybrid.py) + rerank(search/reranker.py).

Tier0 단순화: evidence_id는 documents.document_id를 그대로 사용한다
(evidence_items 테이블 적재는 Tier1, job_id/hypothesis_id 오케스트레이션 확정 후).
"""
from __future__ import annotations

import uuid

from shared.contracts import EvidenceItem, OverlapCandidate
from search.hybrid import HybridSearcher
from search.reranker import ReRanker

_searcher = HybridSearcher()
_reranker = ReRanker()


def retrieve(hypothesis_id: str, query: str, k: int = 5) -> list[EvidenceItem]:
    """가설별 찬반 근거 회수 (documents 하이브리드 검색 + rerank)."""
    raw = _searcher.search_documents(query=query, top_k=k * 2)
    ranked = _reranker.rerank(raw, prefer_contradicting=True, top_k=k)

    return [
        EvidenceItem(
            evidence_id=str(item["document_id"]),
            hypothesis_id=hypothesis_id,
            document_id=str(item["document_id"]),
            source_type=item["source_type"],
            evidence_text=str(item["clean_text"])[:1000],
            stance=item.get("stance", "neutral"),
            reliability_score=float(item.get("reliability_score") or 0.0),
        )
        for item in ranked
    ]


def vector_search(technical_elements: list[str], k: int = 10) -> list[OverlapCandidate]:
    """시그니처: 기술요소 ↔ 특허 limitation 매칭 후보 (claim_limitations 하이브리드 검색 + rerank)."""
    query = " ".join(technical_elements)
    plan_technical_element = technical_elements[0] if technical_elements else ""

    raw = _searcher.search_claim_limitations(query=query, top_k=k * 3)
    ranked = _reranker.rerank(raw, prefer_contradicting=False, top_k=k * 3)

    # 특허(patent_id) 단위 dedup — 같은 특허의 limitation 중 rerank_score 최상위 1개만 유지
    seen_patents: set[str] = set()
    deduped = []
    for item in ranked:
        pid = item.get("patent_id")
        if pid not in seen_patents:
            seen_patents.add(pid)
            deduped.append(item)
        if len(deduped) >= k:
            break

    return [
        OverlapCandidate(
            candidate_id=str(uuid.uuid4()),
            limitation_id=str(item["limitation_id"]),
            evidence_id=str(item["document_id"]),
            plan_technical_element=plan_technical_element,
            hybrid_score=float(item["hybrid_score"]),
            rank=rank,
        )
        for rank, item in enumerate(deduped, start=1)
    ]
