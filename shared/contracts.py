"""
VentureScout — cross-team 계약 (C가 Day 1에 확정).

원칙: 계약 필드(evidence_id·grounded_on·confidence·stance·depth)는 strict,
      분석 본문(payload)은 느슨. 프롬프트가 바뀌어도 이 파일은 안 바뀜.
"""
from __future__ import annotations
from typing import Literal, Optional
from pydantic import BaseModel, Field

# ── 계약 enum ──
Confidence = Literal["high", "mid", "low"]
Stance = Literal["supports", "contradicts", "neutral"]
Depth = Literal["full", "light"]            # 혼합 스코프
AgentName = Literal["market", "competitor", "tech", "ip", "bm", "critic"]


class EvidenceItem(BaseModel):
    """그라운딩 원자 (→ evidence_items). 모든 주장이 evidence_id로 인용."""
    evidence_id: str
    hypothesis_id: str
    document_id: str
    source_type: str
    evidence_text: str
    stance: Stance
    reliability_score: float


class Hypothesis(BaseModel):
    """Hypothesis Ledger 한 칸 (→ hypotheses)."""
    hypothesis_id: str
    code: str                                # "H5"
    axis: str                                # 고객문제|경쟁|수익|기술|IP
    statement: str
    confidence: Confidence
    next_validation: str


class AgentFinding(BaseModel):
    """★ 모든 분석 에이전트(②③④⑤⑥)의 공통 출력 envelope (→ agent_runs)."""
    agent: AgentName
    hypothesis_id: str
    signal: str                              # 핵심 주장 한 줄
    grounded_on: list[str]                   # evidence_id (required; 비면 검증 실패)
    confidence: Confidence
    depth: Depth
    next_experiment: Optional[str] = None
    payload: dict = Field(default_factory=dict)   # 분석 본문 전부(느슨)


class OverlapCandidate(BaseModel):
    """시그니처 기계 출력: B가 produce, ⑤가 read (→ ip_overlap_candidates)."""
    candidate_id: str
    limitation_id: str
    evidence_id: str
    plan_technical_element: str
    hybrid_score: float
    rank: int


class CriticResult(BaseModel):
    """⑦ Critic 최종 판단."""
    decision: Literal["go", "pivot", "kill", "more_research"]
    confidence: Confidence
    summary: str
    objections: list[dict] = Field(default_factory=list)
    next_experiments: list[str] = Field(default_factory=list)
