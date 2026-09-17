"""
하이브리드 검색: pgvector (semantic) + tsvector (keyword) 가중합.

- search_claim_limitations: claim_limitations(시그니처 검색 단위) → ⑤ IP 에이전트
- search_documents:          documents(evidence 검색 단위)        → ② Market, ③ Competitor

성능 설계 (2단계 후보생성):
  ① vec CTE  — `ORDER BY embedding <=> v LIMIT pool` → HNSW 인덱스 사용 (순수 거리)
  ① kw  CTE  — `WHERE tsv @@ q ORDER BY ts_rank LIMIT pool` → GIN(tsvector) 인덱스 사용
  ② 두 후보 합집합(≤2*pool 행)에만 합성 hybrid_score 계산·정렬 → top_k
합성식을 전체 테이블 ORDER BY에 직접 걸면(이전 방식) 인덱스를 못 타 풀스캔이 된다.
후보를 인덱스로 먼저 좁히는 게 핵심. (HNSW 인덱스가 (재)생성돼 있어야 효과 발생)
"""
from __future__ import annotations

import psycopg2
import psycopg2.extras
from pgvector.psycopg2 import register_vector

from config import config
from pipeline.embedder import PatentEmbedder
from pipeline.persistence import create_ip_overlap_candidates


class HybridSearcher:
    def __init__(self):
        self.embedder = PatentEmbedder()

    def _new_conn(self):
        # RDS가 외부에서 끊은 경우 self._conn.closed가 False로 남아
        # "SSL SYSCALL error: EOF detected"가 발생하므로 매 쿼리마다 새 연결을 사용한다.
        conn = psycopg2.connect(
            config.db_dsn,
            connect_timeout=config.db_connect_timeout,
        )
        register_vector(conn)
        return conn

    @staticmethod
    def _fusion_expr() -> str:
        """벡터 항과 키워드 항을 하나의 점수로 합치는 식(SQL 조각).

        **왜 방식이 여러 개인가.** 기본 `weighted`는 두 원점수에 0.6/0.4를 곱해
        더한다. 그런데 두 항은 스케일이 다르다 — 실측으로 상위 20건에서
        `1-cosine`은 폭이 0.17인데 `ts_rank`는 0.73~0.84다. 순위를 가르는 건
        절대값이 아니라 폭이므로, 0.4를 곱한 키워드가 실제로는 순위 변별의
        74~77%를 차지한다(seed_review만 33%). **명목 가중치가 실제 가중치가
        아니고, 코퍼스마다 달라진다.**

        - weighted : 기존 동작. 호환을 위해 기본값으로 둔다
        - minmax   : 후보군 안에서 각 항을 0~1로 편 뒤 가중합. 0.6/0.4가 비로소
                     의도대로 동작한다. 다만 후보군 구성에 따라 값이 흔들린다
        - rrf      : 원점수를 버리고 **순위만** 쓴다(1/(k+rank)). 스케일 불일치가
                     정의상 사라지고 튜닝할 상수가 사실상 없다. 하이브리드 검색의
                     표준 해법

        한쪽 검색에만 걸린 문서는 반대쪽 rank/score가 NULL이다 — RRF는 그 항을
        0으로 보고(합집합의 기본 성질), minmax도 0으로 채운다.
        """
        mode = config.fusion_mode
        vw, kw = config.vector_weight, config.keyword_weight
        if mode == "rrf":
            k = config.rrf_k
            return (f"COALESCE({vw} / ({k} + vec_rank), 0)"
                    f" + COALESCE({kw} / ({k} + kw_rank), 0)")
        if mode == "minmax":
            # 후보군 전체에 대한 min/max를 윈도우로 구해 각 항을 0~1로 편다.
            # 분모가 0이면(모든 값이 같으면) 그 항은 변별력이 없으므로 0으로 둔다.
            return (
                f"{vw} * COALESCE("
                "  (vec_score - MIN(vec_score) OVER ()) /"
                "  NULLIF(MAX(vec_score) OVER () - MIN(vec_score) OVER (), 0), 0)"
                f" + {kw} * COALESCE("
                "  (COALESCE(kw_score,0) - MIN(COALESCE(kw_score,0)) OVER ()) /"
                "  NULLIF(MAX(COALESCE(kw_score,0)) OVER ()"
                "         - MIN(COALESCE(kw_score,0)) OVER (), 0), 0)"
            )
        return f"{vw} * vec_score + {kw} * COALESCE(kw_score, 0)"

    @staticmethod
    def _candidate_pool(top_k: int) -> int:
        """인덱스로 뽑을 후보 풀 크기(vec/kw 각각). top_k보다 넉넉히 — 후보생성 뒤
        필터(source_type·independent_only·code_filter)로 줄어도 top_k를 채우게."""
        return max(top_k * 10, 200)

    def search_claim_limitations(
        self,
        query: str,
        top_k: int | None = None,
        code_filter: str | None = None,   # CPC(USPTO) 또는 IPC(KIPRIS) 접두사 → documents.meta
        independent_only: bool = False,
    ) -> list[dict]:
        """
        특허 청구항 limitation 하이브리드 검색 (시그니처: 청구항 중첩 분석).

        Args:
            query:            기술 요소 키워드 (영어)
            top_k:            반환 수 (기본값 config.top_k_fetch)
            code_filter:      분류코드 접두사. ex) 'G06F', 'H04L' (documents.meta->>'cpc_code')
            independent_only: True면 독립항(상위 patent_claims.is_independent)만

        Returns:
            limitation_id, claim_id, document_id, patent_id, title, normalized_text,
            claim_no, is_independent, meta, hybrid_score 포함 dict 리스트
        """
        top_k = top_k or config.top_k_fetch
        pool = self._candidate_pool(top_k)
        query_vec = self.embedder.embed(query)
        ts_lang = "korean" if config.is_korean else "english"

        # 필터는 후보생성(인덱스) 뒤, 작은 후보군에만 적용한다.
        outer_conditions = ["d.source_type = 'patent'"]
        params: dict = dict(vec=query_vec.tolist(), query=query, top_k=top_k, pool=pool)

        if independent_only:
            outer_conditions.append("pc.is_independent = TRUE")
        if code_filter:
            outer_conditions.append("d.meta->>'cpc_code' LIKE %(code)s")
            params["code"] = f"{code_filter}%"

        outer_where = " AND ".join(outer_conditions)

        sql = f"""
            WITH vec AS (                          -- ① 벡터 후보 (HNSW)
                SELECT limitation_id
                FROM claim_limitations
                WHERE embedding IS NOT NULL
                ORDER BY embedding <=> %(vec)s::vector
                LIMIT %(pool)s
            ),
            kw AS (                                -- ① 키워드 후보 (GIN/tsvector)
                SELECT limitation_id
                FROM claim_limitations
                WHERE to_tsvector('{ts_lang}', normalized_text)
                      @@ plainto_tsquery('{ts_lang}', %(query)s)
                ORDER BY ts_rank(
                    to_tsvector('{ts_lang}', normalized_text),
                    plainto_tsquery('{ts_lang}', %(query)s)
                ) DESC
                LIMIT %(pool)s
            ),
            cand AS (                              -- 후보 합집합 (≤ 2*pool)
                SELECT limitation_id FROM vec
                UNION
                SELECT limitation_id FROM kw
            )
            SELECT                                 -- ② 후보군에만 합성식 계산
                cl.limitation_id,
                cl.claim_id,
                cl.normalized_text,
                cl.limitation_order,
                pc.claim_no,
                pc.is_independent,
                pc.document_id,
                d.ext_id  AS patent_id,
                d.title,
                d.meta,
                1 - (cl.embedding <=> %(vec)s::vector)              AS similarity_score,
                ts_rank(
                    to_tsvector('{ts_lang}', cl.normalized_text),
                    plainto_tsquery('{ts_lang}', %(query)s)
                )                                                    AS lexical_score,
                (
                    {config.vector_weight}  * (1 - (cl.embedding <=> %(vec)s::vector))
                  + {config.keyword_weight} * ts_rank(
                        to_tsvector('{ts_lang}', cl.normalized_text),
                        plainto_tsquery('{ts_lang}', %(query)s)
                    )
                )                                                    AS hybrid_score
            FROM cand
            JOIN claim_limitations cl ON cl.limitation_id = cand.limitation_id
            JOIN patent_claims pc ON cl.claim_id = pc.claim_id
            JOIN documents d      ON pc.document_id = d.document_id
            WHERE {outer_where}
            ORDER BY hybrid_score DESC
            LIMIT %(top_k)s
        """

        conn = self._new_conn()
        try:
            with conn.cursor(cursor_factory=psycopg2.extras.DictCursor) as cur:
                cur.execute(sql, params)
                return [dict(r) for r in cur.fetchall()]
        finally:
            conn.close()

    def find_ip_overlap_candidates(
        self,
        *,
        job_id: str,
        hypothesis_id: str,
        plan_technical_element: str,
        top_k: int | None = None,
        code_filter: str | None = None,
        independent_only: bool = True,
    ) -> list[str]:
        """
        plan_technical_element(자사 계획의 기술 요소 설명)로 claim_limitations를 검색하고,
        그 결과를 evidence_items + ip_overlap_candidates에 적재한다.

        Schema_explains.md ⑨: B(기계)가 candidate를 produce, ⑤ IP 에이전트(C)가 read.
        중첩 여부(supports/contradicts) 판단은 하지 않음 — evidence_items.stance='neutral'.

        Returns: candidate_id 리스트 (rank 순, 빈 검색결과면 [])
        """
        candidates = self.search_claim_limitations(
            plan_technical_element,
            top_k=top_k,
            code_filter=code_filter,
            independent_only=independent_only,
        )
        if not candidates:
            return []

        conn = self._new_conn()
        try:
            result = create_ip_overlap_candidates(
                conn,
                job_id=job_id,
                hypothesis_id=hypothesis_id,
                plan_technical_element=plan_technical_element,
                candidates=candidates,
            )
        finally:
            conn.close()
        return result

    def search_documents(
        self,
        query: str,
        top_k: int | None = None,
        source_types: list[str] | None = None,
    ) -> list[dict]:
        """
        documents 하이브리드 검색 (evidence 후보).
        ② Market, ③ Competitor 에이전트가 시드/웹 데이터 검색에 사용.

        Args:
            query:        검색 쿼리
            top_k:        반환 수
            source_types: ['seed_review', 'seed_competitor', 'seed_pricing', 'web', 'patent'] 중 선택

        Returns:
            document_id, source_type, ext_id, title, clean_text, meta,
            reliability_score, freshness_score, hybrid_score 포함 dict 리스트
        """
        top_k = top_k or config.top_k_fetch
        pool = self._candidate_pool(top_k)
        query_vec = self.embedder.embed(query)
        ts_lang = "korean" if config.is_korean else "english"

        params: dict = dict(vec=query_vec.tolist(), query=query, top_k=top_k, pool=pool)
        # ⚠️ source_type 필터는 후보생성 CTE 안에 둔다(후보 뒤 필터 금지).
        #    seed_* 문서는 각 30건 정도라, 전체 top-N 후보(대부분 특허 5만건)를 뽑고
        #    나중에 필터하면 후보에 안 들어가 0건이 된다 → grounded_on 비어 AgentRun 검증 실패.
        src_filter = ""
        if source_types:
            src_filter = " AND source_type = ANY(%(source_types)s)"
            params["source_types"] = source_types

        sql = f"""
            WITH vec AS (                          -- ① 벡터 후보 (HNSW, 출처 스코프 포함)
                SELECT document_id,
                       ROW_NUMBER() OVER (ORDER BY embedding <=> %(vec)s::vector) AS rnk
                FROM documents
                WHERE embedding IS NOT NULL{src_filter}
                ORDER BY embedding <=> %(vec)s::vector
                LIMIT %(pool)s
            ),
            kw AS (                                -- ① 키워드 후보 (GIN/tsvector, 출처 스코프 포함)
                SELECT document_id,
                       ROW_NUMBER() OVER (
                           ORDER BY ts_rank(
                               to_tsvector('{ts_lang}', clean_text),
                               plainto_tsquery('{ts_lang}', %(query)s)
                           ) DESC
                       ) AS rnk
                FROM documents
                WHERE clean_text IS NOT NULL{src_filter}
                  AND to_tsvector('{ts_lang}', clean_text)
                      @@ plainto_tsquery('{ts_lang}', %(query)s)
                ORDER BY ts_rank(
                    to_tsvector('{ts_lang}', clean_text),
                    plainto_tsquery('{ts_lang}', %(query)s)
                ) DESC
                LIMIT %(pool)s
            ),
            cand AS (
                SELECT document_id FROM vec
                UNION
                SELECT document_id FROM kw
            ),
            scored AS (                            -- ② 후보군에만 두 항을 따로 계산
                SELECT
                    d.document_id, d.source_type, d.ext_id, d.title, d.clean_text,
                    d.meta, d.reliability_score, d.freshness_score,
                    (1 - (d.embedding <=> %(vec)s::vector)) AS vec_score,
                    ts_rank(
                        to_tsvector('{ts_lang}', d.clean_text),
                        plainto_tsquery('{ts_lang}', %(query)s)
                    ) AS kw_score,
                    vec.rnk AS vec_rank,
                    kw.rnk  AS kw_rank
                FROM cand
                JOIN documents d ON d.document_id = cand.document_id
                LEFT JOIN vec ON vec.document_id = cand.document_id
                LEFT JOIN kw  ON kw.document_id  = cand.document_id
            )
            SELECT *, {self._fusion_expr()} AS hybrid_score
            FROM scored
            ORDER BY hybrid_score DESC
            LIMIT %(top_k)s
        """

        conn = self._new_conn()
        try:
            with conn.cursor(cursor_factory=psycopg2.extras.DictCursor) as cur:
                cur.execute(sql, params)
                return [dict(r) for r in cur.fetchall()]
        finally:
            conn.close()
