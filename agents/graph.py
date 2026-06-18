"""
Track C — LangGraph 골격 (척추).
① 구조화 → ②③④⑤⑥ 분석(①의 출력 공통 입력) → ⑦ Critic.
①(시장맥락 분기)·②특허체인은 병렬, ②③④⑤⑥은 leaf, ⑦이 supervisor/critic.

지금은 노드 stub. 각 노드는 AgentFinding(혼합: depth=full/light)을 반환.
실제 LLM 연결은 Bedrock ChatBedrockConverse로.
"""
from __future__ import annotations
from langgraph.graph import StateGraph, START, END
from shared.state import VentureScoutState, AgentFinding
from shared.contracts import CriticResult
from retrieval.tools import vector_search
from retrieval.agents import run_market_agent, run_competitor_agent

# TODO(C): Bedrock ChatBedrockConverse 연결, 프롬프트·few-shot·가드레일 중앙 배포


def structuring_node(state: VentureScoutState) -> dict:
    """① 자유 텍스트 → 가설·기술요소 구조화 (사용자 확인 권장)."""
    # TODO(C): LLM 구조화
    return {"hypotheses": [], "idea": state.get("idea", {})}


def _leaf_finding(agent: str, depth: str) -> AgentFinding:
    return AgentFinding(
        agent=agent, hypothesis_id="H0", signal=f"[MOCK] {agent}",
        grounded_on=["ev_mock_0001"], confidence="low", depth=depth,
    )


def market_node(state):       # ② B 소유, full
    return {"findings": [run_market_agent(state)]}

def competitor_node(state):   # ③ B 소유, light
    return {"findings": [run_competitor_agent(state)]}

def tech_node(state):         # ④ C 소유, light
    return {"findings": [_leaf_finding("tech", "light")]}

def ip_node(state):           # ⑤ C 소유, full (시그니처) — 후보 읽어 판정
    vector_search(state.get("idea", {}).get("technical_elements", []))
    return {"findings": [_leaf_finding("ip", "full")]}

def bm_node(state):           # ⑥ D 소유, light
    return {"findings": [_leaf_finding("bm", "light")]}

def critic_node(state):       # ⑦ C 소유 (척추) — 반박 + 판단
    # TODO(C): ②~⑥ findings 적대 검증, overclaim 차단
    return {"critic": CriticResult(decision="more_research", confidence="low",
                                   summary="[MOCK] 근거 부족, 추가 검증 필요")}


def build_graph():
    g = StateGraph(VentureScoutState)
    for name, fn in [
        ("structuring", structuring_node), ("market", market_node),
        ("competitor", competitor_node), ("tech", tech_node),
        ("ip", ip_node), ("bm", bm_node), ("critic", critic_node),
    ]:
        g.add_node(name, fn)
    g.add_edge(START, "structuring")
    for n in ["market", "competitor", "tech", "ip", "bm"]:
        g.add_edge("structuring", n)        # 분석 병렬 분기
        g.add_edge(n, "critic")             # critic이 종합
    g.add_edge("critic", END)
    return g.compile()


if __name__ == "__main__":
    graph = build_graph()
    print(graph.invoke({"idea": {"technical_elements": ["STT", "요약"]}}))
