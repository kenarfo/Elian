import unittest

from core.jarvis_behavioral.harness import jarvis_session


class PingnijReminderTests(unittest.TestCase):
    def test_pingnij_kwadrans_creates_reminder(self):
        with jarvis_session() as (cm, _backend, log):
            result = cm.handle("pingnij mnie za kwadrans o herbacie")

        calls = [item for item in log if item.get("tool") == "create_reminder"]
        self.assertTrue(calls, result.text)
        args = calls[-1].get("arguments") or {}
        self.assertEqual(args.get("when"), "za kwadrans")
        self.assertEqual(args.get("text"), "herbacie")
        self.assertNotIn("Jasne, słucham", result.text)


if __name__ == "__main__":
    unittest.main()
