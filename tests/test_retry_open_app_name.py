# ==========================
# ELIAN — retry after fail must reopen named app
# ==========================
from __future__ import annotations

import unittest

from core.jarvis_behavioral.harness import jarvis_session


class RetryOpenAppNameTests(unittest.TestCase):
    def test_retry_keeps_app_name(self):
        with jarvis_session() as (cm, backend, log):
            backend.set_inject("fail")
            cm.handle("otwórz Discord")
            backend.set_inject("ok")
            cm.handle("czemu nie zrobiłeś?")
            log.clear()
            out = cm.handle("spróbuj jeszcze raz")
            text = getattr(out, "text", None) or str(out)
            self.assertTrue(any(r.get("tool") == "open_app" for r in log))
            args = next(r.get("arguments") or {} for r in log if r.get("tool") == "open_app")
            self.assertIn("discord", str(args.get("name") or "").casefold())
            self.assertIn("discord", text.casefold())
            self.assertNotIn("aplikacja otwarte", text.casefold())


if __name__ == "__main__":
    unittest.main()
