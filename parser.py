from __future__ import annotations

import hashlib
import json
from typing import Any


REBALANCE_LIST_PATHS = (
    ("list",),
    ("items",),
    ("data", "list"),
    ("data", "items"),
    ("data", "rebalancings"),
    ("data", "rebalancing_history"),
    ("rebalancings",),
    ("rebalancing_history",),
)

HOLDING_PATHS = (
    ("view_rebalancing", "holdings"),
    ("rebalancing", "holdings"),
    ("current_rebalancing", "holdings"),
    ("last_success_rebalancing", "holdings"),
    ("data", "view_rebalancing", "holdings"),
    ("data", "rebalancing", "holdings"),
    ("data", "holdings"),
    ("cube", "view_rebalancing", "holdings"),
    ("holdings",),
    ("holding",),
    ("positions",),
    ("stocks",),
)


def stable_fingerprint(value: Any) -> str:
    payload = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.md5(payload.encode("utf-8")).hexdigest()


def extract_rebalance_records(payload: Any) -> list[dict[str, Any]]:
    if isinstance(payload, list):
        return [item for item in payload if isinstance(item, dict)]

    if not isinstance(payload, dict):
        return []

    for path in REBALANCE_LIST_PATHS:
        value = _path_get(payload, path)
        if _looks_like_rebalance_list(value):
            return value

    found = _find_first_list(payload, _looks_like_rebalance_list)
    return found or []


def parse_rebalance_event(
    record: dict[str, Any],
    cube_symbol: str,
    raw_file: str | None = None,
) -> dict[str, Any]:
    rb_id = _to_str(
        _first(
            record,
            "rebalance_id",
            "rb_id",
            "rebalancing_id",
            "id",
            "original_id",
        )
    )
    histories = _extract_change_rows(record)
    changes = [_parse_change(row) for row in histories]
    changes = [change for change in changes if _has_meaningful_security(change)]

    event = {
        "cube_symbol": cube_symbol,
        "rebalance_id": rb_id,
        "created_at": _to_int(
            _first(record, "created_at", "createdAt", "create_at", "time", "timestamp")
        ),
        "updated_at": _to_int(_first(record, "updated_at", "updatedAt", "update_at")),
        "status": _to_str(_first(record, "status", "state", "result")),
        "cash_weight": _to_float(
            _first(
                record,
                "cash_weight",
                "cash",
                "cash_value",
                "cashWeight",
                "target_cash",
            )
        ),
        "changes": changes,
        "raw_file": raw_file,
    }
    event["fingerprint"] = stable_fingerprint(_without_runtime_fields(event))
    return event


def parse_holding_snapshot(
    payload: Any,
    cube_symbol: str,
    rebalance_id: str | int | None = None,
    raw_file: str | None = None,
    source: str = "api",
) -> dict[str, Any]:
    holdings = [_parse_holding(row) for row in _extract_holding_rows(payload)]
    holdings = [holding for holding in holdings if _has_meaningful_security(holding)]

    snapshot_time = _to_int(
        _first_deep(payload, "snapshot_time", "updated_at", "updatedAt", "created_at", "time", "timestamp")
    )
    cash_weight = _to_float(
        _first_deep(payload, "cash_weight", "cash", "cash_value", "cashWeight", "target_cash")
    )

    normalized_identity = {
        "rebalance_id": _to_str(rebalance_id),
        "snapshot_time": snapshot_time,
        "cash_weight": cash_weight,
        "holdings": holdings,
        "source": source,
    }
    holdings_fingerprint = stable_fingerprint(normalized_identity)
    snapshot_id = _to_str(rebalance_id) or (
        f"{snapshot_time or 'unknown'}-{holdings_fingerprint[:12]}"
    )

    snapshot = {
        "cube_symbol": cube_symbol,
        "snapshot_id": snapshot_id,
        "rebalance_id": _to_str(rebalance_id),
        "snapshot_time": snapshot_time,
        "cash_weight": cash_weight,
        "holdings": holdings,
        "source": source,
        "raw_file": raw_file,
    }
    snapshot["fingerprint"] = stable_fingerprint(_without_runtime_fields(snapshot))
    return snapshot


