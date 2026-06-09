from __future__ import annotations

import argparse
import random
import sys
import time

from config import DEFAULT_CUBE_SYMBOL, ConfigError, load_settings, require_cookie
from parser import extract_rebalance_records
from storage import Storage
from workflow import fetch_store_snapshot_for_event, store_rebalance_if_new
from xueqiu_client import XueqiuAuthError, XueqiuClient, XueqiuRateLimitError


def main() -> int:
    args = parse_args()
    try:
        settings = load_settings(args.data_dir)
        require_cookie(settings)
    except ConfigError as exc:
        print(f"配置错误：{exc}", file=sys.stderr)
        return 2

    storage = Storage(settings.data_dir)
    client = XueqiuClient(
        cookie=settings.cookie,
        user_agent=settings.user_agent,
        timeout=settings.timeout,
        holding_endpoint_templates=settings.holding_endpoint_templates,
    )

    for page in range(1, args.max_pages + 1):
        try:
            payload = client.get_rebalancing_history(args.cube, page=page, count=args.count)
        except XueqiuAuthError as exc:
            print(f"Cookie 可能失效：{exc}", file=sys.stderr)
            return 2
        except XueqiuRateLimitError as exc:
            wait_seconds = random.randint(300, 900)
            print(f"触发限流：{exc}，暂停 {wait_seconds} 秒", file=sys.stderr)
            time.sleep(wait_seconds)
            continue

        storage.save_raw_rebalance(f"{args.cube}_history_page_{page}", payload)
        records = extract_rebalance_records(payload)
        print(f"page={page} records={len(records)}", flush=True)
        if not records:
            break

        for record in records:
            event, is_new_event = store_rebalance_if_new(
                storage,
                args.cube,
                record,
                announce=not args.quiet and not args.no_announce,
            )
            snapshot, is_new_snapshot = fetch_store_snapshot_for_event(
                client,
                storage,
                args.cube,
                event,
                record=record,
                announce=not args.quiet and not args.no_announce,
                allow_current_fallback=False,
                allow_reconstructed=not args.no_reconstruct,
            )
            if not args.quiet:
                print(
                    "  "
                    f"rb_id={event.get('rebalance_id') or 'unknown'} "
                    f"event={'new' if is_new_event else 'seen'} "
                    f"snapshot={'new' if is_new_snapshot else 'seen' if snapshot else 'missing'} "
                    f"source={snapshot.get('source') if snapshot else '-'}",
                    flush=True,
                )
            _sleep(args.sleep_min, args.sleep_max)

        _sleep(args.sleep_min, args.sleep_max)

    return 0


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Backfill Xueqiu cube rebalancing and holdings.")
    parser.add_argument("--cube", default=DEFAULT_CUBE_SYMBOL, help="Cube symbol, e.g. ZH2369777.")
    parser.add_argument("--max-pages", type=int, default=20, help="Maximum history pages to fetch.")
    parser.add_argument("--count", type=int, default=20, help="Rows per history page.")
    parser.add_argument("--sleep-min", type=float, default=1.0, help="Minimum sleep between requests.")
    parser.add_argument("--sleep-max", type=float, default=3.0, help="Maximum sleep between requests.")
    parser.add_argument("--data-dir", default=None, help="Override XQ_DATA_DIR.")
    parser.add_argument("--quiet", action="store_true", help="Only print page summaries.")
    parser.add_argument("--no-announce", action="store_true", help="Do not print rebalance/snapshot broadcasts.")
    parser.add_argument(
        "--no-reconstruct",
        action="store_true",
        help="Do not reconstruct holdings from rebalance changes when no holding API works.",
    )
    return parser.parse_args()


def _sleep(min_seconds: float, max_seconds: float) -> None:
    if max_seconds <= 0:
        return
    time.sleep(random.uniform(max(0, min_seconds), max(min_seconds, max_seconds)))


if __name__ == "__main__":
    raise SystemExit(main())
