"""LangGraph 런타임 State 컨테이너 (느슨)."""
from __future__ import annotations
from typing import TypedDict
from shared.contracts import Hypothesis, EvidenceItem, AgentFinding, CriticResult


class VentureScoutState(TypedDict, total=False):
    idea: dict                               # ideas 행 (① 출력)
    hypotheses: list[Hypothesis]
    evidence_pool: dict[str, EvidenceItem]   # evidence_id → item
    findings: list[AgentFinding]             # ②③④⑤⑥ 누적
    critic: CriticResult                     # ⑦
    final_report: str