def reconstruct_snapshot_from_rebalance(
    event: dict[str, Any],
    raw_file: str | None = None,
) -> dict[str, Any]:
    holdings = []
    for change in event.get("changes", []):
        new_weight = change.get("new_weight")
        if new_weight is None or new_weight <= 0:
            continue
        holdings.append(
            {
                "stock_name": change.get("stock_name"),
                "stock_symbol": change.get("stock_symbol"),
                "weight": new_weight,
                "price": change.get("price"),
                "profit_rate": None,
            }
        )

    snapshot = {
        "cube_symbol": event.get("cube_symbol"),
        "snapshot_id": f"{event.get('rebalance_id') or event.get('fingerprint')}:reconstructed",
        "rebalance_id": event.get("rebalance_id"),
        "snapshot_time": event.get("updated_at") or event.get("created_at"),
        "cash_weight": event.get("cash_weight"),
        "holdings": holdings,
        "source": "reconstructed",
        "raw_file": raw_file or event.get("raw_file"),
    }
    snapshot["fingerprint"] = stable_fingerprint(_without_runtime_fields(snapshot))
    return snapshot


def extract_stock_quote_map(payload: Any) -> dict[str, dict[str, Any]]:
    quotes: dict[str, dict[str, Any]] = {}
    for item in _extract_quote_items(payload):
        quote = item.get("quote") if isinstance(item.get("quote"), dict) else item
        symbol = _to_str(quote.get("symbol")).upper()
        if not symbol:
            continue
        quotes[symbol] = {
            "stock_name": _to_str(quote.get("name")),
            "stock_symbol": symbol,
            "current": _to_float(quote.get("current") or quote.get("current_ext")),
            "percent": _to_float(quote.get("percent")),
            "timestamp": _to_int(
                quote.get("timestamp") or quote.get("time") or quote.get("timestamp_ext")
            ),
        }
    return quotes


def apply_stock_quotes_to_snapshot(
    snapshot: dict[str, Any],
    quote_map: dict[str, dict[str, Any]],
) -> dict[str, Any]:
    enriched = dict(snapshot)
    enriched_holdings = []
    for holding in snapshot.get("holdings", []):
        enriched_holding = dict(holding)
        quote = quote_map.get(_to_str(holding.get("stock_symbol")).upper())
        if quote:
            if quote.get("stock_name") and not enriched_holding.get("stock_name"):
                enriched_holding["stock_name"] = quote["stock_name"]
            if quote.get("current") is not None:
                enriched_holding["price"] = quote["current"]
                enriched_holding["price_source"] = "stock_quote"
            if quote.get("percent") is not None:
                enriched_holding["profit_rate"] = quote["percent"]
                enriched_holding["profit_rate_source"] = "stock_quote_percent"
            if quote.get("timestamp") is not None:
                enriched_holding["quote_time"] = quote["timestamp"]
        enriched_holdings.append(enriched_holding)
    enriched["holdings"] = enriched_holdings
    if quote_map and enriched_holdings:
        enriched["quote_source"] = "stock_batch_quote"
        if "stock_quote" not in _to_str(enriched.get("source")):
            enriched["source"] = f"{enriched.get('source') or 'snapshot'}+stock_quote"
    return enriched


def _parse_change(row: dict[str, Any]) -> dict[str, Any]:
    return {
        "stock_name": _to_str(
            _first(row, "stock_name", "name", "stockName", "stock_name_cn", "stockNameCn")
        ),
        "stock_symbol": _to_str(
            _first(row, "stock_symbol", "symbol", "stockSymbol", "market_symbol")
        ),
        "old_weight": _to_float(
            _first(
                row,
                "prev_weight",
                "old_weight",
                "before_weight",
                "original_weight",
                "prevWeight",
                "oldWeight",
            )
        ),
        "new_weight": _to_float(
            _first(
                row,
                "target_weight",
                "new_weight",
                "after_weight",
                "weight",
                "targetWeight",
                "newWeight",
            )
        ),
        "price": _to_float(
            _first(row, "price", "deal_price", "trade_price", "current_price", "last_price")
        ),
    }


def _parse_holding(row: dict[str, Any]) -> dict[str, Any]:
    return {
        "stock_name": _to_str(
            _first(row, "stock_name", "name", "stockName", "stock_name_cn", "stockNameCn")
        ),
        "stock_symbol": _to_str(
            _first(row, "stock_symbol", "symbol", "stockSymbol", "market_symbol")
        ),
        "weight": _to_float(
            _first(row, "weight", "target_weight", "new_weight", "holding_weight", "percent")
        ),
        "price": _to_float(
            _first(row, "price", "current_price", "last_price", "market_price", "latest_price")
        ),
        "profit_rate": _to_float(
            _first(row, "profit_rate", "profitRate", "gain_percent", "gainPercent", "yield")
        ),
    }


def _extract_change_rows(record: dict[str, Any]) -> list[dict[str, Any]]:
    for key in ("rebalancing_histories", "rebalancing_history", "histories", "changes", "details"):
        value = record.get(key)
        if _looks_like_security_list(value):
            return value
    found = _find_first_list(record, _looks_like_security_list)
    return found or []


