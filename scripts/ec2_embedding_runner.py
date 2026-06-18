#!/usr/bin/env python3
"""
EC2 전용 독립 임베딩 러너.
config 모듈 불필요 — DB_DSN 환경변수만 있으면 동작.

크로스리전(EC2=서울, RDS=도쿄) 환경에서 장시간 연결이 끊기는 문제 방지:
  - TCP keepalive로 유휴 연결 단절 방지
  - 연결이 끊겨도 자동 재연결·재개 (쿼리가 embedding IS NULL 기준이라 이어서 진행)
"""
import logging
import os
import time
from typing import Iterator

import psycopg2
import psycopg2.extras
from pgvector.psycopg2 import register_vector
from sentence_transformers import SentenceTransformer

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(message)s")
logger = logging.getLogger(__name__)

DB_DSN = os.environ["DB_DSN"]
MODEL_NAME = "AI-Growth-Lab/PatentSBERTa"
EMBED_BATCH = 64

# 크로스리전 유휴 연결 단절 방지 + 끊김 빠른 감지
KEEPALIVE_OPTS = dict(
    keepalives=1,
    keepalives_idle=30,      # 30초 유휴 시 keepalive 시작
    keepalives_interval=10,  # 10초 간격 재전송
    keepalives_count=5,      # 5회 실패 시 끊김 판정
    connect_timeout=30,
)


def connect():
    return psycopg2.connect(DB_DSN, **KEEPALIVE_OPTS)


def get_write_conn():
    conn = connect()
    register_vector(conn)
    return conn


def fetch_unembedded(
    table: str, id_col: str, text_col: str
) -> Iterator[list[dict]]:
    """server-side named 커서로 스트리밍 (OOM 방지)."""
    read_conn = connect()
    try:
        with read_conn.cursor(
            name=f"fetch_{table}", cursor_factory=psycopg2.extras.DictCursor
        ) as cur:
            cur.itersize = EMBED_BATCH
            cur.execute(
                f"""
                SELECT {id_col}, {text_col}
                FROM {table}
                WHERE embedding IS NULL AND {text_col} IS NOT NULL
                ORDER BY {id_col}
                """
            )
            batch: list[dict] = []
            for row in cur:
                batch.append(dict(row))
                if len(batch) >= EMBED_BATCH:
                    yield batch
                    batch = []
            if batch:
                yield batch
    finally:
        read_conn.close()


def update_batch(conn, table: str, id_col: str, pairs: list[tuple]) -> None:
    with conn.cursor() as cur:
        psycopg2.extras.execute_batch(
            cur,
            f"UPDATE {table} SET embedding = %s WHERE {id_col} = %s",
            pairs,
            page_size=256,
        )


def embed_table(model, write_conn, table, id_col, text_col):
    """한 테이블 전체 임베딩. 연결이 끊기면 재연결 후 남은 것부터 재개."""
    total = 0
    attempt = 0
    while True:
        try:
            had_rows = False
            for batch in fetch_unembedded(table, id_col, text_col):
                had_rows = True
                texts = [r[text_col] for r in batch]
                embeddings = model.encode(
                    texts, batch_size=EMBED_BATCH, show_progress_bar=False
                )
                pairs = [(emb.tolist(), r[id_col]) for r, emb in zip(batch, embeddings)]
                update_batch(write_conn, table, id_col, pairs)
                write_conn.commit()
                total += len(pairs)
                logger.info(f"[{table}] {total}건 완료")
            # 더 이상 처리할 행이 없으면 완료
            if not had_rows:
                break
            # 커서가 끝까지 돌았으면 완료 (남은 NULL 없음)
            break
        except (psycopg2.OperationalError, psycopg2.InterfaceError) as e:
            attempt += 1
            wait = min(60, 5 * attempt)
            logger.warning(
                f"[{table}] 연결 끊김({type(e).__name__}: {e}). "
                f"{wait}초 후 재연결·재개 (시도 {attempt})"
            )
            time.sleep(wait)
            # write_conn 재생성
            try:
                write_conn.close()
            except Exception:
                pass
            write_conn = get_write_conn()
            # while 루프가 fetch_unembedded를 다시 호출 → embedding IS NULL 기준으로 재개
    return total, write_conn


def run():
    logger.info(f"모델 로드: {MODEL_NAME}")
    model = SentenceTransformer(MODEL_NAME)
    write_conn = get_write_conn()

    total_cl, write_conn = embed_table(
        model, write_conn, "claim_limitations", "limitation_id", "normalized_text"
    )
    total_docs, write_conn = embed_table(
        model, write_conn, "documents", "document_id", "clean_text"
    )

    # D3 Gate
    with write_conn.cursor() as cur:
        cur.execute("SELECT COUNT(*), COUNT(embedding) FROM claim_limitations")
        total, embedded = cur.fetchone()

    gate = "PASS" if total == embedded else f"FAIL (누락 {total - embedded}건)"
    logger.info(f"D3 Gate: {embedded}/{total} → {gate}")
    write_conn.close()

    return {"claim_limitations": total_cl, "documents": total_docs, "d3_gate": gate}


if __name__ == "__main__":
    result = run()
    logger.info(f"최종 결과: {result}")
