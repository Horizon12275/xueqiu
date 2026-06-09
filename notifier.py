from __future__ import annotations

import os
import smtplib
from datetime import datetime
from email.message import EmailMessage
from typing import Any


def _bool_env(name: str, default: bool = False) -> bool:
    value = os.getenv(name)
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "y", "on"}


class EmailNotifier:
    def __init__(self) -> None:
        self.smtp_host = os.getenv("SMTP_HOST", "smtp.qq.com").strip()
        self.smtp_port = int(os.getenv("SMTP_PORT", "465"))
        self.smtp_ssl = _bool_env("SMTP_SSL", True)
        self.smtp_user = os.getenv("SMTP_USER", "").strip()
        self.smtp_password = os.getenv("SMTP_PASSWORD", "").strip()
        self.email_from = os.getenv("EMAIL_FROM", self.smtp_user).strip()
        self.email_to = os.getenv("EMAIL_TO", "").strip()

    def enabled(self) -> bool:
        return bool(
            self.smtp_host
            and self.smtp_port
            and self.smtp_user
            and self.smtp_password
            and self.email_from
            and self.email_to
        )

    def send(self, subject: str, content: str) -> bool:
        if not self.enabled():
            print("[email] disabled: missing SMTP env vars", flush=True)
            return False

        msg = EmailMessage()
        msg["Subject"] = subject
        msg["From"] = self.email_from
        msg["To"] = self.email_to
        msg.set_content(content)

        try:
            if self.smtp_ssl:
                with smtplib.SMTP_SSL(
                    self.smtp_host,
                    self.smtp_port,
                    timeout=15,
                ) as server:
                    server.login(self.smtp_user, self.smtp_password)
                    server.send_message(msg)
            else:
                with smtplib.SMTP(
                    self.smtp_host,
                    self.smtp_port,
                    timeout=15,
                ) as server:
                    server.starttls()
                    server.login(self.smtp_user, self.smtp_password)
                    server.send_message(msg)

            print(f"[email] sent to {self.email_to}: {subject}", flush=True)
            return True
        except Exception as exc:
            print(f"[email error] failed to send email: {exc}", flush=True)
            return False


def notify_rebalance(event: dict[str, Any]) -> None:
    print(format_rebalance(event), flush=True)


def format_rebalance(event: dict[str, Any]) -> str:
    sells, buys, adjusts = _classify_changes(event.get("changes", []))
    lines = [
        f"【雪球组合调仓】{event.get('cube_symbol', '')}",
        f"时间：{_format_time(event.get('updated_at') or event.get('created_at'))}",
        "",
    ]
    if sells:
        lines.append("卖出：")
        lines.extend(_format_change(change) for change in sells)
        lines.append("")
    if buys:
        lines.append("买入：")
        lines.extend(_format_change(change) for change in buys)
        lines.append("")
    if adjusts:
        lines.append("调整：")
        lines.extend(_format_change(change) for change in adjusts)
        lines.append("")
    lines.append(f"现金：{_format_percent(event.get('cash_weight'))}")
    return "\n".join(lines)


def notify_holding_snapshot(snapshot: dict[str, Any]) -> None:
    print(format_holding_snapshot(snapshot), flush=True)


def format_holding_snapshot(snapshot: dict[str, Any]) -> str:
    lines = [
        f"【雪球组合仓位快照】{snapshot.get('cube_symbol', '')}",
        f"时间：{_format_time(snapshot.get('snapshot_time'))}",
        "",
        "股票仓位：",
    ]
    holdings = snapshot.get("holdings", [])
    if holdings:
        lines.extend(_format_holding(holding) for holding in holdings)
    else:
        lines.append("- 未解析到股票仓位")
    lines.extend(
        [
            "",
            f"现金：{_format_percent(snapshot.get('cash_weight'))}",
            f"来源：{snapshot.get('source', 'api')}",
        ]
    )
    return "\n".join(lines)


def _classify_changes(
    changes: list[dict[str, Any]],
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], list[dict[str, Any]]]:
    sells = []
    buys = []
    adjusts = []
    for change in changes:
        old_weight = change.get("old_weight") or 0.0
        new_weight = change.get("new_weight") or 0.0
        if new_weight < old_weight:
            sells.append(change)
        elif new_weight > old_weight:
            buys.append(change)
        else:
            adjusts.append(change)
    return sells, buys, adjusts


def _format_change(change: dict[str, Any]) -> str:
    name = change.get("stock_name") or ""
    symbol = change.get("stock_symbol") or ""
    return (
        f"- {name} {symbol}："
        f"{_format_percent(change.get('old_weight'))} -> {_format_percent(change.get('new_weight'))}"
        f"，成交价 {_format_price(change.get('price'))}"
    )


def _format_holding(holding: dict[str, Any]) -> str:
    name = holding.get("stock_name") or ""
    symbol = holding.get("stock_symbol") or ""
    price_label = "现价" if holding.get("price_source") == "stock_quote" else "价格"
    percent_label = (
        "涨跌"
        if holding.get("profit_rate_source") == "stock_quote_percent"
        else "收益"
    )
    return (
        f"- {name} {symbol}：{_format_percent(holding.get('weight'))}"
        f"，{price_label} {_format_price(holding.get('price'))}"
        f"，{percent_label} {_format_signed_percent(holding.get('profit_rate'))}"
    )


def _format_time(value: Any) -> str:
    if value in (None, ""):
        return "未知"
    timestamp = float(value)
    if timestamp > 10_000_000_000:
        timestamp = timestamp / 1000
    return datetime.fromtimestamp(timestamp).strftime("%Y-%m-%d %H:%M:%S")


def _format_percent(value: Any) -> str:
    if value is None:
        return "未知"
    return f"{float(value):.2f}%"


def _format_signed_percent(value: Any) -> str:
    if value is None:
        return "未知"
    return f"{float(value):+.2f}%"


def _format_price(value: Any) -> str:
    if value is None:
        return "未知"
    return f"{float(value):.2f}"
