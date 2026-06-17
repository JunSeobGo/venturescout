#!/usr/bin/env python3
"""
EC2 전용 독립 임베딩 러너.
config 모듈 불필요 — DB_DSN 환경변수만 있으면 동작.
"""
import logging
import os

import psycopg2
import psycopg2.extras
from pgvector.psycopg2 import register_vector
from sentence_transformers import SentenceTransformer

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(message)s")
logger = logging.getLogger(__name__)

DB_DSN = os.environ["DB_DSN"]
MODEL_NAME = "AI-Growth-Lab/PatentSBERTa"
BATCH_SIZE = 256


def get_conn():
    conn = psycopg2.connect(DB_DSN)
    register_vector(conn)
    return conn


def fetch_unembedded(conn, table, id_col, text_col):
    with conn.cursor(cursor_factory=psycopg2.extras.DictCursor) as cur:
        cur.execute(
            f"""
            SELECT {id_col}, {text_col}
            FROM {table}
            WHERE embedding IS NULL AND {text_col} IS NOT NULL
            ORDER BY {id_col}
            """
        )
        while True:
            rows = cur.fetchmany(BATCH_SIZE)
            if not rows:
                break
            yield [dict(r) for r in rows]


def update_batch(conn, table, id_col, pairs):
    with conn.cursor() as cur:
        psycopg2.extras.execute_batch(
            cur,
            f"UPDATE {table} SET embedding = %s WHERE {id_col} = %s",
            pairs,
            page_size=256,
        )


def run():
    logger.info(f"모델 로드: {MODEL_NAME}")
    model = SentenceTransformer(MODEL_NAME)
    conn = get_conn()

    # claim_limitations
    total_cl = 0
    for batch in fetch_unembedded(conn, "claim_limitations", "limitation_id", "normalized_text"):
        texts = [r["normalized_text"] for r in batch]
        embeddings = model.encode(texts, batch_size=BATCH_SIZE, show_progress_bar=False)
        pairs = [(emb.tolist(), r["limitation_id"]) for r, emb in zip(batch, embeddings)]
        update_batch(conn, "claim_limitations", "limitation_id", pairs)
        conn.commit()
        total_cl += len(pairs)
        logger.info(f"[claim_limitations] {total_cl}건 완료")

    # documents
    total_docs = 0
    for batch in fetch_unembedded(conn, "documents", "document_id", "clean_text"):
        texts = [r["clean_text"] for r in batch]
        embeddings = model.encode(texts, batch_size=BATCH_SIZE, show_progress_bar=False)
        pairs = [(emb.tolist(), r["document_id"]) for r, emb in zip(batch, embeddings)]
        update_batch(conn, "documents", "document_id", pairs)
        conn.commit()
        total_docs += len(pairs)
        logger.info(f"[documents] {total_docs}건 완료")

    # D3 Gate
    with conn.cursor() as cur:
        cur.execute("SELECT COUNT(*), COUNT(embedding) FROM claim_limitations")
        total, embedded = cur.fetchone()

    gate = "PASS" if total == embedded else f"FAIL (누락 {total - embedded}건)"
    logger.info(f"D3 Gate: {embedded}/{total} → {gate}")
    conn.close()

    return {"claim_limitations": total_cl, "documents": total_docs, "d3_gate": gate}


if __name__ == "__main__":
    result = run()
    logger.info(f"최종 결과: {result}")
