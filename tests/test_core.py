from lib import costs
from lib.ledger import Ledger


def test_claude_cost_math():
    # Haiku 5.5: $0.10 in / $0.50 out per 1M
    assert abs(costs.claude_cost("claude-haiku-5-5", 1_000_000, 1_000_000) - 0.60) < 1e-9
    assert abs(costs.claude_cost("claude-haiku-5-5", 1_000_000, 1_000_000, batch=True) - 0.30) < 1e-9


def test_cost_from_usage_dict_includes_cache():
    usage = {"input_tokens": 1000, "output_tokens": 500,
             "cache_creation_input_tokens": 2000, "cache_read_input_tokens": 10000}
    expected = (1000 * 4 + 500 * 20 + 2000 * 5 + 10000 * 0.2) / 1_000_000
    assert abs(costs.cost_from_usage("claude-opus-5-5", usage) - expected) < 1e-12


def test_apify_cost():
    cfg = {"price_per_1000_results": 2.0, "start_fee_usd": 0.02}
    assert abs(costs.apify_cost(500, cfg) - 1.02) < 1e-9


def test_estimate_total():
    e = costs.Estimate("t")
    e.add("a", "1", 0.5)
    e.add("b", "2", 0.25)
    assert e.total == 0.75
    assert "TOTAL" in e.render()


def test_ledger_stages(tmp_path):
    path = tmp_path / "ledger.json"
    led = Ledger(path)
    led.mark("abc", fetched_at="2026-11-18")
    assert led.has("abc")
    assert not led.has("abc", "extracted_at")
    assert led.missing(["abc", "def"], "fetched_at") == ["def"]
    led.save()
    assert Ledger(path).has("abc")
