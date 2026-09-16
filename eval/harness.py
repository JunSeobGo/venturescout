"""
Track D — 평가 하네스 (ADR-019: process 기반, outcome 정답 없음).

창업 검증은 verdict 정답이 없으므로 '맞췄나'가 아니라 '과정이 건강한가'를 잰다.
헤드라인 = **Critic ON/OFF 정량화**(멀티에이전트가 실제로 뭘 바꾸는가).

실 LLM(Bedrock) 연결 후 — graph는 비결정적이므로 1회 측정으론 'Critic 효능'인지
'그 회의 우연'인지 구분 불가. 그래서 같은 idea를 N회 ON/OFF 돌려 **분포로 집계**한다(ADR-030).
  - 지금 계산: JSON Validity·Groundedness·Overclaim(실측)·latency·cost_usd(실측 토큰)·
    Critic ON/OFF 분포 / 검색 지표 precision@k·recall@k·MRR·NDCG@k·contradiction_coverage
  - 검색 지표는 정답 라벨셋을 읽어 계산한다(eval/labelset.py). 라벨이 비어 있으면
    조용한 None이 아니라 사유를 담아 반환한다.
  - 아직 미구현: Answer/Citation Accuracy, latency p50/p95/p99, Task Success Rate

graph.py는 건드리지 않는다. Critic OFF는 여기서 critic 없는 그래프를 따로 배선해 만든다.
"""
from __future__ import annotations
import statistics
import time
from collections import Counter
from typing import Optional

from langgraph.graph import StateGraph, START, END

from shared.state import VentureScoutState
from shared.contracts import AgentRun, CriticResult
from agents.graph import (
    build_graph,                                    # Critic ON (척추 그대로)
    structuring_node, market_node, competitor_node,
    tech_node, ip_node, bm_node,                    # critic_node만 빼고 재사용
)
from agents.llm import reset_usage, usage_snapshot  # 실측 토큰·비용 (ADR-029 cost_usd 승격)

# 헤드라인 반복 횟수 기본값. 실 LLM은 호출당 비용·지연이 있어 무한 반복 불가
# → idea당 적당히(ADR-030: idea 2~3개 × 5회 수준). 환경에 맞게 조절.
DEFAULT_REPEAT = 5

ANALYSIS_NODES = [
    ("market", market_node), ("competitor", competitor_node),
    ("tech", tech_node), ("ip", ip_node), ("bm", bm_node),
]


# ── Critic OFF 그래프 (critic 노드 없이 structuring → 분석 5노드 → END) ──
def build_graph_no_critic():
    """Critic OFF 베이스라인. ⑦ 적대검증 없이 agent_runs만 모으고 끝낸다.

    ON 그래프와 동일 배선에서 critic 노드/엣지만 제거 → 멀티에이전트 효과의
    '대조군'. graph.py 원본 불변(여기서 별도 컴파일).
    """
    g = StateGraph(VentureScoutState)
    g.add_node("structuring", structuring_node)
    for name, fn in ANALYSIS_NODES:
        g.add_node(name, fn)
    g.add_edge(START, "structuring")
    for name, _ in ANALYSIS_NODES:
        g.add_edge("structuring", name)
        g.add_edge(name, END)                       # critic 없이 바로 종료
    return g.compile()


# ── process 지표 ──
# C 계약: 분석 산출은 agent_runs = list[AgentRun].
def json_validity(runs: list[AgentRun]) -> float:
    """agent_runs가 전부 계약(AgentRun) 스키마를 만족하는 비율. (재검증)"""
    if not runs:
        return 0.0
    ok = 0
    for r in runs:
        try:
            AgentRun.model_validate(r.model_dump())
            ok += 1
        except Exception:
            pass
    return ok / len(runs)