def _extract_holding_rows(payload: Any) -> list[dict[str, Any]]:
    if isinstance(payload, dict):
        for path in HOLDING_PATHS:
            value = _path_get(payload, path)
            if _looks_like_security_list(value):
                return value
        found = _find_first_holding_list(payload)
        return found or []
    if _looks_like_holding_list(payload):
        return payload
    return []


def _extract_quote_items(payload: Any) -> list[dict[str, Any]]:
    if isinstance(payload, dict):
        data = payload.get("data", payload)
        if isinstance(data, dict):
            items = data.get("items")
            if isinstance(items, list):
                return [item for item in items if isinstance(item, dict)]
            quote = data.get("quote")
            if isinstance(quote, dict):
                return [quote]
        items = payload.get("items")
        if isinstance(items, list):
            return [item for item in items if isinstance(item, dict)]
    if isinstance(payload, list):
        return [item for item in payload if isinstance(item, dict)]
    return []


def _looks_like_rebalance_list(value: Any) -> bool:
    if not isinstance(value, list):
        return False
    dict_items = [item for item in value if isinstance(item, dict)]
    if not dict_items:
        return False
    signals = {"rebalancing_histories", "rebalancing_history", "rebalance_id", "rb_id", "created_at", "updated_at"}
    return any(signals.intersection(item.keys()) for item in dict_items)


def _looks_like_security_list(value: Any) -> bool:
    if not isinstance(value, list):
        return False
    dict_items = [item for item in value if isinstance(item, dict)]
    if not dict_items:
        return False
    signals = {
        "stock_name",
        "stock_symbol",
        "stockName",
        "stockSymbol",
        "symbol",
        "name",
        "target_weight",
        "prev_weight",
        "weight",
    }
    return any(signals.intersection(item.keys()) for item in dict_items)


def _looks_like_holding_list(value: Any) -> bool:
    if not isinstance(value, list):
        return False
    dict_items = [item for item in value if isinstance(item, dict)]
    if not dict_items:
        return False
    holding_signals = {
        "weight",
        "holding_weight",
        "percent",
        "profit_rate",
        "profitRate",
        "gain_percent",
        "current_price",
        "last_price",
        "market_value",
    }
    return any(holding_signals.intersection(item.keys()) for item in dict_items)


def _find_first_holding_list(value: Any, parent_key: str = "") -> list[dict[str, Any]] | None:
    if parent_key in {"rebalancing_histories", "rebalancing_history", "histories", "changes"}:
        return None
    if _looks_like_holding_list(value):
        return value
    if isinstance(value, dict):
        for key, child in value.items():
            found = _find_first_holding_list(child, key)
            if found:
                return found
    elif isinstance(value, list):
        for child in value:
            found = _find_first_holding_list(child, parent_key)
            if found:
                return found
    return None


def _has_meaningful_security(item: dict[str, Any]) -> bool:
    return bool(item.get("stock_name") or item.get("stock_symbol") or item.get("weight") is not None)


def _path_get(value: Any, path: tuple[str, ...]) -> Any:
    current = value
    for part in path:
        if not isinstance(current, dict):
            return None
        current = current.get(part)
    return current


def _find_first_list(value: Any, predicate) -> list[dict[str, Any]] | None:
    if predicate(value):
        return value
    if isinstance(value, dict):
        for child in value.values():
            found = _find_first_list(child, predicate)
            if found:
                return found
    elif isinstance(value, list):
        for child in value:
            found = _find_first_list(child, predicate)
            if found:
                return found
    return None


def _first(mapping: dict[str, Any], *keys: str) -> Any:
    for key in keys:
        if key in mapping and mapping[key] is not None:
            return mapping[key]
    return None


def _first_deep(value: Any, *keys: str) -> Any:
    if isinstance(value, dict):
        for key in keys:
            if key in value and value[key] is not None:
                return value[key]
        for child in value.values():
            found = _first_deep(child, *keys)
            if found is not None:
                return found
    elif isinstance(value, list):
        for child in value:
            found = _first_deep(child, *keys)
            if found is not None:
                return found
    return None


def _to_float(value: Any) -> float | None:
    if value in (None, ""):
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _to_int(value: Any) -> int | None:
    if value in (None, ""):
        return None
    try:
        return int(float(value))
    except (TypeError, ValueError):
        return None


def _to_str(value: Any) -> str:
    if value in (None, ""):
        return ""
    return str(value)


def _without_runtime_fields(value: dict[str, Any]) -> dict[str, Any]:
    return {key: item for key, item in value.items() if key not in {"raw_file", "fingerprint"}}
