import importlib.util
import io
import json
import unittest
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path
from unittest.mock import patch


ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("update", ROOT / "scripts" / "update.py")
update = importlib.util.module_from_spec(spec)
spec.loader.exec_module(update)


def prices(anchor="100", close="120", day="2026-10-06"):
    series = {
        ticker: [("2025-12-31", Decimal(anchor)), (day, Decimal(close))]
        for ticker in update.STOCKS
    }
    series["QQQ"] = [
        ("1999-03-10", Decimal("50")),
        ("2025-08-01", Decimal("150")),
        (day, Decimal("120")),
    ]
    return series


class SnapshotTests(unittest.TestCase):
    now = datetime(2026, 10, 7, 15, tzinfo=timezone.utc)

    def test_previous_year_baseline_and_sorted_rows(self):
        series = prices()
        series["AAPL"] = [
            ("2025-12-30", Decimal("100")),
            ("2025-12-31", Decimal("110")),
            ("2026-10-06", Decimal("121")),
        ]
        result = update.snapshot(series, "Yahoo", self.now)
        self.assertEqual(result["as_of"], "2026-10-06")
        apple = next(row for row in result["stocks"] if row["ticker"] == "AAPL")
        self.assertEqual((apple["base_date"], apple["ytd_pct"]), ("2025-12-31", 10.0))
        self.assertEqual(len(result["stocks"]), 7)
        self.assertEqual(result["stocks"][0]["ytd_pct"], 20.0)

    def test_rejects_different_trading_dates(self):
        series = prices()
        series["TSLA"][-1] = ("2026-10-05", Decimal("120"))
        with self.assertRaisesRegex(ValueError, "do not share"):
            update.snapshot(series, "Yahoo", self.now)

    def test_qqq_uses_all_time_high_close_not_recent_high(self):
        series = prices()
        series["QQQ"].insert(1, ("2000-03-10", Decimal("200")))
        result = update.snapshot(series, "Yahoo", self.now)["qqq"]
        self.assertEqual((result["peak_date"], result["peak_close"]), ("2000-03-10", 200.0))
        self.assertEqual((result["close"], result["drawdown_pct"]), (120.0, 40.0))

    def test_qqq_new_high_has_zero_drawdown(self):
        series = prices()
        series["QQQ"][-1] = ("2026-10-06", Decimal("200"))
        result = update.snapshot(series, "Yahoo", self.now)["qqq"]
        self.assertEqual((result["peak_date"], result["drawdown_pct"]), ("2026-10-06", 0.0))

    def test_qqq_ignores_incomplete_today(self):
        series = prices()
        series["QQQ"].append(("2026-10-07", Decimal("300")))
        result = update.snapshot(series, "Yahoo", self.now)["qqq"]
        self.assertEqual((result["peak_date"], result["close"]), ("2025-08-01", 120.0))

    def test_qqq_requires_matching_date_and_full_history(self):
        series = prices()
        series["QQQ"].pop()
        with patch("sys.stderr"):
            result = update.snapshot(series, "Yahoo", self.now)
        self.assertIn("qqq_error", result)
        self.assertEqual(len(result["stocks"]), 7)
        series = prices()
        series["QQQ"].pop(0)
        with patch("sys.stderr"):
            result = update.snapshot(series, "Yahoo", self.now)
        self.assertIn("qqq_error", result)

    def test_rejects_incomplete_market_day(self):
        series = prices(day="2026-10-07")
        with self.assertRaisesRegex(ValueError, "current-year close"):
            update.snapshot(series, "Yahoo", datetime(2026, 10, 7, 19, tzinfo=timezone.utc))

    def test_rejects_stale_prices(self):
        with self.assertRaisesRegex(ValueError, "stale"):
            update.snapshot(prices(day="2026-09-15"), "Yahoo", self.now)

    def test_rejects_potential_unadjusted_split(self):
        with self.assertRaisesRegex(ValueError, "possible split"):
            update.snapshot(prices(close="20"), "Sina", self.now)

    def test_sina_parses_jsonp_with_prefix(self):
        body = '/* redirect */\nvar([{"d":"2025-12-31","c":"100"},{"d":"2026-10-06","c":"120"}]);'
        with patch.object(update, "get_text", return_value=body):
            self.assertEqual(update.sina_daily("AAPL", 2026)[-1], ("2026-10-06", Decimal("120")))

    def test_yahoo_uses_market_timezone_and_ignores_missing_close(self):
        sample = {
            "chart": {"error": None, "result": [{
                "meta": {"currency": "USD"},
                "timestamp": [int(datetime(2026, 10, 7, 20, tzinfo=timezone.utc).timestamp())],
                "indicators": {"quote": [{"close": [123.45]}]},
            }]}
        }
        with patch.object(update, "get_text", return_value=json.dumps(sample)):
            self.assertEqual(update.yahoo_daily("AAPL", 2026), [("2026-10-07", Decimal("123.45"))])

    def test_qqq_fetch_requests_full_history(self):
        sample = {
            "chart": {"error": None, "result": [{
                "meta": {"currency": "USD"},
                "timestamp": [int(datetime(1999, 3, 10, 20, tzinfo=timezone.utc).timestamp())],
                "indicators": {"quote": [{"close": [50.0]}]},
            }]}
        }
        with patch.object(update, "get_text", return_value=json.dumps(sample)) as request:
            self.assertEqual(update.yahoo_daily("QQQ", 2026, full_history=True)[0][0], "1999-03-10")
            self.assertIn(
                f"period1={int(datetime(1999, 3, 10, tzinfo=timezone.utc).timestamp())}",
                request.call_args.args[0],
            )

    def test_qqq_fetch_failure_keeps_seven_stock_snapshot(self):
        with patch.object(update, "yahoo_daily", side_effect=TimeoutError("Yahoo down")), \
             patch.object(update, "sina_daily", side_effect=lambda ticker, year: prices()[ticker]), \
             patch.object(update, "OUTPUT") as output, \
             patch.object(update, "datetime", wraps=datetime) as clock, patch("sys.stderr"):
            clock.now.return_value = self.now
            output.exists.return_value = False
            update.update()
            payload = json.loads(output.write_text.call_args.args[0])
        self.assertIn("qqq_error", payload)
        self.assertNotIn("qqq", payload)
        self.assertEqual(len(payload["stocks"]), 7)


if __name__ == "__main__":
    unittest.main()
