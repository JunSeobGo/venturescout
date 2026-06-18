from agents.graph import build_graph
from agents.mock_data import MOCK_DOCUMENTS, MOCK_EVIDENCE, MOCK_IP_CANDIDATES


def test_mock_graph_uses_mock_data_as_source():
    result = build_graph().invoke(
        {
            "job_id": "job_test",
            "idea_id": "idea_test",
        }
    )

    assert result["idea"].title == "AI 회의록 자동화 SaaS"
    assert len(result["documents"]) == len(MOCK_DOCUMENTS)
    assert len(result["evidence_items"]) == len(MOCK_EVIDENCE)
    assert len(result["ip_overlap_candidates"]) == len(MOCK_IP_CANDIDATES)
    assert len(result["agent_runs"]) == 6


def test_every_agent_run_cites_existing_mock_evidence():
    result = build_graph().invoke(
        {
            "job_id": "job_test",
            "idea_id": "idea_test",
        }
    )

    evidence_ids = set(result["evidence_items"])

    for run in result["agent_runs"]:
        assert set(run.grounded_on).issubset(evidence_ids)


def test_final_report_contains_professional_evidence_sections():
    result = build_graph().invoke(
        {
            "job_id": "job_test",
            "idea_id": "idea_test",
        }
    )

    report = result["final_report"]
    assert report["executive_summary"]["decision"] == result["decision"]
    assert len(report["hypothesis_assessment"]) == 5
    assert report["related_patents"]
    assert report["related_patents"][0]["evidence_id"].startswith("ev_ip_")
    assert report["related_business_signals"]
    assert report["priority_recommendations"]
    assert report["traceability"]["evidence_ids"]

    critic_run = result["agent_runs"][-1]
    assert critic_run.agent_name == "critic"
    assert critic_run.output_json["professional_report"] == report
