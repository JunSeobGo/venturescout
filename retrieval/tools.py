"""Track C 에이전트가 사용하는 mock 검색 도구.

운영 환경에서는 Track B가 documents, evidence_items, claim_limitations에 대한
하이브리드 검색으로 본문을 교체한다. 함수 시그니처는 이미 9개 테이블
계약을 반환하므로, 에이전트는 병렬로 개발할 수 있다.

실제 데이터 전환 지점:
- retrieve()는 MOCK_EVIDENCE 필터링 대신 PostgreSQL/pgvector/tsvector 하이브리드 검색을 호출해야 한다.
- vector_search()는 MOCK_IP_CANDIDATES 필터링 대신 claim_limitations embedding 검색과 lexical 검색 결과를 조합해야 한다.
- 반환 타입은 EvidenceItem, IPOverlapCandidate로 유지해야 graph와 agent contract가 깨지지 않는다.
"""

from __future__ import annotations

import os

from agents.logger import get_logger
from agents.db_workflow import create_evidence_item, get_hypothesis_id_by_code
from agents.mock_data import MOCK_EVIDENCE, MOCK_IP_CANDIDATES
from retrieval.pgvector_search import search_documents_by_vector
from shared.contracts import EvidenceItem, IPOverlapCandidate

logger = get_logger("retrieval.tools")


def retrieve(
    hypothesis_id: str,
    query: str,
    *,
    job_id: str = "job_mock_001",
    k: int = 5,
) -> list[EvidenceItem]:
    """가설과 관련된 evidence_items 행을 반환한다."""

    logger.debug(f"[retrieve] hypothesis_id={hypothesis_id}, query='{query}', k={k}")

    if os.getenv("AGENT_DATA_PROVIDER", "mock").lower() == "db":
        return _retrieve_from_db(
            hypothesis_id=hypothesis_id,
            query=query,
            job_id=job_id,
            k=k,
        )

    # 실제 데이터 전환 지점:
    # 여기서 MOCK_EVIDENCE를 순회하지 말고, documents/evidence_items를 대상으로
    # job_id + hypothesis_id + query 기반 하이브리드 검색을 실행한다.
    # 검색 결과는 아래처럼 EvidenceItem으로 변환해서 반환하면 graph 코드는 그대로 쓸 수 있다.
    matched = [
        EvidenceItem(
            job_id=job_id,
            **item,
        )
        for item in MOCK_EVIDENCE
        if item["hypothesis_id"] == hypothesis_id
    ]

    result = matched[:k]
    logger.info(f"retrieve 완료: {len(result)}개 근거 수집 ({hypothesis_id})")
    for item in result[:3]:
        logger.debug(f"  - {item.evidence_id}: {item.stance}")

    return result


def _retrieve_from_db(
    *,
    hypothesis_id: str,
    query: str,
    job_id: str,
    k: int,
) -> list[EvidenceItem]:
    """DB documents 검색 결과를 evidence_items에 저장한 뒤 EvidenceItem으로 반환한다."""

    db_hypothesis_id = hypothesis_id
    if hypothesis_id.startswith("H"):
        db_hypothesis_id = get_hypothesis_id_by_code(job_id=job_id, code=hypothesis_id)

    documents = search_documents_by_vector(query, top_k=k)
    evidence_rows = [
        create_evidence_item(
            job_id=job_id,
            hypothesis_id=db_hypothesis_id,
            document=document,
            stance="neutral",
        )
        for document in documents
    ]

    result = [
        EvidenceItem(
            evidence_id=row["evidence_id"],
            job_id=job_id,
            hypothesis_id=db_hypothesis_id,
            document_id=row["document_id"],
            source_type=row.get("source_type") or "unknown",
            evidence_text=row["evidence_text"],
            stance=row.get("stance") or "neutral",
            relevance_score=float(row.get("relevance_score") or 0.0),
            reliability_score=float(row.get("reliability_score") or 0.0),
        )
        for row in evidence_rows
    ]

    logger.info(f"DB retrieve 완료: {len(result)}개 evidence_items 생성 ({hypothesis_id})")
    for item in result[:3]:
        logger.debug(f"  - {item.evidence_id}: {item.document_id}")
    return result


def vector_search(
    technical_elements: list[str],
    *,
    job_id: str = "job_mock_001",
    hypothesis_id: str = "H5",
    k: int = 10,
) -> list[IPOverlapCandidate]:
    """기계가 생성한 IP 중첩 후보를 반환한다. 법적 판단은 아니다."""

    logger.debug(f"[vector_search] hypothesis_id={hypothesis_id}, elements={technical_elements}, k={k}")

    if os.getenv("AGENT_DATA_PROVIDER", "mock").lower() == "db":
        logger.info("DB vector_search: claim_limitations 연결 전이므로 IP 후보는 빈 목록을 반환한다.")
        return []

    # 실제 데이터 전환 지점:
    # 여기서 MOCK_IP_CANDIDATES를 읽지 말고, technical_elements를 query로 삼아
    # claim_limitations.embedding 벡터 검색 + normalized_text lexical 검색을 수행한다.
    # 그 결과를 ip_overlap_candidates에 저장/조회한 뒤 IPOverlapCandidate로 반환한다.
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

    result = matched[:k]

    # IP 위험도별 분류
    high_watch = [c for c in result if c.hybrid_score >= 0.78]
    watch = [c for c in result if 0.70 <= c.hybrid_score < 0.78]
    low_watch = [c for c in result if c.hybrid_score < 0.70]

    logger.info(f"vector_search 완료: {len(result)}개 IP 후보")
    logger.info(f"  high_watch: {len(high_watch)}, watch: {len(watch)}, low_watch: {len(low_watch)}")
    for item in result[:3]:
        logger.debug(f"  - {item.candidate_id}: {item.plan_technical_element} (score={item.hybrid_score:.2f})")

    return result
