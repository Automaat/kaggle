import copy
import dataclasses
import importlib
import json
import pathlib
import sys

import pytest
from kaggle_environments import make
from kaggle_environments.envs.kaggriculture import kaggriculture as simulator


TOOLS = pathlib.Path(__file__).resolve().parent.parent / "tools"
sys.path.insert(0, str(TOOLS))
ledger = importlib.import_module("economics.market_ledger")
validator = importlib.import_module("economics.validate_market_ledger")


def _shed(**values):
    result = {item: 0 for item in ledger.SHED_ITEMS}
    result.update(values)
    return result


def _seeds(**values):
    result = {item: 0 for item in ledger.CROPS}
    result.update(values)
    return result


def _inventory(**values):
    result = {
        item: int(param.I0)
        for item, param in zip(ledger.PRODUCTS, ledger.DEFAULT_MARKET_PARAMS)
    }
    result.update(values)
    return result


def _account(money=3000.0, shed=None, seeds=None, **values):
    return ledger.PlayerAccount.from_mappings(
        money,
        shed or _shed(),
        seeds or _seeds(),
        values.get("hires_today", 0),
        values.get("unlocked_quadrants", 1),
        values.get("hands", 0),
    )


def _state(
    step=1,
    inventory=None,
    players=None,
    shops=(),
    params=ledger.DEFAULT_MARKET_PARAMS,
    config=ledger.MarketConfig(),
):
    return ledger.MarketState.from_mappings(
        step,
        inventory or _inventory(),
        players or (_account(), _account()),
        shops,
        params,
        config,
    )


def _replace_player(model_state, index, player):
    players = list(model_state.players)
    players[index] = player
    return dataclasses.replace(model_state, players=tuple(players))


def test_tables_match_simulator():
    assert ledger.PRODUCTS == tuple(simulator.PRODUCTS)
    assert ledger.CROPS == tuple(simulator.CROPS)
    assert ledger.ANIMALS == tuple(simulator.ANIMALS)
    assert ledger.SHOP_DEMAND == {
        shop: tuple(products) for shop, products in simulator.SHOPS.items()
    }
    assert ledger.SHOP_DEMAND["ICE_CREAM_SHOP"] == (
        "STRAWBERRY",
        "MILK",
        "WHEAT",
    )


def test_price_function_matches_simulator_boundaries():
    functions = ("linear", "sq", "sqrt", "log", "log10", "hinge", "unknown")
    for item, default in zip(ledger.PRODUCTS, ledger.DEFAULT_MARKET_PARAMS):
        values = (
            int(default.I0 - 2 * default.T),
            int(default.I0 - default.T),
            int(default.I0 - 1),
            int(default.I0),
            int(default.I0 + 1),
            int(default.I0 + default.T),
            int(default.I0 + 2 * default.T),
            -1,
        )
        for function in functions:
            overrides = {
                item: {
                    "below_func": function,
                    "above_func": function,
                    "I0": 9000,
                    "T": 150,
                }
            }
            params = ledger.resolve_market_params(overrides)
            reference = simulator._resolve_market_params(overrides)
            for inventory in values:
                assert ledger.market_price(item, inventory, params) == simulator.market_price(
                    item,
                    inventory,
                    reference,
                )


def test_sparse_parameter_resolution_matches_simulator():
    overrides = {
        "WOOL": {"I0": 9123, "T": 77, "above_func": "log10"},
        "WHEAT": "ignored",
        "UNKNOWN": {"base": 900},
    }
    params = ledger.resolve_market_params(overrides)
    reference = simulator._resolve_market_params(overrides)
    for item in ledger.PRODUCTS:
        for inventory in (8000, 9123, 10000, 12000):
            assert ledger.market_price(item, inventory, params) == simulator.market_price(
                item,
                inventory,
                reference,
            )


def test_buy_then_sell_nets_zero():
    model_state = _state()
    result = ledger.apply_market_phase(
        model_state,
        ([['BUY_PRODUCT', 'WHEAT', 1], ['SELL', 'WHEAT', 1]], []),
        trace=True,
    )
    assert result.after_town.players[0].money == model_state.players[0].money
    assert result.after_town.inventory == model_state.inventory
    assert result.after_town.players[0].shed == model_state.players[0].shed
    quotes = [event.quoted_price for event in result.order_events]
    assert quotes[0] == quotes[1]


def test_lockstep_sellers_receive_same_quote():
    players = (
        _account(shed=_shed(WHEAT=1)),
        _account(shed=_shed(WHEAT=1)),
    )
    result = ledger.apply_market_phase(
        _state(players=players),
        ([['SELL', 'WHEAT', 1]], [['SELL', 'WHEAT', 1]]),
        trace=True,
    )
    assert [event.quoted_price for event in result.order_events] == [25, 25]
    assert [event.item_inventory_before for event in result.order_events] == [10000, 10000]
    assert result.after_town.inventory_mapping()["WHEAT"] == 10002


