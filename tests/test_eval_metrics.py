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


# ── 순위 기반 지표 (Recall@K / MRR / NDCG@K) ─────────────────────────────────

def test_rank_metrics_all_hits():
    """top-3이 전부 정답이면 precision=1, recall=1, MRR=1, NDCG=1."""
    m = labelset._rank_metrics(["a", "b", "c"], {"a", "b", "c"})
    assert m == {
        "precision_at_k": 1.0,
        "recall_at_k_pooled": 1.0,
        "reciprocal_rank": 1.0,
        "ndcg_at_k": 1.0,
    }


def test_rank_metrics_no_hits():
    m = labelset._rank_metrics(["x", "y"], {"a"})
    assert m["precision_at_k"] == 0.0
    assert m["recall_at_k_pooled"] == 0.0
    assert m["reciprocal_rank"] == 0.0
    assert m["ndcg_at_k"] == 0.0


def test_reciprocal_rank_uses_first_hit_position():
    """첫 정답이 3등이면 RR = 1/3."""
    m = labelset._rank_metrics(["x", "y", "a", "b"], {"a", "b"})
    assert m["reciprocal_rank"] == round(1 / 3, 3)


def test_recall_counts_missed_relevant_docs():
    """정답 4개 중 top-2에 1개만 들어오면 recall = 0.25."""
    m = labelset._rank_metrics(["a", "x"], {"a", "b", "c", "d"})
    assert m["recall_at_k_pooled"] == 0.25
    assert m["precision_at_k"] == 0.5     # 보여준 2건 중 1건이 정답


def test_ndcg_rewards_higher_ranked_hits():
    """같은 개수라도 정답이 위에 있을수록 NDCG가 높아야 한다."""
    top = labelset._rank_metrics(["a", "x", "y"], {"a"})["ndcg_at_k"]
    bottom = labelset._rank_metrics(["x", "y", "a"], {"a"})["ndcg_at_k"]
    assert top > bottom
    assert top == 1.0        # 1등이 정답이면 이상적 배치


def test_recall_is_none_when_nothing_labeled_relevant():
    """정답 미라벨이면 0.0이 아니라 None — 0.0은 '다 놓쳤다'로 오해된다."""
    m = labelset._rank_metrics(["a", "b"], set())
    assert m["recall_at_k_pooled"] is None


def test_mean_skips_none_and_returns_none_when_empty():
    assert labelset._mean([1.0, None, 0.5]) == 0.75
    assert labelset._mean([None, None]) is None


# ── 채점 불가 쿼리 제외 + 천장 ────────────────────────────────────────────────
# 정답이 하나도 라벨링되지 않은 쿼리는 무엇을 검색해도 0.0이라, 평균에 넣으면
# 올바른 동작(관련 문서가 없으니 못 찾음)을 0점으로 처벌한다. 초판 라벨셋의
# saas-h2가 그랬고 7개 중 하나가 0.0 고정으로 평균을 끌어내렸다.

def _fake_eval(monkeypatch, queries, retrieved_by_qid):
    """retrieve를 가짜로 바꿔 DB 없이 evaluate_retrieval을 돌린다."""
    monkeypatch.setattr(
        labelset, "load_labelset", lambda path=None: {"queries": queries}
    )
    monkeypatch.setattr(
        labelset, "_retrieve_ids",
        lambda query, k: retrieved_by_qid[query["query_id"]],
    )
    return labelset.evaluate_retrieval()


def _q(qid, labels):
    return {"query_id": qid, "query": "q", "axis": "customer_problem", "labels": labels}


def test_query_with_no_relevant_labels_is_excluded(monkeypatch):
    result = _fake_eval(
        monkeypatch,
        [
            _q("good", {"a": {"relevant": True}, "b": {"relevant": False}}),
            _q("none", {"c": {"relevant": False}, "d": {"relevant": False}}),
        ],
        {"good": ["a"], "none": ["c"]},
    )
    assert result["queries"] == 2
    assert result["scored_queries"] == 1
    assert result["unscorable_queries"] == ["none"]
    # 'none'을 포함했다면 (1.0 + 0.0) / 2 = 0.5로 깎였을 것이다
    assert result["precision_at_k"] == 1.0


def test_unscorable_queries_are_named_not_silently_dropped(monkeypatch):
    result = _fake_eval(
        monkeypatch,
        [_q("a", {"x": {"relevant": True}}), _q("b", {"y": {"relevant": None}})],
        {"a": ["x"], "b": ["y"]},
    )
    assert result["unscorable_queries"] == ["b"]


def test_precision_ceiling_reflects_scarce_labels(monkeypatch):
    """정답이 2건뿐이면 P@5의 천장은 0.4다 — 실측 0.4는 만점이지 실패가 아니다."""
    labels = {f"d{i}": {"relevant": i < 2} for i in range(10)}
    result = _fake_eval(monkeypatch, [_q("scarce", labels)], {"scarce": ["d0", "d1"]})
    assert result["precision_at_k_ceiling"] == 0.4


def test_ceiling_is_one_when_labels_are_plentiful(monkeypatch):
    labels = {f"d{i}": {"relevant": True} for i in range(9)}
    result = _fake_eval(monkeypatch, [_q("many", labels)], {"many": list(labels)[:5]})
    assert result["precision_at_k_ceiling"] == 1.0
