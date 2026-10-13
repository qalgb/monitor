import importlib.util
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch


ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("notify", ROOT / "scripts" / "notify.py")
notify = importlib.util.module_from_spec(spec)
spec.loader.exec_module(notify)


class Response:
    def __enter__(self):
        return self

    def __exit__(self, *_):
        return False

    def read(self):
        return b'{"code":0}'


class NotificationTests(unittest.TestCase):
    def test_send_once_and_mark_only_after_success(self):
        with tempfile.TemporaryDirectory() as directory:
            data = Path(directory) / "data.json"
            sent = Path(directory) / "sent.json"
            data.write_text(json.dumps({
                "as_of": "2026-10-06", "source": "Yahoo",
                "stocks": [{"name": "苹果", "ticker": "AAPL", "ytd_pct": 12.3, "close": 123.45}],
            }), encoding="utf-8")
            with patch.object(notify, "DATA", data), patch.object(notify, "SENT", sent), \
                 patch.dict("os.environ", {"FEISHU_WEBHOOK": "https://open.feishu.cn/open-apis/bot/v2/hook/test"}), \
                 patch.object(notify, "urlopen", return_value=Response()) as send:
                notify.notify()
                notify.notify()
                self.assertEqual(send.call_count, 1)
                self.assertEqual(json.loads(sent.read_text())["as_of"], "2026-10-06")

    def test_rejected_message_does_not_mark_sent(self):
        with tempfile.TemporaryDirectory() as directory:
            data = Path(directory) / "data.json"
            sent = Path(directory) / "sent.json"
            data.write_text('{"as_of":"2026-10-06","source":"Yahoo","stocks":[]}')
            class Rejected(Response):
                def read(self):
                    return b'{"code":19021,"msg":"failed"}'
            with patch.object(notify, "DATA", data), patch.object(notify, "SENT", sent), \
                 patch.dict("os.environ", {"FEISHU_WEBHOOK": "https://open.feishu.cn/open-apis/bot/v2/hook/test"}), \
                 patch.object(notify, "urlopen", return_value=Rejected()):
                with self.assertRaisesRegex(ValueError, "rejected"):
                    notify.notify()
                self.assertFalse(sent.exists())

    def test_adding_qqq_sends_on_existing_trading_date_once(self):
        with tempfile.TemporaryDirectory() as directory:
            data = Path(directory) / "data.json"
            sent = Path(directory) / "sent.json"
            data.write_text(json.dumps({
                "as_of": "2026-10-12", "source": "Yahoo", "stocks": [],
                "qqq": {
                    "drawdown_pct": 1.79, "close": 746.04,
                    "peak_close": 759.66, "peak_date": "2026-10-06",
                },
            }))
            sent.write_text('{"as_of":"2026-10-12"}')
            with patch.object(notify, "DATA", data), patch.object(notify, "SENT", sent), \
                 patch.dict("os.environ", {"FEISHU_WEBHOOK": "https://open.feishu.cn/open-apis/bot/v2/hook/test"}), \
                 patch.object(notify, "urlopen", return_value=Response()) as send:
                notify.notify()
                self.assertIn("QQQ 距历史最高收盘价回撤 1.79%", json.loads(
                    send.call_args.args[0].data)["content"]["text"])
                notify.notify()
                self.assertEqual(send.call_count, 1)
                self.assertTrue(json.loads(sent.read_text())["qqq"])

    def test_does_not_send_older_snapshot_when_it_adds_qqq(self):
        with tempfile.TemporaryDirectory() as directory:
            data = Path(directory) / "data.json"
            sent = Path(directory) / "sent.json"
            data.write_text('{"as_of":"2026-10-11","stocks":[],"source":"Yahoo","qqq":{}}')
            sent.write_text('{"as_of":"2026-10-12"}')
            with patch.object(notify, "DATA", data), patch.object(notify, "SENT", sent), \
                 patch.dict("os.environ", {"FEISHU_WEBHOOK": "https://open.feishu.cn/open-apis/bot/v2/hook/test"}), \
                 patch.object(notify, "urlopen") as send:
                notify.notify()
                send.assert_not_called()

    def test_missing_qqq_is_disclosed_in_message(self):
        with tempfile.TemporaryDirectory() as directory:
            data = Path(directory) / "data.json"
            sent = Path(directory) / "sent.json"
            data.write_text(json.dumps({
                "as_of": "2026-10-12", "stocks": [], "source": "Sina",
                "qqq_error": "QQQ 历史行情暂不可用",
            }))
            with patch.object(notify, "DATA", data), patch.object(notify, "SENT", sent), \
                 patch.dict("os.environ", {"FEISHU_WEBHOOK": "https://open.feishu.cn/open-apis/bot/v2/hook/test"}), \
                 patch.object(notify, "urlopen", return_value=Response()) as send:
                notify.notify()
                message = json.loads(send.call_args.args[0].data)["content"]["text"]
                self.assertIn("QQQ 历史行情暂不可用", message)
                self.assertFalse(json.loads(sent.read_text())["qqq"])


if __name__ == "__main__":
    unittest.main()
