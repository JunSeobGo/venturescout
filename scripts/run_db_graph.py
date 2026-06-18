"""
DB documents/evidence_items/agent_runs를 사용하는 LangGraph 실행 스크립트.

현재 단계:
- ideas, analysis_jobs, hypotheses를 DB에 생성한다.
- graph 실행 중 retrieve()가 documents를 검색하고 evidence_items를 생성한다.
- graph 실행 후 agent_runs와 analysis_jobs decision을 DB에 기록한다.

주의:
- query embedding은 아직 mock vector다.
- LLM 호출은 AGENT_LLM_PROVIDER=bedrock일 때만 시도한다.
"""

from __future__ import annotations

import json
import os
import sys

PROJECT_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

os.environ.setdefault("AGENT_DATA_PROVIDER", "db")
os.environ.setdefault("PYTHONIOENCODING", "utf-8")

from agents.db_workflow import (
    create_analysis_job,
    create_hypotheses,
    create_idea,
    get_hypothesis_id_by_code,
    log_agent_run,
    update_analysis_job,
)
from agents.graph import build_graph
from agents.mock_data import MOCK_HYPOTHESES, MOCK_RAW_INPUT, MOCK_STRUCTURED_IDEA


def _db_hypothesis_id(job_id: str, hypothesis_id: str | None) -> str | None:
    """graph 내부 H1~H5 code를 DB uuid hypothesis_id로 바꾼다."""

    if not hypothesis_id:
        return None
    if hypothesis_id.startswith("H"):
        return get_hypothesis_id_by_code(job_id=job_id, code=hypothesis_id)
    return hypothesis_id


def main() -> None:
    raw_input = os.getenv("VENTURESCOUT_RAW_INPUT", MOCK_RAW_INPUT)

    idea_id = create_idea(raw_input, structured=MOCK_STRUCTURED_IDEA)
    job_id = create_analysis_job(idea_id)
    create_hypotheses(
        job_id=job_id,
        idea_id=idea_id,
        hypotheses=MOCK_HYPOTHESES,
    )

    app = build_graph()
    result = app.invoke(
        {
            "job_id": job_id,
            "idea_id": idea_id,
            "raw_input": raw_input,
        }
    )

    agent_run_ids = []
    for run in result.get("agent_runs", []):
        agent_run_ids.append(
            log_agent_run(
                job_id=job_id,
                agent_name=run.agent_name,
                hypothesis_id=_db_hypothesis_id(job_id, run.hypothesis_id),
                grounded_on=run.grounded_on,
                output_json=run.output_json,
                confidence=run.confidence,
                depth=run.depth,
                model_name=run.model_name or "graph",
            )
        )

    critic = result.get("critic")
    update_analysis_job(
        job_id=job_id,
        status="done",
        current_stage="completed",
        progress_pct=100,
        decision=result.get("decision"),
        decision_summary=critic.summary if critic else None,
    )

    print("DB graph run completed")
    print("idea_id:", idea_id)
    print("job_id:", job_id)
    print("decision:", result.get("decision"))
    print("agent_runs_logged:", len(agent_run_ids))
    print("evidence_items_in_state:", len(result.get("evidence_items", {})))
    print("final_report:")
    print(json.dumps(result.get("final_report"), ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