def groundedness(runs: list[AgentRun]) -> float:
    """grounded_on(근거 id)이 비어있지 않은 run 비율. 근거 없는 주장 = 비그라운드."""
    if not runs:
        return 0.0
    return sum(1 for r in runs if r.grounded_on) / len(runs)


def overclaim_count(runs: list[AgentRun]) -> int:
    """금지된 단정 표현이 검출된 run 수 (`overclaim_flag`).

    이전 정의는 "grounded_on 비었는데 confidence≠low"였는데, `grounded_on`이 계약상
    min_length=1이라 **구조적으로 항상 0**이었다. 지금은 graph._overclaim_audit()이
    agents.guardrails.BANNED_CLAIMS로 실제 문구를 검사해 flag를 세운다.
    """
    return sum(1 for r in runs if r.overclaim_flag)


def overclaim_rate(runs: list[AgentRun]) -> float:
    """run 중 과장 표현이 검출된 비율. 낮을수록 정직한 출력."""
    if not runs:
        return 0.0
    return round(overclaim_count(runs) / len(runs), 3)


def overclaim_phrases(runs: list[AgentRun]) -> dict[str, int]:
    """검출된 금지 표현별 빈도 — 어떤 표현이 실제로 나오는지 봐야 목록을 튜닝할 수 있다."""
    found: Counter[str] = Counter()
    for run in runs:
        found.update(run.output_json.get("_overclaim_phrases") or [])
    return dict(found)


# ── 헤드라인: Critic ON/OFF 비교 ──
def _naive_decision(runs: list[AgentRun]) -> str:
    """Critic OFF 베이스라인 판정: 적대검증 없는 낙관 규칙.
    근거 있는 run이 하나라도 있으면 'go'(편향 그대로) — Critic이 이걸 교정하는지 본다."""
    return "go" if any(r.grounded_on for r in runs) else "more_research"


def _invoke_timed(graph, idea: dict) -> tuple[dict, float]:
    """이미 컴파일된 graph를 1회 invoke → (최종 state, 소요초). 컴파일은 타이밍 밖."""
    t0 = time.perf_counter()
    state = graph.invoke({"idea": idea})
    return state, time.perf_counter() - t0


def _compare_once(idea: dict) -> tuple[dict, list[AgentRun]]:
    """1회 ON/OFF 실행 → (비교 dict, ON 그래프의 agent_runs).
    agent_metrics 재사용을 위해 ON runs도 함께 반환(추가 invoke 없음)."""
    off_state, off_latency = _invoke_timed(build_graph_no_critic(), idea)
    on_state, on_latency = _invoke_timed(build_graph(), idea)

    off_runs = off_state.get("agent_runs", [])
    critic: Optional[CriticResult] = on_state.get("critic")
    off_decision = _naive_decision(off_runs)
    on_decision = critic.decision if critic else None

    on_runs = on_state.get("agent_runs", [])
    # ⚠️ overclaim은 ON/OFF "감소량"이 아니다. Critic은 다른 에이전트의 출력 텍스트를
    #    다시 쓰지 않으므로 분석 5노드의 과장 표현은 양쪽에서 같다(LLM 비결정성 제외).
    #    Critic이 실제로 교정하는 건 **최종 판정**이고, 그건 decision_changed가 잡는다.
    #    여기서는 분석 노드와 critic 자신의 과장 비율을 각각 관측값으로만 남긴다.
    comparison = {
        "off_decision": off_decision,
        "on_decision": on_decision,
        "decision_changed": off_decision != on_decision,
        "objections_added": len(critic.objections) if critic else 0,
        "overclaim_rate_analysis": overclaim_rate(off_runs),
        "overclaim_rate_critic": overclaim_rate(
            [r for r in on_runs if r.agent_name == "critic"]
        ),
        "critic_latency_overhead_s": round(on_latency - off_latency, 4),
    }
    return comparison, on_runs


def compare_critic(idea: dict) -> dict:
    """멀티에이전트 헤드라인 (단발). ON vs OFF 1회 차이. 내부 단위 — 반복은 아래에서."""
    return _compare_once(idea)[0]


