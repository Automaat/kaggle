import argparse
import hashlib
import json
from dataclasses import asdict
from pathlib import Path

try:
    from .market_ledger import SHOP_DEMAND
    from .shop_forecast import ShopForecastInput, forecast_shops, verify_forecast
except ImportError:
    from market_ledger import SHOP_DEMAND
    from shop_forecast import ShopForecastInput, forecast_shops, verify_forecast


COMPARATOR_COMMIT = "b74a3ea"
COMPARATOR_SHA256 = "86951703eac27253938500eac664650c1e927d1b86b26ed84be008f24739d699"


def registered_cases():
    return (
        ("game_start", ShopForecastInput(0, ())),
        ("before_first_open", ShopForecastInput(71, ())),
        ("after_first_open", ShopForecastInput(72, ("PET_CAFE",))),
        (
            "shop_cap",
            ShopForecastInput(576, tuple(sorted(SHOP_DEMAND))),
        ),
    )


def _source_hash(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def _result_summary(result):
    return {
        "source_step": result.source_step,
        "terminal_step": result.terminal_step,
        "open_shop_signature": result.open_shop_signature,
        "next_daily_replan_step": result.next_daily_replan_step,
        "next_shop_replan_step": result.next_shop_replan_step,
        "action_end_step": result.action_end_step,
        "strategy_end_step": result.strategy_end_step,
        "investment_end_step": result.investment_end_step,
        "scenarios": tuple(
            {
                "name": scenario.name,
                "probability": scenario.probability,
                "next_shop": scenario.next_shop,
                "total_drain": scenario.total_drain,
            }
            for scenario in result.scenarios
        ),
        "expected_drain_by_day": result.expected_drain_by_day,
        "expected_total_drain": result.expected_total_drain,
        "input_hash": result.input_hash,
    }


def run():
    cases = []
    for name, data in registered_cases():
        result = forecast_shops(data)
        cases.append(
            {
                "name": name,
                "input": asdict(data),
                "result": _result_summary(result),
                "verification_errors": verify_forecast(data, result),
            }
        )
    module = Path(__file__).with_name("shop_forecast.py")
    payload = {
        "experiment": "round39_14_shop_aware_rolling",
        "status": "accepted-standalone-not-integrated",
        "comparator": {
            "version": "1.14.0",
            "commit": COMPARATOR_COMMIT,
            "sha256": COMPARATOR_SHA256,
        },
        "information_boundary": {
            "visible": "town.unlocked_shops",
            "future_shop_type_visible": False,
            "episode_seed_visible": False,
            "next_shop_branch_count": len(SHOP_DEMAND),
        },
        "policy": {
            "action_horizon": "current-day",
            "stable_strategy_horizon": "next-shop-opening",
            "investment_horizon": "terminal-step",
            "daily_replan": True,
            "shop-change-replan": True,
        },
        "cases": cases,
        "model_sha256": _source_hash(module),
    }
    encoded = json.dumps(payload, allow_nan=False, indent=2, sort_keys=True)
    payload["deterministic_sha256"] = hashlib.sha256(encoded.encode()).hexdigest()
    return payload


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--output")
    arguments = parser.parse_args()
    payload = run()
    encoded = json.dumps(payload, allow_nan=False, indent=2, sort_keys=True) + "\n"
    if arguments.output:
        Path(arguments.output).write_text(encoded)
    else:
        print(encoded, end="")


if __name__ == "__main__":
    main()
