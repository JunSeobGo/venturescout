"""실행 상한(guardrail) 검증 — 비용·호출 수를 재기만 하지 않고 실제로 막는지.

지금까지 usage는 계측만 하고 차단하지 않아, 루프 버그나 재시도 폭주가 나면
그대로 과금됐다. AWS 계정이 개인 명의로 넘어와 실제 위험이 된 뒤 추가한 방어다.
DB·LLM을 쓰지 않는다.
"""
import pytest

from agents import llm


@pytest.fixture(autouse=True)
def _clean_usage():
    llm.reset_usage()
    yield
    llm.reset_usage()


def test_check_budget_passes_when_unused():
    """아무것도 안 쓴 상태에서는 통과해야 한다."""
    llm._check_budget()          # 예외가 안 나면 통과


def test_call_count_limit_blocks(monkeypatch):
    monkeypatch.setattr(llm, "MAX_LLM_CALLS_PER_JOB", 3)
    for _ in range(3):
        llm._record_usage(10, 10)
    with pytest.raises(llm.BudgetExceeded, match="호출 상한"):
        llm._check_budget()


def test_call_count_limit_allows_under_threshold(monkeypatch):
    monkeypatch.setattr(llm, "MAX_LLM_CALLS_PER_JOB", 3)
    for _ in range(2):
        llm._record_usage(10, 10)
    llm._check_budget()


def test_cost_limit_blocks(monkeypatch):
    """입력 100만 토큰이면 $3 — 상한 $1이면 막혀야 한다."""
    monkeypatch.setattr(llm, "MAX_COST_USD_PER_JOB", 1.0)
    monkeypatch.setattr(llm, "MAX_LLM_CALLS_PER_JOB", 0)   # 호출 수 제한은 끄고 비용만
    llm._record_usage(1_000_000, 0)
    with pytest.raises(llm.BudgetExceeded, match="비용 상한"):
        llm._check_budget()


def test_zero_means_unlimited(monkeypatch):
    """0은 '제한 없음'이다 — 끄고 싶은 사람이 실수로 즉시 차단당하지 않게."""
    monkeypatch.setattr(llm, "MAX_COST_USD_PER_JOB", 0)
    monkeypatch.setattr(llm, "MAX_LLM_CALLS_PER_JOB", 0)
    llm._record_usage(10_000_000, 10_000_000)
    llm._check_budget()


def test_invoke_checks_budget_before_calling_bedrock(monkeypatch):
    """상한에 걸리면 Bedrock에 요청조차 보내지 않아야 한다(과금 방지의 핵심)."""
    monkeypatch.setattr(llm, "MAX_LLM_CALLS_PER_JOB", 1)
    llm._record_usage(10, 10)

    called = []
    monkeypatch.setattr(llm, "load_claude_config", lambda *a, **k: called.append(1))

    with pytest.raises(llm.BudgetExceeded):
        llm.invoke_claude_json(system="s", user="u")
    assert called == [], "상한 초과인데 설정 로드까지 진행됐다 — 검사 위치가 늦다"


def test_reset_usage_clears_budget_state():
    """잡마다 리셋되지 않으면 두 번째 잡이 즉시 상한에 걸린다."""
    llm._record_usage(500_000, 500_000)
    llm.reset_usage()
    snap = llm.usage_snapshot()
    assert snap["calls"] == 0 and snap["cost_usd"] == 0.0
