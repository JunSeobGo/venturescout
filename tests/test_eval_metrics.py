"""평가 지표 검증 — overclaim 실측과 라벨셋 로딩.

이전에는 `overclaim_flag`가 항상 False 하드코딩이고 `detect_overclaim()`은
호출되지 않는 죽은 코드라, "근거 없는 확정적 주장 방지"가 측정되지 않았다.
여기서 그 경로가 실제로 동작하는지 고정한다. DB·LLM을 쓰지 않는다.
"""
import json

import pytest

from agents import graph
from eval import harness, labelset
from shared.contracts import AgentRun


def _run(agent_name: str, output_json: dict, overclaim_flag: bool) -> AgentRun:
    return AgentRun(
        job_id="job-1",
        hypothesis_id="H1",
        agent_name=agent_name,
        depth="light",
        confidence="low",
        grounded_on=["ev-1"],
        output_json=output_json,
        overclaim_flag=overclaim_flag,
    )


# ── _narrative_text ──────────────────────────────────────────────────────────

def test_narrative_text_collects_nested_strings():
    text = graph._narrative_text({
        "summary": "요약",
        "risks": ["위험1", "위험2"],
        "risk_register": [{"risk": "중첩", "mitigation": "완화"}],
    })
    for expected in ("요약", "위험1", "위험2", "중첩", "완화"):
        assert expected in text


def test_narrative_text_skips_internal_and_llm_meta():
    """내부 계산값·LLM 메타는 에이전트의 주장이 아니므로 검사 대상이 아니다."""
    text = graph._narrative_text({
        "summary": "정상 문장",
        "_evidence_strength": 0.42,
        "_overclaim_phrases": ["침해 위험이 없다"],
        "llm_model_id": "침해 위험이 없다",
    })
    assert "정상 문장" in text
    assert "침해 위험이 없다" not in text


# ── _overclaim_audit ─────────────────────────────────────────────────────────

def test_overclaim_audit_detects_banned_phrase():
    found = graph._overclaim_audit({"summary": "본 아이디어는 침해 위험이 없다."})
    assert "침해 위험이 없다" in found


def test_overclaim_audit_detects_inside_nested_list():
    found = graph._overclaim_audit({"risks": [{"note": "사실상 경쟁사가 없다."}]})
    assert "경쟁사가 없다" in found


def test_overclaim_audit_clean_output_returns_empty():
    found = graph._overclaim_audit({
        "summary": "현재 수집된 근거 기준으로는 위험이 낮아 보인다.",
        "next_experiment": "추가 검증이 필요하다.",
    })
    assert found == []


# ── harness 지표 ─────────────────────────────────────────────────────────────

def test_overclaim_count_uses_flag_not_grounded_on():
    """옛 정의(grounded_on 비었는지)는 계약상 항상 0이었다. flag 기반이어야 한다."""
    runs = [
        _run("market", {"summary": "정상"}, overclaim_flag=False),
        _run("ip", {"summary": "침해 위험이 없다"}, overclaim_flag=True),
    ]
    assert harness.overclaim_count(runs) == 1
    assert harness.overclaim_rate(runs) == 0.5


def test_overclaim_rate_empty_runs_is_zero():
    assert harness.overclaim_rate([]) == 0.0


def test_overclaim_phrases_aggregates_frequency():
    runs = [
        _run("ip", {"_overclaim_phrases": ["침해 위험이 없다"]}, overclaim_flag=True),
        _run("tech", {"_overclaim_phrases": ["침해 위험이 없다", "경쟁사가 없다"]}, True),
    ]
    assert harness.overclaim_phrases(runs) == {
        "침해 위험이 없다": 2,
        "경쟁사가 없다": 1,
    }


# ── 라벨셋 ───────────────────────────────────────────────────────────────────

def _write_labelset(tmp_path, payload) -> str:
    path = tmp_path / "labels.json"
    path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
    return str(path)


def test_load_labelset_rejects_missing_field(tmp_path):
    path = _write_labelset(tmp_path, {"queries": [{"query_id": "q1", "query": "x"}]})
    with pytest.raises(ValueError, match="labels"):
        labelset.load_labelset(path)


def test_load_labelset_rejects_empty_queries(tmp_path):
    path = _write_labelset(tmp_path, {"queries": []})
    with pytest.raises(ValueError, match="queries"):
        labelset.load_labelset(path)


def test_missing_labelset_raises_with_guidance():
    with pytest.raises(FileNotFoundError, match="build_labelset"):
        labelset.load_labelset("eval/labels/does-not-exist.json")


def test_labeled_relevant_ignores_null_and_false():
    """relevant=null(미라벨)은 정답으로 세지 않는다 — precision을 부풀리면 안 된다."""
    query = {"labels": {
        "d1": {"relevant": True},
        "d2": {"relevant": False},
        "d3": {"relevant": None},
    }}
    assert labelset._labeled_relevant(query) == {"d1"}


def test_labeled_stance_picks_contradicts_only():
    query = {"labels": {
        "d1": {"stance": "contradicts"},
        "d2": {"stance": "supports"},
        "d3": {"stance": "neutral"},
    }}
    assert labelset._labeled_stance(query, "contradicts") == {"d1"}


def test_retrieval_metrics_reports_reason_when_labelset_missing(monkeypatch):
    """라벨셋이 없으면 조용한 None이 아니라 사유를 담아 돌려준다."""
    monkeypatch.setattr(labelset, "DEFAULT_LABELSET", "eval/labels/nope.json")
    result = harness.retrieval_metrics()
    assert result["precision_at_k"] is None
    assert "라벨셋 없음" in result["reason"]
