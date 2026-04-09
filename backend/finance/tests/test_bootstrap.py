from __future__ import annotations

import pytest

from finance.services.bootstrap import _build_position_rows


def test_build_position_rows_infers_option_multiplier_from_symbol():
    rows = _build_position_rows(
        [
            {
                "symbol": "HIMS 260410C00020000",
                "side": "LONG",
                "quantity": 1,
                "average_cost": 1.5,
                "cost_basis": 0,
                "asset_type": "",
                "underlying_symbol": "",
            }
        ],
        {
            "HIMS 260410C00020000": {
                "payload": {
                    "quote": {
                        "last": 2.0,
                    }
                },
                "as_of": "2026-04-08T12:00:00Z",
            }
        },
    )

    assert len(rows) == 1
    row = rows[0]
    assert row["asset_type"] == "OPTION"
    assert row["display_symbol"] == "HIMS"
    assert row["cost_basis"] == pytest.approx(150.0)
    assert row["market_value"] == pytest.approx(200.0)
    assert row["gain_amount"] == pytest.approx(50.0)
    assert row["gain_percent"] == pytest.approx(33.3333333333)


def test_build_position_rows_infers_short_option_signs():
    rows = _build_position_rows(
        [
            {
                "symbol": "HIMS 260410C00020000",
                "side": "SHORT",
                "quantity": 1,
                "average_cost": 1.5,
                "cost_basis": 0,
                "asset_type": "",
                "underlying_symbol": "",
            }
        ],
        {
            "HIMS 260410C00020000": {
                "payload": {
                    "quote": {
                        "last": 2.0,
                    }
                },
                "as_of": "2026-04-08T12:00:00Z",
            }
        },
    )

    assert len(rows) == 1
    row = rows[0]
    assert row["quantity_display"] == pytest.approx(-1.0)
    assert row["cost_basis"] == pytest.approx(150.0)
    assert row["market_value"] == pytest.approx(200.0)
    assert row["gain_amount"] == pytest.approx(-50.0)
    assert row["gain_percent"] == pytest.approx(-33.3333333333)
