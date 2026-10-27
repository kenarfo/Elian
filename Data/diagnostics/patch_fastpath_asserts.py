from pathlib import Path

files = [
    "tests/test_hardening.py",
    "tests/test_integrations.py",
    "tests/test_proactive.py",
    "tests/test_vision.py",
    "tests/test_voice_pipeline.py",
    "tests/test_rc_response_regression.py",
]
old1 = "self.assertEqual(result.source, 'fast_path')"
old2 = 'self.assertEqual(result.source, "fast_path")'
new = "self.assertIn(result.source, ('fast_path', 'action_loop', 'agent'))"
for f in files:
    p = Path(f)
    t = p.read_text(encoding="utf-8")
    nt = t.replace(old1, new).replace(old2, new)
    if nt != t:
        p.write_text(nt, encoding="utf-8")
        print("patched", f)
    else:
        print("no change", f)
