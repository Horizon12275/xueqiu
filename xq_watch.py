from __future__ import annotations

import argparse
import random
import sys
import time
from datetime import datetime, time as dt_time, timedelta
from zoneinfo import ZoneInfo

from config import DEFAULT_CUBE_SYMBOL, ConfigError, load_settings, require_cookie
from notifier import (
    EmailNotifier,
    format_holding_snapshot,
    format_rebalance,
    notify_holding_snapshot,
    notify_rebalance,
)
from parser import extract_rebalance_records, parse_rebalance_event
from storage import Storage
from workflow import (
    enrich_snapshot_with_live_quotes,
    fetch_store_snapshot_for_event,
    resolve_current_cube_snapshot,
    resolve_snapshot_for_event,
    store_rebalance_if_new,
)
from xueqiu_client import XueqiuAuthError, XueqiuClient, XueqiuRateLimitError


MARKET_TZ = ZoneInfo("Asia/Shanghai")
MARKET_OPEN = dt_time(9, 0)
MARKET_CLOSE = dt_time(15, 30)


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
    email_notifier = EmailNotifier()
    if email_notifier.enabled():
        print("[email] notifier enabled", flush=True)
    else:
        print("[email] notifier disabled", flush=True)

    print(
        f"开始监听雪球组合 {args.cube}，interval_avg={args.interval}s，"
        f"jitter={args.jitter:.0%}，data_dir={settings.data_dir}",
        flush=True,
    )

    startup_email_sent = False
    skip_initial_notifications = (
        not args.notify_on_first_run and not storage.has_any_rebalance_state()
    )
    rate_limit_backoff = 300

    while True:
        if args.market_hours_only:
            wait_seconds = seconds_until_market_open()
            if wait_seconds > 0:
                next_open = datetime.now(MARKET_TZ) + timedelta(seconds=wait_seconds)
                print(
                    "当前不在 UTC+8 周一至周五 09:00-15:30 监听窗口内，"
                    f"暂停到 {next_open.strftime('%Y-%m-%d %H:%M:%S %Z')}，"
                    f"约 {wait_seconds / 60:.1f} 分钟。",
                    flush=True,
                )
                time.sleep(wait_seconds)
                continue

        try:
            payload = client.get_rebalancing_history(args.cube, page=1, count=args.count)
            rate_limit_backoff = 300
        except XueqiuAuthError as exc:
            print(f"Cookie 可能失效：{exc}", file=sys.stderr)
            return 2
        except XueqiuRateLimitError as exc:
            wait_seconds = min(900, rate_limit_backoff + random.randint(0, 120))
            print(f"触发限流：{exc}，暂停 {wait_seconds} 秒", file=sys.stderr)
            time.sleep(wait_seconds)
            rate_limit_backoff = min(900, rate_limit_backoff * 2)
            continue
        except Exception as exc:
            print(f"本轮请求失败：{exc}", file=sys.stderr)
            sleep_with_jitter(args.interval, args.jitter)
            continue

        records = extract_rebalance_records(payload)
        if records:
            latest_event = parse_rebalance_event(records[0], args.cube)
            latest_snapshot = None
            if not startup_email_sent or args.print_latest_each_poll:
                latest_snapshot = get_latest_snapshot(
                    client,
                    args.cube,
                    latest_event,
                    records[0],
                    allow_reconstructed=not args.no_reconstruct,
                )

            if not startup_email_sent:
                email_notifier.send(
                    f"【雪球组合监听启动】{args.cube}",
                    format_startup_email(
                        args=args,
                        data_dir=str(settings.data_dir),
                        latest_event=latest_event,
                        latest_snapshot=latest_snapshot,
                    ),
                )
                startup_email_sent = True

            if skip_initial_notifications:
                for record in records[: args.scan_count]:
                    event = parse_rebalance_event(record, args.cube)
                    storage.mark_rebalance_seen(event)
                print(
                    "首次启动：已记录当前最新调仓状态，本轮不发送旧调仓邮件。",
                    flush=True,
                )
                skip_initial_notifications = False
                sleep_with_jitter(args.interval, args.jitter)
                continue

            latest_fingerprint = None
            if args.print_latest_each_poll:
                latest_fingerprint = latest_event.get("fingerprint")
                print("【本轮最新调仓】", flush=True)
                notify_rebalance(latest_event)
                print("【本轮详细仓位】", flush=True)
                if latest_snapshot is not None:
                    notify_holding_snapshot(latest_snapshot)
                else:
                    print("未抓到详细仓位。", flush=True)

            for record in reversed(records[: args.scan_count]):
                event, is_new_event = store_rebalance_if_new(
                    storage,
                    args.cube,
                    record,
                    announce=not args.print_latest_each_poll,
                )
                if not is_new_event:
                    continue
                if (
                    args.print_latest_each_poll
                    and event.get("fingerprint") != latest_fingerprint
                ):
                    notify_rebalance(event)
                email_notifier.send(
                    f"【雪球组合调仓】{args.cube}",
                    format_rebalance(event),
                )
                fetch_store_snapshot_for_event(
                    client,
                    storage,
                    args.cube,
                    event,
                    record=record,
                    announce=True,
                    allow_current_fallback=args.current_fallback,
                    allow_reconstructed=not args.no_reconstruct,
                )
        else:
            print("未解析到调仓记录，本轮跳过。", flush=True)

        sleep_with_jitter(args.interval, args.jitter)


