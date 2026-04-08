from __future__ import annotations

from tools.models import ToolRisk

FINANCE_TOOL_NAMES = [
    "ticker_lookup",
    "watchlist_add",
    "watchlist_remove",
    "watchlist_list",
    "portfolio_get",
    "broker_accounts",
    "broker_balances",
    "broker_positions",
    "broker_activity",
    "get_market_hours",
    "stock_quote",
    "stock_history",
    "stock_news",
    "stock_filings",
    "research_snapshot_get",
    "research_snapshot_refresh",
]


def build_finance_tool_group() -> dict[str, object]:
    return {
        "name": "Finance Research",
        "description": (
            "Market-data, portfolio, and watchlist tools for the finance research cockpit. "
            "Watchlist changes are initiated from chat, and read-only broker context comes from "
            "the Schwab Trader API - Individual."
        ),
        "tools": [
            {
                "name": "ticker_lookup",
                "description": "Resolve a ticker or company query into canonical finance metadata from the ticker universe.",
                "risk": ToolRisk.SAFE,
                "requires_approval": False,
                "released": True,
                "args_schema": {
                    "type": "object",
                    "additionalProperties": False,
                    "required": ["query"],
                    "properties": {
                        "query": {
                            "type": "string",
                            "minLength": 1,
                            "description": "Symbol, company name, or alias to resolve.",
                        }
                    },
                },
            },
            {
                "name": "watchlist_add",
                "description": "Add a ticker to the active finance watchlist through chat.",
                "risk": ToolRisk.ELEVATED,
                "requires_approval": False,
                "released": True,
                "args_schema": {
                    "type": "object",
                    "additionalProperties": False,
                    "required": ["symbol"],
                    "properties": {
                        "symbol": {"type": "string", "minLength": 1},
                        "watchlist_name": {"type": "string", "default": "AI Watchlist"},
                        "note": {"type": "string"},
                    },
                },
            },
            {
                "name": "watchlist_remove",
                "description": "Remove a ticker from the active finance watchlist through chat.",
                "risk": ToolRisk.ELEVATED,
                "requires_approval": False,
                "released": True,
                "args_schema": {
                    "type": "object",
                    "additionalProperties": False,
                    "required": ["symbol"],
                    "properties": {
                        "symbol": {"type": "string", "minLength": 1},
                        "watchlist_name": {"type": "string", "default": "AI Watchlist"},
                    },
                },
            },
            {
                "name": "watchlist_list",
                "description": "List watchlist contents for the current finance context.",
                "risk": ToolRisk.SAFE,
                "requires_approval": False,
                "released": True,
                "args_schema": {
                    "type": "object",
                    "additionalProperties": False,
                    "properties": {
                        "watchlist_name": {"type": "string", "default": "AI Watchlist"},
                    },
                },
            },
            {
                "name": "portfolio_get",
                "description": "Fetch the active portfolio summary and holdings context.",
                "risk": ToolRisk.SAFE,
                "requires_approval": False,
                "released": True,
                "args_schema": {
                    "type": "object",
                    "additionalProperties": False,
                    "properties": {
                        "portfolio_id": {"type": "string", "default": "current"},
                    },
                },
            },
            {
                "name": "broker_accounts",
                "description": "List Schwab accounts available to the user through the Trader API.",
                "risk": ToolRisk.SAFE,
                "requires_approval": False,
                "released": True,
                "args_schema": {
                    "type": "object",
                    "additionalProperties": False,
                    "properties": {},
                },
            },
            {
                "name": "broker_balances",
                "description": "Fetch Schwab balances and buying power for an account.",
                "risk": ToolRisk.SAFE,
                "requires_approval": False,
                "released": True,
                "args_schema": {
                    "type": "object",
                    "additionalProperties": False,
                    "properties": {
                        "account_id": {"type": "string"},
                    },
                },
            },
            {
                "name": "broker_positions",
                "description": "Fetch Schwab positions for an account.",
                "risk": ToolRisk.SAFE,
                "requires_approval": False,
                "released": True,
                "args_schema": {
                    "type": "object",
                    "additionalProperties": False,
                    "properties": {
                        "account_id": {"type": "string"},
                    },
                },
            },
            {
                "name": "broker_activity",
                "description": "Fetch recent Schwab account activity or order history.",
                "risk": ToolRisk.SAFE,
                "requires_approval": False,
                "released": True,
                "args_schema": {
                    "type": "object",
                    "additionalProperties": False,
                    "properties": {
                        "account_id": {"type": "string"},
                        "limit": {"type": "integer", "minimum": 1, "default": 25},
                    },
                },
            },
            {
                "name": "get_market_hours",
                "description": "Fetch Schwab market hours for equities, options, bonds, futures, or forex and reuse the cached daily market-hours state.",
                "risk": ToolRisk.SAFE,
                "requires_approval": False,
                "released": True,
                "args_schema": {
                    "type": "object",
                    "additionalProperties": False,
                    "properties": {
                        "markets": {
                            "type": "array",
                            "items": {
                                "type": "string",
                                "enum": ["equity", "option", "bond", "future", "forex"],
                            },
                            "default": ["equity", "option"],
                            "description": "Markets to inspect. Equity includes premarket and after-hours windows.",
                        },
                        "date": {
                            "type": "string",
                            "description": "Optional YYYY-MM-DD date. Defaults to today.",
                        },
                    },
                },
            },
            {
                "name": "stock_quote",
                "description": "Fetch the latest market quote for a ticker.",
                "risk": ToolRisk.SAFE,
                "requires_approval": False,
                "released": True,
                "args_schema": {
                    "type": "object",
                    "additionalProperties": False,
                    "required": ["symbol"],
                    "properties": {
                        "symbol": {"type": "string", "minLength": 1},
                    },
                },
            },
            {
                "name": "stock_history",
                "description": "Fetch historical OHLCV data for a ticker.",
                "risk": ToolRisk.SAFE,
                "requires_approval": False,
                "released": True,
                "args_schema": {
                    "type": "object",
                    "additionalProperties": False,
                    "required": ["symbol", "timeframe"],
                    "properties": {
                        "symbol": {"type": "string", "minLength": 1},
                        "timeframe": {"type": "string", "minLength": 1},
                        "start": {"type": "string"},
                        "end": {"type": "string"},
                    },
                },
            },
            {
                "name": "stock_news",
                "description": "Fetch recent news for a ticker.",
                "risk": ToolRisk.SAFE,
                "requires_approval": False,
                "released": True,
                "args_schema": {
                    "type": "object",
                    "additionalProperties": False,
                    "required": ["symbol"],
                    "properties": {
                        "symbol": {"type": "string", "minLength": 1},
                        "limit": {"type": "integer", "minimum": 1, "default": 10},
                    },
                },
            },
            {
                "name": "stock_filings",
                "description": "Fetch recent SEC filings for a ticker.",
                "risk": ToolRisk.SAFE,
                "requires_approval": False,
                "released": True,
                "args_schema": {
                    "type": "object",
                    "additionalProperties": False,
                    "required": ["symbol"],
                    "properties": {
                        "symbol": {"type": "string", "minLength": 1},
                        "filing_types": {
                            "type": "array",
                            "items": {"type": "string"},
                            "default": ["8-K", "10-K", "10-Q"],
                        },
                        "limit": {"type": "integer", "minimum": 1, "default": 10},
                    },
                },
            },
            {
                "name": "research_snapshot_get",
                "description": "Fetch a cached finance research snapshot for a ticker, portfolio, or watchlist.",
                "risk": ToolRisk.SAFE,
                "requires_approval": False,
                "released": True,
                "args_schema": {
                    "type": "object",
                    "additionalProperties": False,
                    "properties": {
                        "ticker": {"type": "string"},
                        "portfolio_id": {"type": "string"},
                        "watchlist_name": {"type": "string"},
                    },
                },
            },
            {
                "name": "research_snapshot_refresh",
                "description": "Queue a refresh for a cached finance research snapshot.",
                "risk": ToolRisk.SAFE,
                "requires_approval": False,
                "released": True,
                "args_schema": {
                    "type": "object",
                    "additionalProperties": False,
                    "properties": {
                        "ticker": {"type": "string"},
                        "portfolio_id": {"type": "string"},
                        "watchlist_name": {"type": "string"},
                    },
                },
            },
        ],
    }