def compare_critic_repeated(idea: dict, n: int = DEFAULT_REPEAT) -> dict:
    """ADR-030: 같은 idea를 N회 ON/OFF 돌려 분포로 집계.

    실 LLM은 비결정적이라 1회로는 'Critic 효능 vs 우연'을 못 가린다. N회로:
      - change_rate           : Critic이 판정을 바꾼 비율(0~1) — 헤드라인의 진짜 답
      - objections_mean/stdev : 반박 수 평균·표준편차(stdev 작을수록 출력 안정)
      - on_decision_distribution: ON 판정 분포(한 판정 수렴=신뢰 / 흩어지면 모호)
    ※ Bedrock 호출이 N배 → 비용·지연도 N배.
    """
    singles = [_compare_once(idea)[0] for _ in range(n)]
    changes = [s["decision_changed"] for s in singles]
    objs = [s["objections_added"] for s in singles]
    on_decisions = [s["on_decision"] for s in singles]
    overheads = [s["critic_latency_overhead_s"] for s in singles]

    return {
        "n": n,
        "change_rate": round(sum(changes) / n, 3),
        "objections_mean": round(statistics.fmean(objs), 2),
        "objections_stdev": round(statistics.pstdev(objs), 2) if n > 1 else 0.0,
        "on_decision_distribution": dict(Counter(d for d in on_decisions if d)),
        "critic_latency_overhead_s_mean": round(statistics.fmean(overheads), 4),
        "sample": singles[0],   # 참고용 첫 회 단발 결과
    }


# ── 전체 평가 ──
def evaluate(idea: dict, n: int = DEFAULT_REPEAT) -> dict:
    """idea 하나에 대한 process 지표 묶음.

    ON/OFF를 N회 돌려(헤드라인은 분포 집계, ADR-030) 첫 회 ON 결과로 agent_metrics 산출.
    총 invoke = N×2 (OFF+ON). 실 LLM 시 비용·지연 N배 주의.
    """
    singles: list[dict] = []
    first_runs: list[AgentRun] = []
    first_on_latency = 0.0
    # 첫 회 ON 1회분(structuring+분석5+critic)만 실측 비용으로 잡는다 = "분석 1건 비용".
    first_cost = {"input_tokens": 0, "output_tokens": 0, "calls": 0, "cost_usd": None}
    for i in range(n):
        off_state, off_latency = _invoke_timed(build_graph_no_critic(), idea)
        if i == 0:
            reset_usage()                       # OFF 제외, ON 1회분만 누적
        on_state, on_latency = _invoke_timed(build_graph(), idea)
        if i == 0:
            first_cost = usage_snapshot()
        off_runs = off_state.get("agent_runs", [])
        critic: Optional[CriticResult] = on_state.get("critic")
        off_decision = _naive_decision(off_runs)
        on_runs = on_state.get("agent_runs", [])
        singles.append({
            "off_decision": off_decision,
            "on_decision": critic.decision if critic else None,
            "decision_changed": off_decision != (critic.decision if critic else None),
            "objections_added": len(critic.objections) if critic else 0,
            "overclaim_rate_analysis": overclaim_rate(off_runs),
            "overclaim_rate_critic": overclaim_rate(
                [r for r in on_runs if r.agent_name == "critic"]
            ),
            "critic_latency_overhead_s": round(on_latency - off_latency, 4),
        })
        if i == 0:
            first_runs = on_runs
            first_on_latency = on_latency

    changes = [s["decision_changed"] for s in singles]
    objs = [s["objections_added"] for s in singles]
    on_decisions = [s["on_decision"] for s in singles]
    overheads = [s["critic_latency_overhead_s"] for s in singles]

    return {
        "agent_metrics": {
            "json_validity": json_validity(first_runs),
            "groundedness": groundedness(first_runs),
            "overclaim_count": overclaim_count(first_runs),
            "overclaim_rate": overclaim_rate(first_runs),
            # 어떤 표현이 실제로 검출됐는지 — BANNED_CLAIMS 튜닝 근거
            "overclaim_phrases": overclaim_phrases(first_runs),
        },
        # ★ 헤드라인 (ADR-019/030) — N회 분포 집계
        "multiagent_effect": {
            "n": n,
            "change_rate": round(sum(changes) / n, 3),
            "objections_mean": round(statistics.fmean(objs), 2),
            "objections_stdev": round(statistics.pstdev(objs), 2) if n > 1 else 0.0,
            "on_decision_distribution": dict(Counter(d for d in on_decisions if d)),
            "critic_latency_overhead_s_mean": round(statistics.fmean(overheads), 4),
            "sample": singles[0],
        },
        "system_metrics": {
            "latency_s": round(first_on_latency, 4),
            # 실측: agents/llm.py가 converse usage(input/output 토큰)를 캡처·누적 → 단가로 환산.
            # 분석 1건(ON 1회) 기준. 단가는 Sonnet 4.6 $3/$15 per 1M(.env로 override 가능).
            "cost_usd": first_cost["cost_usd"],
            "tokens": {
                "input": first_cost["input_tokens"],
                "output": first_cost["output_tokens"],
                "calls": first_cost["calls"],
            },
        },
        # 라벨셋이 있으면 실측, 없으면 사유를 담아 반환한다(조용한 None 금지).
        "retrieval_metrics": retrieval_metrics(),
    }