def get_latest_snapshot(
    client: XueqiuClient,
    cube: str,
    latest_event: dict,
    latest_record: dict,
    *,
    allow_reconstructed: bool,
) -> dict | None:
    latest_snapshot = resolve_current_cube_snapshot(client, cube)
    if latest_snapshot is None:
        latest_snapshot = resolve_snapshot_for_event(
            client,
            cube,
            latest_event,
            record=latest_record,
            allow_current_fallback=True,
            allow_reconstructed=allow_reconstructed,
        )
        latest_snapshot = enrich_snapshot_with_live_quotes(client, latest_snapshot)
    return latest_snapshot


def format_startup_email(
    *,
    args: argparse.Namespace,
    data_dir: str,
    latest_event: dict | None,
    latest_snapshot: dict | None,
) -> str:
    sections = [
        "雪球组合监听任务已启动，正在监听。",
        "",
        f"组合：{args.cube}",
        f"启动时间：{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}",
        f"平均轮询间隔：{args.interval} 秒",
        f"随机扰动：{args.jitter:.0%}",
        f"交易时段限制：{'开启，UTC+8 周一至周五 09:00-15:30' if args.market_hours_only else '关闭'}",
        f"数据目录：{data_dir}",
    ]
    if latest_event is not None:
        sections.extend(["", "当前最新调仓：", format_rebalance(latest_event)])
    else:
        sections.extend(["", "当前最新调仓：未解析到"])

    if latest_snapshot is not None:
        sections.extend(["", "当前详细仓位：", format_holding_snapshot(latest_snapshot)])
    else:
        sections.extend(["", "当前详细仓位：未抓到"])

    return "\n".join(sections)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Watch Xueqiu cube rebalancing and holdings.")
    parser.add_argument("--cube", default=DEFAULT_CUBE_SYMBOL, help="Cube symbol, e.g. ZH2369777.")
    parser.add_argument(
        "--interval",
        type=float,
        default=60,
        help="Average polling interval in seconds.",
    )
    parser.add_argument(
        "--jitter",
        type=float,
        default=0.0,
        help="Random jitter ratio around --interval. Example: 0.5 means 50%% below to 50%% above.",
    )
    parser.add_argument("--count", type=int, default=20, help="Rows to request from history page 1.")
    parser.add_argument(
        "--scan-count",
        type=int,
        default=5,
        help="How many newest records from page 1 to check each poll.",
    )
    parser.add_argument(
        "--print-latest-each-poll",
        action="store_true",
        help="Print the newest rebalance record on every polling request, even when it is already seen.",
    )
    parser.add_argument("--data-dir", default=None, help="Override XQ_DATA_DIR.")
    parser.add_argument(
        "--current-fallback",
        action="store_true",
        help="Use /cubes/show.json as a latest-snapshot fallback for new rebalances.",
    )
    parser.add_argument(
        "--no-reconstruct",
        action="store_true",
        help="Do not reconstruct holdings from rebalance changes when no holding API works.",
    )
    parser.add_argument(
        "--notify-on-first-run",
        action="store_true",
        help="Send email for already-visible rebalances on the first run with empty state.",
    )
    parser.add_argument(
        "--market-hours-only",
        action=argparse.BooleanOptionalAction,
        default=True,
        help="Only poll during UTC+8 Monday-Friday 09:00-15:30. Enabled by default.",
    )
    args = parser.parse_args()
    if args.interval <= 0:
        parser.error("--interval must be greater than 0")
    if args.jitter < 0:
        parser.error("--jitter must be greater than or equal to 0")
    return args


def is_market_window(now: datetime | None = None) -> bool:
    current = now.astimezone(MARKET_TZ) if now else datetime.now(MARKET_TZ)
    if current.weekday() >= 5:
        return False
    current_time = current.time()
    return MARKET_OPEN <= current_time <= MARKET_CLOSE


def seconds_until_market_open(now: datetime | None = None) -> float:
    current = now.astimezone(MARKET_TZ) if now else datetime.now(MARKET_TZ)
    if is_market_window(current):
        return 0.0

    for day_offset in range(8):
        candidate_date = current.date() + timedelta(days=day_offset)
        if candidate_date.weekday() >= 5:
            continue
        candidate_open = datetime.combine(candidate_date, MARKET_OPEN, tzinfo=MARKET_TZ)
        if candidate_open > current:
            return (candidate_open - current).total_seconds()

    return 3600.0


def sleep_with_jitter(interval: float, jitter: float) -> None:
    if jitter == 0:
        wait_seconds = interval
    else:
        lower = max(0.1, interval * (1 - jitter))
        upper = max(lower, interval * (1 + jitter))
        wait_seconds = random.uniform(lower, upper)
    print(f"下次轮询等待 {wait_seconds:.1f} 秒", flush=True)
    time.sleep(wait_seconds)


if __name__ == "__main__":
    raise SystemExit(main())
