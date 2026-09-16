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


# ── 티어별 단가 (haiku 추가 이후) ────────────────────────────────────────────
# 누적을 한 덩어리로 세면 haiku 호출까지 sonnet 단가로 계산돼 비용이 3배로 잡히고,
# 그 숫자로 상한을 걸면 멀쩡한 실행이 막힌다.

def test_haiku_costs_one_third_of_sonnet():
    llm._record_usage(1_000_000, 0, "sonnet")
    llm._record_usage(1_000_000, 0, "haiku")
    snap = llm.usage_snapshot()

    assert snap["by_tier"]["sonnet"]["cost_usd"] == 3.0
    assert snap["by_tier"]["haiku"]["cost_usd"] == 1.0
    assert snap["cost_usd"] == 4.0          # 합계는 티어별 합
    assert snap["input_tokens"] == 2_000_000
    assert snap["calls"] == 2


def test_default_tier_is_sonnet():
    """티어를 안 넘기는 기존 호출부는 sonnet으로 센다(기존 동작 유지)."""
    llm._record_usage(1_000_000, 0)
    assert llm.usage_snapshot()["cost_usd"] == 3.0


def test_budget_uses_tier_price_not_flat_sonnet(monkeypatch):
    """haiku로 300만 토큰을 써도 $3이므로 상한 $4에 걸리면 안 된다."""
    monkeypatch.setattr(llm, "MAX_COST_USD_PER_JOB", 4.0)
    monkeypatch.setattr(llm, "MAX_LLM_CALLS_PER_JOB", 0)
    llm._record_usage(3_000_000, 0, "haiku")
    llm._check_budget()          # sonnet 단가로 셌다면 $9라 여기서 막힌다


def test_snapshot_empty_has_no_tiers():
    snap = llm.usage_snapshot()
    assert snap["by_tier"] == {}
    assert snap["cost_usd"] == 0.0