def retrieval_metrics(k: int = 5) -> dict:
    """검색 품질 지표. 라벨셋이 없으면 계산 대신 이유를 돌려준다.

    LLM을 쓰지 않아 잡 평가와 독립적으로 돌릴 수 있다 — `python -m eval.labelset`.
    """
    from eval.labelset import evaluate_retrieval  # 순환 import 방지용 지연 import

    try:
        result = evaluate_retrieval(k=k)
    except FileNotFoundError as exc:
        return {
            "precision_at_k": None,
            "contradiction_coverage": None,
            "reason": f"라벨셋 없음 — eval/build_labelset.py로 생성 필요 ({exc.args[0].splitlines()[0]})",
        }
    return {
        "k": result["k"],
        "queries": result["queries"],
        "precision_at_k": result["precision_at_k"],
        # 풀 기준 recall — 라벨이 검색 상위 N건에만 달려 있어 절대값이 아니다
        "recall_at_k_pooled": result["recall_at_k_pooled"],
        "mrr": result["mrr"],
        "ndcg_at_k": result["ndcg_at_k"],
        "contradiction_coverage": result["contradiction_coverage"],
        "unlabeled_in_topk": result["unlabeled_in_topk"],
    }


def _print_report(idea: dict, n: int = DEFAULT_REPEAT) -> None:
    import json
    rep = evaluate(idea, n=n)
    print("=== VentureScout 평가 하네스 ===")
    print(json.dumps(rep, ensure_ascii=False, indent=2, default=str))
    me = rep["multiagent_effect"]
    print("\n--- 헤드라인 요약 (ADR-030 분포) ---")
    print(f"  반복 횟수(n)     : {me['n']}")
    print(f"  판정 교정 비율   : {me['change_rate']}  (Critic이 OFF→ON에서 판정 바꾼 비율)")
    print(f"  반박 수 평균±편차: {me['objections_mean']} ± {me['objections_stdev']}")
    print(f"  ON 판정 분포     : {me['on_decision_distribution']}")


if __name__ == "__main__":
    # 실 LLM이면 n회만큼 Bedrock 호출 → 비용·시간 주의. 빠른 점검은 n 낮춰서.
    _print_report({"technical_elements": ["추천", "임베딩"], "revenue_hint": "commission"}, n=3)