def test_simultaneous_buy_and_sell_use_different_quote_rules():
    players = (_account(), _account(shed=_shed(WHEAT=1)))
    model_state = _state(players=players)
    queues = ([['BUY_PRODUCT', 'WHEAT', 1]], [['SELL', 'WHEAT', 1]])
    result = ledger.apply_market_phase(model_state, queues, trace=True)
    assert [event.quoted_price for event in result.order_events] == [26, 25]
    assert result.after_town.inventory == model_state.inventory
    environment = make("kaggriculture", configuration={"episodeSteps": 720})
    case = validator.ValidationCase("buy-sell", "boundary", model_state, queues)
    assert validator.compare_case(environment, case) is None


def test_later_units_use_updated_inventory():
    players = (_account(shed=_shed(WHEAT=3)), _account())
    result = ledger.apply_market_phase(
        _state(players=players),
        ([['SELL', 'WHEAT', 3]], []),
        trace=True,
    )
    assert [event.item_inventory_before for event in result.order_events] == [
        10000,
        10001,
        10002,
    ]
    assert [event.quoted_price for event in result.order_events] == [
        simulator.market_price("WHEAT", value)
        for value in (10000, 10001, 10002)
    ]


def test_floor_sales_pay_without_inventory_growth():
    players = (_account(money=0.0, shed=_shed(STRAWBERRY=2)), _account())
    model_state = _state(
        inventory=_inventory(STRAWBERRY=10100),
        players=players,
    )
    assert ledger.market_price("STRAWBERRY", 10100) == 1
    result = ledger.apply_market_phase(
        model_state,
        ([['SELL', 'STRAWBERRY', 2]], []),
    )
    assert result.after_town.players[0].money == 2
    assert result.after_town.inventory_mapping()["STRAWBERRY"] == 10100


def test_one_failed_order_does_not_stop_other_player():
    players = (_account(), _account(shed=_shed(WHEAT=3)))
    result = ledger.apply_market_phase(
        _state(players=players),
        ([['SELL', 'WHEAT', 3]], [['SELL', 'WHEAT', 3]]),
        trace=True,
    )
    first, *accepted = result.order_events
    assert first.player == 0
    assert first.accepted is False
    assert first.failure_reason == "unavailable"
    assert len(accepted) == 3
    assert all(event.accepted for event in accepted)


@pytest.mark.parametrize(
    "queue",
    (
        (),
        [()],
        [[]],
        [["SELL"]],
        [["SELL", "WHEAT", 0]],
        [["SELL", "WHEAT", -1]],
        [["SELL", "WHEAT", "bad"]],
        [["UNKNOWN"]],
    ),
)
def test_parser_cases_match_simulator(queue):
    environment = make("kaggriculture", configuration={"episodeSteps": 720})
    case = validator.ValidationCase("parser", "boundary", _state(), (queue, []))
    assert validator.compare_case(environment, case) is None


def test_parser_accepts_simulator_integer_conversion():
    player = _account(shed=_shed(WHEAT=5))
    model_state = _state(players=(player, _account()))
    for quantity, expected in ((True, 1), (2.9, 2), ("3", 3)):
        result = ledger.apply_market_phase(
            model_state,
            ([['SELL', 'WHEAT', quantity, 'EXTRA']], []),
        )
        assert result.after_town.players[0].shed_mapping()["WHEAT"] == 5 - expected


def test_trace_records_unsupported_item():
    result = ledger.apply_market_phase(
        _state(),
        ([['BUY_SEED', 'INVALID', 1]], []),
        trace=True,
    )
    assert len(result.order_events) == 1
    event = result.order_events[0]
    assert event.accepted is False
    assert event.failure_reason == "unsupported_item"


def test_market_order_limit_matches_simulator():
    config = ledger.MarketConfig(max_orders=1)
    model_state = _state(config=config)
    result = ledger.apply_market_phase(
        model_state,
        ([['HIRE'], ['HIRE']], []),
    )
    assert result.after_town.players[0].hands == 1


def test_atomic_cash_and_structural_boundaries():
    config = ledger.MarketConfig(hire_multiplier=3)
    player = _account(money=2.0, hires_today=0, hands=0)
    result = ledger.apply_market_phase(
        _state(players=(player, _account()), config=config),
        ([['HIRE']], []),
        trace=True,
    )
    assert result.after_town.players[0] == player
    assert result.order_events[0].failure_reason == "insufficient_cash"
    player = _account(money=1000.0)
    result = ledger.apply_market_phase(
        _state(players=(player, _account())),
        ([['BUY_LAND']], []),
    )
    assert result.after_town.players[0].money == 0
    assert result.after_town.players[0].unlocked_quadrants == 2


