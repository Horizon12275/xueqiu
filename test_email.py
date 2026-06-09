from __future__ import annotations

from config import load_settings
from notifier import EmailNotifier


def main() -> int:
    load_settings()
    notifier = EmailNotifier()

    subject = "测试：雪球调仓提醒邮件"
    content = """这是一封测试邮件。

如果你收到这封邮件，说明 QQ 邮箱 SMTP 已经配置成功。

后续 xq_watch.py 发现新的雪球组合调仓时，会自动发送邮件提醒。
"""

    ok = notifier.send(subject, content)
    if ok:
        print("test email sent")
        return 0

    print("test email failed")
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
