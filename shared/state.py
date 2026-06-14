"""LangGraph 런타임 State 컨테이너 (느슨).

주의(통합 계약): ②③④⑤⑥이 병렬 분기로 같은 키에 동시 기록하므로,
fan-out 되는 키(findings)는 reducer가 필수. 없으면 INVALID_CONCURRENT_GRAPH_UPDATE.
"""
from __future__ import annotations
import operator
from typing import TypedDict, Annotated
from shared.contracts import Hypothesis, EvidenceItem, AgentFinding, CriticResult


def _merge_dict(a: dict, b: dict) -> dict:
    return {**(a or {}), **(b or {})}


class VentureScoutState(TypedDict, total=False):
    idea: dict
    hypotheses: list[Hypothesis]
    evidence_pool: Annotated[dict[str, EvidenceItem], _merge_dict]  # 병렬 기록 머지
    findings: Annotated[list[AgentFinding], operator.add]           # 병렬 append
    critic: CriticResult
    final_report: str