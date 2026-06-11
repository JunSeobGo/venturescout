"""계약 스키마 검증 — Day 1부터 green 유지."""
from shared.contracts import AgentFinding, EvidenceItem


def test_agent_finding_requires_grounding():
    f = AgentFinding(agent="ip", hypothesis_id="H5", signal="중첩 신호 중간",
                     grounded_on=["ev_0412"], confidence="mid", depth="full")
    assert f.grounded_on == ["ev_0412"]
    assert f.payload == {}                      # 느슨 payload 기본 빈 dict


def test_evidence_stance_enum():
    e = EvidenceItem(evidence_id="ev_1", hypothesis_id="H1", document_id="d1",
                     source_type="seed_review", evidence_text="...",
                     stance="contradicts", reliability_score=0.6)
    assert e.stance == "contradicts"
