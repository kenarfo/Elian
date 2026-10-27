# QA baseline 2026-09-10 (Grocky adversarial)

## SOFTWARE
- unittest discover: Ran 1156 in 132.7s — FAILED (failures=3, errors=16, skipped=6)
- main.py --agent-smoke: CRASH ModuleNotFoundError: faster_whisper (Voice import before flag)
- core.agent_smoke.run_agent_smoke(): 11/11 PASS (dry-run) — VERIFY often skipped: verify_tool_unregistered
- Hardware / mic RC: NOT RUN

## Observed traces (examples)
- FALSE SUCCESS: HA ConnectTimeout + turn_off_device success=True "Zgaszona"
- FALSE TALK/ACT: "potrzebuję discorda" SEMANTIC TALK needs_tools=false → handler "Uruchomiłem Discord"
- WRONG TOOL / ASR: "odtwórz chrome" → play_track; ASR repair odtwórz→otwórz
- UNNECESSARY ASK: cold "zgaś ją" / "ją wyłącz" → ASK
- LOST CONTEXT: cold slang "weź no","bardziej","nie tyle","no i?" → TALK
- COLD "nie aż tak" → learned_routine (wrong without brightness goal)