def test_shed_capacity_excludes_seeds():
    config = ledger.MarketConfig(shed_capacity=1)
    player = _account(money=1000.0, shed=_shed(WHEAT=1))
    result = ledger.apply_market_phase(
        _state(players=(player, _account()), config=config),
        (
            [
                ['BUY_PRODUCT', 'FERTILIZER', 1],
                ['BUY_ANIMAL', 'GOOSE', 1],
                ['BUY_SEED', 'CARROT', 1],
            ],
            [],
        ),
    )
    account_after = result.after_town.players[0]
    assert account_after.shed == player.shed
    assert account_after.seed_mapping()["CARROT"] == 1
    assert account_after.money == 980


def test_town_timing_and_duplicate_demand():
    shops = ("ICE_CREAM_SHOP", "ICE_CREAM_SHOP", "YARN_STORE")
    model_state = _state(step=0, shops=shops)
    traced = ledger.apply_market_phase(model_state, ([], []), trace=True)
    inventory = traced.after_town.inventory_mapping()
    assert inventory["WHEAT"] == 9997
    assert inventory["STRAWBERRY"] == 9997
    assert inventory["MILK"] == 9997
    assert inventory["WOOL"] == 9997
    assert inventory["FERTILIZER"] == 10000
    assert len(traced.town_events) == 15
    quiet = ledger.apply_market_phase(
        dataclasses.replace(model_state, source_step=1),
        ([], []),
        trace=True,
    )
    assert quiet.after_town.inventory == model_state.inventory
    assert quiet.town_events == ()


def test_trace_does_not_change_result_and_inputs_are_immutable():
    model_state = _state(players=(_account(shed=_shed(WHEAT=2)), _account()))
    original = copy.deepcopy(model_state)
    without_trace = ledger.apply_market_phase(
        model_state,
        ([['SELL', 'WHEAT', 2]], []),
    )
    with_trace = ledger.apply_market_phase(
        model_state,
        ([['SELL', 'WHEAT', 2]], []),
        trace=True,
    )
    assert without_trace.after_town == with_trace.after_town
    assert without_trace.order_events == ()
    assert without_trace.town_events == ()
    assert model_state == original
    with pytest.raises(dataclasses.FrozenInstanceError):
        model_state.source_step = 2


def test_state_validation_rejects_invalid_vectors():
    with pytest.raises(TypeError):
        ledger.PlayerAccount(1.0, [0] * len(ledger.SHED_ITEMS), (0,) * len(ledger.CROPS), 0, 1, 0)
    with pytest.raises(ValueError):
        _account(shed=_shed(WHEAT=-1))
    missing = _inventory()
    del missing["WHEAT"]
    with pytest.raises(ValueError):
        _state(inventory=missing)
    with pytest.raises(TypeError):
        _state(step=True)


def test_simulator_exception_parity():
    cases = (
        [["BUY_SEED", [], 1]],
        [["BUY_SEED", "WHEAT", float("inf")]],
    )
    for queue in cases:
        model_state = _state()
        environment = make("kaggriculture", configuration={"episodeSteps": 720})
        case = validator.ValidationCase("exception", "boundary", model_state, (queue, []))
        reference_state = validator._inject(environment, case)
        with pytest.raises((TypeError, OverflowError)) as model_error:
            ledger.apply_market_phase(model_state, (queue, []))
        with pytest.raises(type(model_error.value)):
            simulator._process_market(reference_state, environment)


def test_market_loop_safety_limit():
    player = _account(money=0.0, shed=_shed(WHEAT=ledger.MARKET_LOOP_LIMIT + 1))
    result = ledger.apply_market_phase(
        _state(players=(player, _account())),
        ([['SELL', 'WHEAT', ledger.MARKET_LOOP_LIMIT]], []),
    )
    assert result.after_town.players[0].shed_mapping()["WHEAT"] == 2


def test_model_has_no_runtime_or_agent_import():
    source = (TOOLS / "economics/market_ledger.py").read_text()
    assert "kaggle_environments" not in source
    assert "agent_2" not in source
    assert "v1_14" not in source


def test_validator_smoke_is_exact_and_deterministic():
    first = validator.run_validation(random_cases=100, boundaries=False)
    second = validator.run_validation(random_cases=100, boundaries=False)
    assert first["mismatches"] == 0
    assert first["failures"] == 0
    assert first["fixtures"] == 100
    assert first["input_sha256"] == second["input_sha256"]
    first.pop("elapsed_seconds")
    second.pop("elapsed_seconds")
    assert json.dumps(first, sort_keys=True) == json.dumps(second, sort_keys=True)
