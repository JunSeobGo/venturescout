"""계약 스키마 검증 — Day 1부터 green 유지."""
from shared.contracts import AgentRun, EvidenceItem, IPOverlapCandidate
from shared.state import AgentFinding


def test_agent_finding_requires_grounding():
    f = AgentFinding(agent="ip", hypothesis_id="H5", signal="중첩 신호 중간",
                     grounded_on=["ev_0412"], confidence="mid", depth="full")
    assert f.grounded_on == ["ev_0412"]
    assert f.payload == {}                      # 느슨 payload 기본 빈 dict


def test_evidence_stance_enum():
    e = EvidenceItem(evidence_id="ev_1", job_id="job_001", hypothesis_id="H1",
                     document_id="d1", source_type="seed_review", evidence_text="...",
                     stance="contradicts", relevance_score=0.5, reliability_score=0.6)
    assert e.stance == "contradicts"
    assert e.job_id == "job_001"
    assert e.relevance_score == 0.5


def test_agent_run_grounded_on():
    """C팀 에이전트가 사용하는 AgentRun 계약 검증."""
    r = AgentRun(
        job_id="job_001", agent_name="market", depth="full", confidence="mid",
        grounded_on=["ev_0412"],
    )
    assert r.grounded_on == ["ev_0412"]
    assert r.output_json == {}


def test_ip_overlap_candidate():
    c = IPOverlapCandidate(
        candidate_id="c1", job_id="job_001", hypothesis_id="H5",
        limitation_id="lim_1", evidence_id="ev_1",
        plan_technical_element="STT", lexical_score=0.3,
        similarity_score=0.8, hybrid_score=0.7, rank=1,
    )
    assert c.hybrid_score == 0.7
