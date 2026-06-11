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

app = FastAPI(title="VentureScout API")


class AnalyzeRequest(BaseModel):
    idea: str


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
