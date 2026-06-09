from __future__ import annotations

import json
import re
import tempfile
from pathlib import Path
from typing import Any


class Storage:
    def __init__(self, data_dir: Path | str) -> None:
        self.data_dir = Path(data_dir)
        self.raw_rebalancing_dir = self.data_dir / "raw" / "rebalancing"
        self.raw_holdings_dir = self.data_dir / "raw" / "holdings"
        self.parsed_dir = self.data_dir / "parsed"
        self.state_path = self.data_dir / "state.json"
        self.rebalances_path = self.parsed_dir / "rebalances.jsonl"
        self.snapshots_path = self.parsed_dir / "holding_snapshots.jsonl"
        self._ensure_dirs()
        self.state = self._load_state()

    def has_rebalance(self, event: dict[str, Any]) -> bool:
        return self._rebalance_key(event) in self.state.get("seen_rebalance_keys", [])

    def has_snapshot(self, snapshot: dict[str, Any]) -> bool:
        return self._snapshot_key(snapshot) in self.state.get("seen_snapshot_keys", [])

    def has_any_rebalance_state(self) -> bool:
        return bool(
            self.state.get("last_rebalance_fingerprint")
            or self.state.get("seen_rebalance_keys")
            or self.state.get("seen_rebalance_ids")
        )

    def save_raw_rebalance(self, identity: str, payload: Any) -> str:
        return self._save_raw(self.raw_rebalancing_dir, identity, payload)

    def save_raw_holding(self, identity: str, payload: Any) -> str:
        return self._save_raw(self.raw_holdings_dir, identity, payload)

    def append_rebalance(self, event: dict[str, Any]) -> None:
        key = self._rebalance_key(event)
        self._append_jsonl(self.rebalances_path, event)
        self.mark_rebalance_seen(event)

    def mark_rebalance_seen(self, event: dict[str, Any]) -> None:
        key = self._rebalance_key(event)
        self._remember("seen_rebalance_keys", key)
        if event.get("rebalance_id"):
            self._remember("seen_rebalance_ids", event["rebalance_id"])
        self.state["last_rebalance_fingerprint"] = event.get("fingerprint")
        self._save_state()

    def append_snapshot(self, snapshot: dict[str, Any]) -> None:
        key = self._snapshot_key(snapshot)
        self._append_jsonl(self.snapshots_path, snapshot)
        self._remember("seen_snapshot_keys", key)
        if snapshot.get("snapshot_id"):
            self._remember("seen_snapshot_ids", snapshot["snapshot_id"])
        self.state["last_holding_snapshot_fingerprint"] = snapshot.get("fingerprint")
        self._save_state()

    def _ensure_dirs(self) -> None:
        self.raw_rebalancing_dir.mkdir(parents=True, exist_ok=True)
        self.raw_holdings_dir.mkdir(parents=True, exist_ok=True)
        self.parsed_dir.mkdir(parents=True, exist_ok=True)

    def _load_state(self) -> dict[str, Any]:
        default_state = {
            "last_rebalance_fingerprint": None,
            "last_holding_snapshot_fingerprint": None,
            "seen_rebalance_ids": [],
            "seen_snapshot_ids": [],
            "seen_rebalance_keys": [],
            "seen_snapshot_keys": [],
        }
        if not self.state_path.exists():
            return default_state
        try:
            loaded = json.loads(self.state_path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            return default_state
        default_state.update({key: value for key, value in loaded.items() if value is not None})
        return default_state

    def _save_state(self) -> None:
        self._atomic_write_json(self.state_path, self.state)

    def _save_raw(self, directory: Path, identity: str, payload: Any) -> str:
        safe_identity = _safe_filename(identity)
        path = directory / f"{safe_identity}.json"
        counter = 2
        while path.exists():
            path = directory / f"{safe_identity}-{counter}.json"
            counter += 1
        self._atomic_write_json(path, payload)
        return path.relative_to(self.data_dir.parent).as_posix()

    def _append_jsonl(self, path: Path, record: dict[str, Any]) -> None:
        with path.open("a", encoding="utf-8") as file:
            file.write(json.dumps(record, ensure_ascii=False, sort_keys=True))
            file.write("\n")

    def _atomic_write_json(self, path: Path, payload: Any) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        with tempfile.NamedTemporaryFile(
            "w",
            encoding="utf-8",
            dir=path.parent,
            prefix=f".{path.name}.",
            delete=False,
        ) as tmp:
            json.dump(payload, tmp, ensure_ascii=False, indent=2, sort_keys=True)
            tmp.write("\n")
            tmp_path = Path(tmp.name)
        tmp_path.replace(path)

    def _remember(self, key: str, value: Any) -> None:
        values = self.state.setdefault(key, [])
        if value not in values:
            values.append(value)

    def _rebalance_key(self, event: dict[str, Any]) -> str:
        identity = event.get("rebalance_id") or "unknown"
        return f"{identity}:{event.get('fingerprint')}"

    def _snapshot_key(self, snapshot: dict[str, Any]) -> str:
        identity = snapshot.get("snapshot_id") or snapshot.get("rebalance_id") or "unknown"
        return f"{identity}:{snapshot.get('fingerprint')}"


def _safe_filename(value: str) -> str:
    cleaned = re.sub(r"[^A-Za-z0-9_.-]+", "_", value.strip())
    return cleaned.strip("._") or "payload"
