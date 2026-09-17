"""Live PostgreSQL/pgvector retrieval tools used by VentureScout agents."""

from __future__ import annotations

import os
import threading
import uuid

from pipeline.persistence import persist_ip_overlap_candidates
from shared.contracts import EvidenceItem, IPOverlapCandidate

# psycopg2 연결은 스레드 안전하지 않으므로 스레드마다 별도 인스턴스를 유지한다.
# 5개 에이전트가 병렬 스레드로 실행될 때 같은 연결을 공유하면 SSL 에러가 발생한다.
_local = threading.local()


def _require_live_retrieval() -> None:
    mode = os.getenv("RETRIEVAL", "live").lower()
    if mode != "live":
        raise RuntimeError(
            "RETRIEVAL must be 'live'. Fixed retrieval data is not allowed."
        )


# 가설 코드 → stance 축 이름. structuring이 만드는 H1~H5의 axis와 같다(graph.py 참조).
# 평가 경로(eval/labelset.py)는 축 이름을 그대로 넘기므로 폴백으로 원문을 쓴다.
AXIS_BY_HYPOTHESIS = {
    "H1": "customer_problem",
    "H2": "competition",
    "H3": "business_model",
    "H4": "technology",
    "H5": "ip",
}


def _attach_stance(rows: list[dict], hypothesis_id: str) -> None:
    """적재 시점에 태깅해 둔 stance를 검색 결과 최상위로 끌어올린다(제자리 수정).

    값은 이미 `documents.meta`에 담겨 따라 나오는데, 읽는 쪽(reranker, EvidenceItem)은
    최상위 `stance` 키를 본다. 그 사이가 이어져 있지 않아 항상 neutral로 떨어졌고,
    contradiction 축이 전 문서 0.5 고정이라 순위에 아무 영향을 주지 못했다(ADR-045).

    축마다 다른 키(`stance_<축>`)를 쓰므로 어느 가설로 검색하는지에 따라 값이 달라진다.
    태깅이 안 된 축·문서는 neutral이다 — 없는 판단을 지어내지 않는다.

    근거 인용(`stance_<축>_span`)은 여기서 꺼내지 않는다. EvidenceItem에 담을 자리가
    없어 지금 꺼내면 아무도 읽지 않는 값이 하나 더 생긴다 — 이 프로젝트가 반복해서
    겪은 죽은 코드 패턴이다. Citation Accuracy 지표를 붙일 때 소비처와 함께 꺼낸다.
    """
    axis = AXIS_BY_HYPOTHESIS.get(hypothesis_id, hypothesis_id)
    key = f"stance_{axis}"
    for row in rows:
        meta = row.get("meta")
        meta = meta if isinstance(meta, dict) else {}
        row["stance"] = meta.get(key) or "neutral"


def _get_engines():
    _require_live_retrieval()
    if not hasattr(_local, "searcher"):
        from search.hybrid import HybridSearcher
        from search.reranker import ReRanker

        _local.searcher = HybridSearcher()
        _local.reranker = ReRanker()
    return _local.searcher, _local.reranker


def retrieve(
    hypothesis_id: str,
    query: str,
    *,
    job_id: str = "",
    k: int = 5,
    source_types: list[str] | None = None,
) -> list[EvidenceItem]:
    """Retrieve evidence from live documents using hybrid search."""

    searcher, reranker = _get_engines()
    raw = searcher.search_documents(
        query=query,
        top_k=k * 2,
        source_types=source_types,
    )

    # stance는 적재 시점에 LLM 배치로 미리 태깅해 documents.meta에 넣어 둔다(ADR-047).
    # 검색 시점에는 꺼내 쓰기만 하므로 지연이 0이다 — NLI를 검색 경로에 넣었다가
    # +33초가 나왔던 방식(ADR-046)과 다른 점이 이것이다.
    _attach_stance(raw, hypothesis_id)
    ranked = reranker.rerank(raw, prefer_contradicting=True, top_k=k)

    return [
        EvidenceItem(
            evidence_id=str(item["document_id"]),
            job_id=job_id,
            hypothesis_id=hypothesis_id,
            document_id=str(item["document_id"]),
            source_type=item["source_type"],
            evidence_text=str(item["clean_text"])[:1000],
            stance=item.get("stance", "neutral"),
            relevance_score=float(item.get("hybrid_score") or 0.0),
            reliability_score=float(item.get("reliability_score") or 0.0),
        )
        for item in ranked
    ]


def vector_search(
    technical_elements: list[str],
    *,
    job_id: str = "",
    hypothesis_id: str = "",
    k: int = 10,
) -> list[IPOverlapCandidate]:
    """Retrieve live claim-limitation overlap candidates."""

    query = " ".join(technical_elements)
    if not query.strip():
        raise RuntimeError("IP search requires at least one technical element.")

    searcher, reranker = _get_engines()
    raw = searcher.search_claim_limitations(query=query, top_k=k * 3)
    ranked = reranker.rerank(raw, prefer_contradicting=False, top_k=k * 3)

    seen_patents: set[str] = set()
    deduped = []
    for item in ranked:
        patent_id = item.get("patent_id")
        if patent_id not in seen_patents:
            seen_patents.add(patent_id)
            deduped.append(item)
        if len(deduped) >= k:
            break

    plan_technical_element = technical_elements[0]

    # 후보를 DB에도 남긴다. 이 프로젝트의 시그니처 기능(claim → limitation 분해로
    # 중첩 후보를 짚는 것)의 산출물이 지금까지 메모리에만 있고 `ip_overlap_candidates`
    # 테이블은 계속 비어 있었다 — "무엇이 겹쳤는가"를 사후에 확인할 수가 없었다.
    # 적재 실패는 분석을 깨뜨리지 않는다(내부에서 잡고 경고만 남긴다).
    persist_ip_overlap_candidates(
        job_id=job_id,
        hypothesis_id=hypothesis_id,
        plan_technical_element=plan_technical_element,
        rows=deduped,
    )

    return [
        IPOverlapCandidate(
            candidate_id=str(uuid.uuid4()),
            job_id=job_id,
            hypothesis_id=hypothesis_id,
            limitation_id=str(item["limitation_id"]),
            evidence_id=str(item["document_id"]),
            plan_technical_element=plan_technical_element,
            lexical_score=float(item.get("lexical_score") or 0.0),
            similarity_score=float(item.get("similarity_score") or 0.0),
            hybrid_score=float(item["hybrid_score"]),
            rank=rank,
        )
        for rank, item in enumerate(deduped, start=1)
    ]
