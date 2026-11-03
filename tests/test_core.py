import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from guard_core import Cooldowns, load_config, match_names, message_link, normalize, snowflake


class CoreTests(unittest.TestCase):
    def test_matching(self):
        self.assertTrue(match_names(["Ｅxample\u200b Employee"], ["Example Employee"]))
        self.assertTrue(match_names(["Example Employe"], ["Example Employee"]))
        self.assertFalse(match_names(["Unrelated Person"], ["Example Employee"]))
        self.assertFalse(match_names(["Al"], ["Ali"]))
        self.assertFalse(match_names(["💥"], ["---"]))
        self.assertEqual(normalize("A B_c!"), "abc")

    def test_input_validation(self):
        self.assertEqual(snowflake("345678901234567890"), 345678901234567890)
        for value in ("@person", "1", "18446744073709551616", "123456789012345\n"):
            with self.assertRaises(ValueError):
                snowflake(value)
        self.assertTrue(message_link("https://discord.com/channels/@me/123456789012345678/234567890123456789"))
        self.assertFalse(message_link("https://discord.com.evil.test/channels/@me/123456789012345678/234567890123456789"))

    def test_config(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "config.json"
            config = json.loads(Path("config.example.json").read_text())
            path.write_text(json.dumps(config))
            self.assertIsInstance(load_config(path)["employee_ids"], set)
            config["employee_ids"] = []
            path.write_text(json.dumps(config))
            with self.assertRaises(ValueError):
                load_config(path)

    def test_cooldowns_persist_and_expire(self):
        with tempfile.TemporaryDirectory() as directory:
            path = str(Path(directory) / "test.sqlite3")
            with patch("guard_core.time.time", return_value=1000):
                first = Cooldowns(path)
                self.assertEqual(first.remaining("a", 60), 0)
                first.mark("a")
                first.db.close()
            second = Cooldowns(path)
            with patch("guard_core.time.time", return_value=1030):
                self.assertEqual(second.remaining("a", 60), 30)
                self.assertEqual(second.remaining("b", 60), 0)
            with patch("guard_core.time.time", return_value=1061):
                self.assertEqual(second.remaining("a", 60), 0)
            second.db.close()


if __name__ == "__main__":
    unittest.main()
