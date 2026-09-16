import sys
from types import SimpleNamespace

from agents import graph
from agents.llm import (
    current_model_name,
    invoke_claude_json,
    load_claude_config,
    model_tier_for_agent,
)


def test_agent_model_tiers():
    assert model_tier_for_agent("structuring") == "sonnet"
    assert model_tier_for_agent("market") == "sonnet"
    assert model_tier_for_agent("competitor") == "sonnet"
    assert model_tier_for_agent("bm") == "sonnet"
    assert model_tier_for_agent("tech") == "sonnet"
    assert model_tier_for_agent("ip") == "sonnet"
    assert model_tier_for_agent("critic") == "sonnet"


def test_all_agents_use_same_sonnet_model(monkeypatch):
    monkeypatch.setenv("AGENT_LLM_PROVIDER", "bedrock")
    monkeypatch.setenv("BEDROCK_SONNET_MODEL_ID", "sonnet-test")

    assert load_claude_config("sonnet").model_id == "sonnet-test"
    assert current_model_name("tech") == "bedrock:sonnet:sonnet-test"
    assert current_model_name("ip") == "bedrock:sonnet:sonnet-test"
    assert current_model_name("critic") == "bedrock:sonnet:sonnet-test"


def test_graph_passes_agent_tier_to_llm(monkeypatch):
    called_tiers = []

    def fake_invoke_claude_json(*, system, user, model_tier):
        called_tiers.append(model_tier)
        return {"summary": "test"}

    monkeypatch.setattr(graph, "invoke_claude_json", fake_invoke_claude_json)

    for agent_name in ("market", "competitor", "bm", "tech", "ip", "critic"):
        graph._agent_output_with_llm(
            agent_name=agent_name,
            hypothesis_id="H-test",
            role="test",
            required_fields=["summary"],
            context={},
        )

    assert called_tiers == [
        "sonnet",
        "sonnet",
        "sonnet",
        "sonnet",
        "sonnet",
        "sonnet",
    ]


def test_invoke_uses_selected_tier_model_id(monkeypatch):
    requested_model_ids = []

    class FakeClient:
        def converse(self, *, modelId, **kwargs):
            requested_model_ids.append(modelId)
            return {
                "output": {
                    "message": {
                        "content": [{"text": '{"summary": "ok"}'}]
                    }
                }
            }

    fake_boto3 = SimpleNamespace(
        client=lambda service_name, region_name, **kwargs: FakeClient()
    )
    monkeypatch.setitem(sys.modules, "boto3", fake_boto3)
    monkeypatch.setenv("AGENT_LLM_PROVIDER", "bedrock")
    monkeypatch.setenv("BEDROCK_SONNET_MODEL_ID", "sonnet-selected")

    output = invoke_claude_json(
        system="system",
        user="user",
        model_tier="sonnet",
    )

    assert requested_model_ids == ["sonnet-selected"]
    assert output["summary"] == "ok"
    assert output["llm_model_id"] == "sonnet-selected"
    assert output["llm_succeeded"] is True


# ── 티어 분리 (haiku 추가 이후) ───────────────────────────────────────────────
# _model_id_for_tier가 인자를 무시하고 항상 sonnet을 돌려주던 버그가 있었다.
# 티어가 하나뿐일 땐 드러나지 않았으므로, 두 번째 티어를 넣은 지금 고정한다.

def test_haiku_tier_resolves_to_its_own_model_id(monkeypatch):
    monkeypatch.setenv("BEDROCK_SONNET_MODEL_ID", "sonnet-test")
    monkeypatch.setenv("BEDROCK_HAIKU_MODEL_ID", "haiku-test")

    assert load_claude_config("sonnet").model_id == "sonnet-test"
    assert load_claude_config("haiku").model_id == "haiku-test"


def test_legacy_bedrock_model_id_does_not_leak_into_haiku(monkeypatch):
    """티어 구분 없던 시절의 BEDROCK_MODEL_ID가 haiku를 sonnet으로 새게 하면 안 된다."""
    monkeypatch.delenv("BEDROCK_SONNET_MODEL_ID", raising=False)
    monkeypatch.delenv("BEDROCK_HAIKU_MODEL_ID", raising=False)
    monkeypatch.setenv("BEDROCK_MODEL_ID", "legacy-sonnet")

    assert load_claude_config("sonnet").model_id == "legacy-sonnet"
    assert load_claude_config("haiku").model_id != "legacy-sonnet"


def test_agent_tier_overrides_from_env(monkeypatch):
    """A/B 실험은 코드 수정이 아니라 환경변수로 한다."""
    monkeypatch.setenv("AGENT_TIER_OVERRIDES", "market=haiku, tech=haiku")

    assert model_tier_for_agent("market") == "haiku"
    assert model_tier_for_agent("tech") == "haiku"
    assert model_tier_for_agent("critic") == "sonnet"   # 지정 안 한 노드는 그대로


def test_tier_override_ignores_unknown_tier(monkeypatch):
    """오타 하나로 실행이 죽는 것보다 기본값으로 도는 편이 안전하다."""
    monkeypatch.setenv("AGENT_TIER_OVERRIDES", "market=hauki")
    assert model_tier_for_agent("market") == "sonnet"
