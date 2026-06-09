from __future__ import annotations

from typing import Any

from notifier import notify_holding_snapshot, notify_rebalance
from parser import (
    apply_stock_quotes_to_snapshot,
    extract_stock_quote_map,
    parse_holding_snapshot,
    parse_rebalance_event,
    reconstruct_snapshot_from_rebalance,
)
from storage import Storage
from xueqiu_client import XueqiuClient, XueqiuEndpointError


def store_rebalance_if_new(
    storage: Storage,
    cube_symbol: str,
    record: dict[str, Any],
    *,
    announce: bool,
) -> tuple[dict[str, Any], bool]:
    event = parse_rebalance_event(record, cube_symbol)
    if storage.has_rebalance(event):
        return event, False

    identity = f"{cube_symbol}_{event.get('rebalance_id') or event['fingerprint']}"
    raw_file = storage.save_raw_rebalance(identity, record)
    event["raw_file"] = raw_file
    storage.append_rebalance(event)
    if announce:
        notify_rebalance(event)
    return event, True


def fetch_store_snapshot_for_event(
    client: XueqiuClient,
    storage: Storage,
    cube_symbol: str,
    event: dict[str, Any],
    *,
    record: dict[str, Any] | None = None,
    announce: bool,
    allow_current_fallback: bool = False,
    allow_reconstructed: bool = True,
) -> tuple[dict[str, Any] | None, bool]:
    snapshot = resolve_snapshot_for_event(
        client,
        cube_symbol,
        event,
        record=record,
        allow_current_fallback=allow_current_fallback,
        allow_reconstructed=allow_reconstructed,
    )
    if snapshot is None:
        return None, False

    if snapshot.get("_raw_payload") is not None:
        raw_payload = snapshot.pop("_raw_payload")
        identity_suffix = snapshot.pop("_raw_identity_suffix", snapshot.get("snapshot_id") or "snapshot")
        return _store_snapshot_if_new(storage, snapshot, raw_payload, cube_symbol, identity_suffix, announce)

    if storage.has_snapshot(snapshot):
        return snapshot, False
    storage.append_snapshot(snapshot)
    if announce:
        notify_holding_snapshot(snapshot)
    return snapshot, True


def resolve_snapshot_for_event(
    client: XueqiuClient,
    cube_symbol: str,
    event: dict[str, Any],
    *,
    record: dict[str, Any] | None = None,
    allow_current_fallback: bool = False,
    allow_reconstructed: bool = True,
) -> dict[str, Any] | None:
    rb_id = event.get("rebalance_id")

    if record is not None:
        snapshot = parse_holding_snapshot(
            record,
            cube_symbol,
            rebalance_id=rb_id,
            raw_file=event.get("raw_file"),
            source="history_record",
        )
        if snapshot.get("holdings"):
            snapshot["_raw_payload"] = record
            snapshot["_raw_identity_suffix"] = "history_record"
            return snapshot

    if rb_id:
        try:
            responses = client.get_holding_snapshot_candidates(rb_id, cube_symbol)
        except XueqiuEndpointError:
            responses = []
        for response in responses:
            snapshot = parse_holding_snapshot(
                response.data,
                cube_symbol,
                rebalance_id=rb_id,
                source=response.label,
            )
            if not snapshot.get("holdings"):
                continue
            snapshot["_raw_payload"] = response.data
            snapshot["_raw_identity_suffix"] = f"{rb_id}_{response.label}"
            return snapshot

    if allow_current_fallback:
        try:
            response = client.get_current_cube_detail(cube_symbol)
        except XueqiuEndpointError:
            response = None
        if response is not None:
            snapshot = parse_holding_snapshot(
                response.data,
                cube_symbol,
                rebalance_id=rb_id,
                source=response.label,
            )
            if snapshot.get("holdings"):
                snapshot["_raw_payload"] = response.data
                snapshot["_raw_identity_suffix"] = f"{rb_id or 'current'}_{response.label}"
                return snapshot

    if allow_reconstructed:
        snapshot = reconstruct_snapshot_from_rebalance(event, raw_file=event.get("raw_file"))
        if snapshot.get("holdings"):
            return snapshot

    return None


def enrich_snapshot_with_live_quotes(
    client: XueqiuClient,
    snapshot: dict[str, Any] | None,
) -> dict[str, Any] | None:
    if snapshot is None:
        return None

    symbols = [
        holding.get("stock_symbol", "")
        for holding in snapshot.get("holdings", [])
        if holding.get("stock_symbol")
    ]
    if not symbols:
        return snapshot

    try:
        quote_payload = client.get_stock_batch_quotes(symbols)
    except XueqiuEndpointError:
        return snapshot
    quote_map = extract_stock_quote_map(quote_payload)
    return apply_stock_quotes_to_snapshot(snapshot, quote_map)


def resolve_current_cube_snapshot(
    client: XueqiuClient,
    cube_symbol: str,
) -> dict[str, Any] | None:
    try:
        response = client.get_current_cube_detail(cube_symbol)
    except XueqiuEndpointError:
        return None

    snapshot = parse_holding_snapshot(
        response.data,
        cube_symbol,
        source=response.label,
    )
    if not snapshot.get("holdings"):
        return None
    return enrich_snapshot_with_live_quotes(client, snapshot)


def _store_snapshot_if_new(
    storage: Storage,
    snapshot: dict[str, Any],
    raw_payload: Any,
    cube_symbol: str,
    identity_suffix: str,
    announce: bool,
) -> tuple[dict[str, Any], bool]:
    if storage.has_snapshot(snapshot):
        return snapshot, False

    raw_identity = f"{cube_symbol}_{identity_suffix}_{snapshot.get('fingerprint', '')[:12]}"
    raw_file = storage.save_raw_holding(raw_identity, raw_payload)
    snapshot["raw_file"] = raw_file
    storage.append_snapshot(snapshot)
    if announce:
        notify_holding_snapshot(snapshot)
    return snapshot, True
