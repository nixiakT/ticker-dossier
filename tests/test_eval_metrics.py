from __future__ import annotations

from evals.ablation import WITHOUT_POLICY, WITH_POLICY, summarize


def test_replay_ablation_has_all_demo_day_metrics() -> None:
    result = summarize(WITH_POLICY)
    assert set(result) == {"success_rate", "average_steps", "average_tokens", "json_valid_rate"}
    assert result["success_rate"] == 1.0
    assert result["json_valid_rate"] == 1.0


def test_policy_replay_improves_success_and_json_validity() -> None:
    with_policy = summarize(WITH_POLICY)
    without_policy = summarize(WITHOUT_POLICY)
    assert with_policy["success_rate"] > without_policy["success_rate"]
    assert with_policy["json_valid_rate"] > without_policy["json_valid_rate"]
