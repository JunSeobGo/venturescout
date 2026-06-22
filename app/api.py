"""
Track D — FastAPI (비동기 job + 스트리밍).
/analyze 는 최종 JSON + 스트리밍 이벤트 둘 다 지원하도록 설계.
얇은 클라이언트 원칙: 에이전트 로직은 graph에, API는 호출+이벤트 중계만.
"""
from __future__ import annotations
import json
from fastapi import FastAPI
from fastapi.responses import StreamingResponse
from pydantic import BaseModel

<<<<<<< Updated upstream
=======
from agents.graph import build_graph
from agents.input_validation import InsufficientInputError, validate_input_detail
from config import config

>>>>>>> Stashed changes
app = FastAPI(title="VentureScout API")


class AnalyzeRequest(BaseModel):
    idea: str


<<<<<<< Updated upstream
=======
def _sse(payload: dict) -> str:
    """SSE 한 이벤트로 직렬화 (ensure_ascii=False → 한글 그대로)."""
    return f"data: {json.dumps(payload, ensure_ascii=False, default=str)}\n\n"


# ── job_id 라이프사이클 (D 소유 진입점) ───────────────────────────────────────
# DATABASE_URL은 .env에서 옴(검증은 로컬 docker postgres로). config.py(B)가 머지되면
# 그쪽 db_dsn으로 교체 가능 — 지금은 D가 독립적으로 최소 연결만 갖는다.

def _db_conn():
    return psycopg2.connect(
        config.db_dsn,
        connect_timeout=config.db_connect_timeout,
    )


def _create_job(raw_input: str) -> tuple[str, str]:
    """ideas → analysis_jobs 행을 만들고 (job_id, idea_id) 반환. status=running.

    analysis_jobs.idea_id가 NOT NULL FK라 ideas를 먼저 만든다(스키마: ideas←analysis_jobs).
    """
    conn = _db_conn()
    try:
        with conn.cursor() as cur:
            cur.execute(
                "INSERT INTO ideas (raw_input) VALUES (%s) RETURNING idea_id",
                (raw_input,),
            )
            idea_id = str(cur.fetchone()[0])
            cur.execute(
                "INSERT INTO analysis_jobs (idea_id, status, started_at) "
                "VALUES (%s, 'running', now()) RETURNING job_id",
                (idea_id,),
            )
            job_id = str(cur.fetchone()[0])
        conn.commit()
        return job_id, idea_id
    finally:
        conn.close()


def _finish_job(job_id: str, status: str, decision: str | None, summary: str | None) -> None:
    """분석 종료 시 analysis_jobs 상태/결정 업데이트 (done|failed)."""
    conn = _db_conn()
    try:
        with conn.cursor() as cur:
            cur.execute(
                "UPDATE analysis_jobs SET status=%s, decision=%s, decision_summary=%s, "
                "finished_at=now() WHERE job_id=%s",
                (status, decision, summary, job_id),
            )
        conn.commit()
    finally:
        conn.close()


>>>>>>> Stashed changes
@app.get("/health")
def health():
    return {"status": "ok"}


@app.post("/analyze")
async def analyze(req: AnalyzeRequest):
    """SSE 스트리밍: 에이전트 단계 → 최종 리포트."""
    async def event_stream():
        # TODO(D): agents.graph.build_graph().astream_events 로 실제 단계 중계
        for stage in ["structuring", "market", "ip", "critic"]:
            yield f"data: {json.dumps({'stage': stage, 'status': 'running'})}\n\n"
        yield f"data: {json.dumps({'type': 'report', 'decision': 'more_research'})}\n\n"

    return StreamingResponse(event_stream(), media_type="text/event-stream")
