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


if __name__ == "__main__":
    unittest.main()
