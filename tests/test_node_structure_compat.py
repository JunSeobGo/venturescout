from agents.nodes.critic_node import critic_node
from agents.nodes.ip_node import ip_node
from agents.nodes.structuring_node import structuring_node
from agents.nodes.tech_node import tech_node


AGENT_RESULT_KEYS = {
    "agent_name",
    "hypothesis_id",
    "depth",
    "confidence",
    "grounded_on",
    "summary",
    "key_findings",
    "risks",
    "recommendations",
    "needs_more_research",
    "output_json",
}


def test_structuring_preserves_legacy_state_and_hypothesis_shape(monkeypatch):
    monkeypatch.setenv("AGENT_LLM_PROVIDER", "mock")
    state = {
        "raw_input": (
            "title: Retail inventory forecasting\n"
            "target customer: Local retailers\n"
            "problem: Stockouts and waste recur\n"
            "solution: Forecast reorder quantities\n"
            "technical elements: demand forecasting, time series analysis"
        )
    }

    result = structuring_node(state)

    assert result is state
    assert result["title"] == "Retail inventory forecasting"
    assert result["technical_elements"] == [
        "demand forecasting",
        "time series analysis",
    ]
    assert [item["code"] for item in result["hypotheses"]] == ["H1", "H4", "H5"]
    assert set(result["hypotheses"][0]) == {
        "hypothesis_id",
        "code",
        "axis",
        "statement",
        "confidence",
        "next_validation",
        "supporting_evidence",
        "contradicting_evidence",
    }


def test_agent_nodes_preserve_legacy_result_envelopes(monkeypatch):
    monkeypatch.setenv("AGENT_LLM_PROVIDER", "mock")
    state = {
        "job_id": "job_test",
        "technical_elements": ["demand forecasting"],
        "hypotheses": [
            {
                "hypothesis_id": "uuid-h4",
                "code": "H4",
            },
            {
                "hypothesis_id": "uuid-h5",
                "code": "H5",
            },
        ],
        "evidence_items": {
            "ev_h4": {
                "evidence_id": "ev_h4",
                "hypothesis_id": "uuid-h4",
                "evidence_text": "Forecasting implementation case",
                "stance": "supports",
            }
        },
        "ip_overlap_candidates": [
            {
                "candidate_id": "candidate_1",
                "job_id": "job_test",
                "hypothesis_id": "uuid-h5",
                "limitation_id": "limitation_1",
                "evidence_id": "ev_h5",
                "plan_technical_element": "demand forecasting",
                "lexical_score": 0.7,
                "similarity_score": 0.8,
                "hybrid_score": 0.79,
                "rank": 1,
            }
        ],
    }

    tech_node(state)
    ip_node(state)

    assert set(state["tech_result"]) == AGENT_RESULT_KEYS | {
        "groundedness_score",
        "overclaim_flag",
        "validation_errors",
    }
    assert set(state["tech_result"]["output_json"]) == {
        "feasibility_signal",
        "required_models_or_apis",
        "cost_risks",
    }

    assert set(state["ip_result"]) == AGENT_RESULT_KEYS | {
        "groundedness_score",
        "overclaim_flag",
        "validation_errors",
    }
    assert set(state["ip_result"]["output_json"]) == {
        "overlap_signal",
        "high_overlap_elements",
        "design_around_options",
        "legal_guardrail_note",
        "candidates",
    }

    state["market_result"] = {
        **state["tech_result"],
        "agent_name": "market",
        "hypothesis_id": "H1",
        "confidence": "mid",
    }
    state["competitor_result"] = {
        **state["tech_result"],
        "agent_name": "competitor",
        "hypothesis_id": "H2",
        "confidence": "mid",
    }
    state["bm_result"] = {
        **state["tech_result"],
        "agent_name": "bm",
        "hypothesis_id": "H3",
        "confidence": "mid",
    }

    critic_node(state)

    assert set(state["critic_result"]) == AGENT_RESULT_KEYS
    assert set(state["critic_result"]["output_json"]) == {
        "decision",
        "decision_reason",
        "objections",
        "overclaim_points",
        "missing_evidence",
        "next_experiments",
    }
