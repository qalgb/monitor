#!/usr/bin/env python3
"""Send each new closing-day snapshot to a Feishu custom bot once."""

import json
import os
import sys
from pathlib import Path
from urllib.request import Request, urlopen


ROOT = Path(__file__).resolve().parents[1]
DATA = ROOT / "site" / "data.json"
SENT = ROOT / "state" / "sent.json"
PAGE = "https://qalgb.github.io/monitor/"


def notify():
    webhook = os.environ.get("FEISHU_WEBHOOK")
    if not webhook or not webhook.startswith("https://open.feishu.cn/open-apis/bot/v2/hook/"):
        raise ValueError("Set FEISHU_WEBHOOK to the Feishu group custom bot webhook in Actions secrets")
    data = json.loads(DATA.read_text(encoding="utf-8"))
    if SENT.exists() and json.loads(SENT.read_text(encoding="utf-8"))["as_of"] >= data["as_of"]:
        print(f"Already notified for {data['as_of']}")
        return
    lines = [f"美股七巨头年初至今 · {data['as_of']} 收盘"]
    for stock in data["stocks"]:
        lines.append(
            f"{stock['name']} {stock['ticker']}: {stock['ytd_pct']:+.2f}%"
            f"  (${stock['close']:.2f})"
        )
    lines.extend((f"基准：上年最后交易日收盘价｜数据：{data['source']}", PAGE))
    payload = json.dumps({
        "msg_type": "text", "content": {"text": "\n".join(lines)}
    }, ensure_ascii=False).encode("utf-8")
    request = Request(webhook, data=payload, headers={"Content-Type": "application/json"})
    with urlopen(request, timeout=15) as response:
        result = json.load(response)
    if result.get("code", result.get("StatusCode")) != 0:
        raise ValueError(f"Feishu rejected notification: {result}")
    SENT.parent.mkdir(parents=True, exist_ok=True)
    SENT.write_text(json.dumps({"as_of": data["as_of"]}) + "\n", encoding="utf-8")
    print(f"Notified for {data['as_of']}")


if __name__ == "__main__":
    try:
        notify()
    except (ValueError, OSError) as error:
        print(error, file=sys.stderr)
        sys.exit(1)
