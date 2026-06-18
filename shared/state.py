"""LangGraph 런타임 State 컨테이너 (느슨)."""
from __future__ import annotations
from typing import Any, TypedDict
from shared.contracts import (
    AgentName, Confidence, Depth, Hypothesis, EvidenceItem, CriticResult,
)
from pydantic import BaseModel, Field


class AgentFinding(BaseModel):
    """Track B 내부 에이전트 출력 envelope. 통합 시 AgentRun으로 대체 예정."""

    agent: AgentName
    hypothesis_id: str
    signal: str
    grounded_on: list[str]
    confidence: Confidence
    depth: Depth
    next_experiment: str | None = None
    payload: dict[str, Any] = Field(default_factory=dict)


class VentureScoutState(TypedDict, total=False):
    idea: dict                               # ideas 행 (① 출력)
    hypotheses: list[Hypothesis]
    evidence_pool: dict[str, EvidenceItem]   # evidence_id → item
    findings: list[AgentFinding]             # ②③④⑤⑥ 누적
    critic: CriticResult                     # ⑦
    final_report: str
