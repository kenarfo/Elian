---
name: agent-smoke-gate
description: >-
  Runs Elian's offline agent smoke gate (dry-run tools, no mic) and reports
  PASS/FAIL for Spotify/APP, Michael Jackson/MEDIA, knowledge AI, and Ok.
  acknowledgement. Use when the user asks for agent-smoke, before microphone
  RC validation, after Agent/Planner/intent_family/safety changes, or to verify
  json_retry no longer falls back to open_app.
---

# Agent smoke gate

## When

- After changes to Planner, intent family, repair, safety, or ConversationManager agent path
- Before any hardware / mic RC smoke
- User says `--agent-smoke`, agent-smoke, or “sprawdź trzy repro”

## Steps

1. From repo root, run dry-run smoke (no side effects):

   ```bash
   python main.py --agent-smoke
   ```

2. Require **4/4 PASS** (Phase 10.1 + 10.2):
   - `możesz odpalić mi spotify` → APP, `open_app` Spotify, no generic clarification
   - `puść coś michaela jacksona` → MEDIA, `play_media` Michael Jackson
   - `co oznacza ai?` → KNOWLEDGE, no tools, no clarification
   - `Ok.` → CONVERSATION_CONTROL / ack, **tool_calls=0**

3. Optional real model (still dry tools):

   ```bash
   python main.py --agent-smoke-real
   ```

4. Report: each input’s family, decision_type, tool, clarification, json_retry_count, tool_calls, PASS/FAIL.

## Hard rules

- Do not run mic RC until smoke PASSes.
- Do not “fix” smoke by weakening assertions.
- If FAIL: fix Agent contract / safety; re-run smoke; then full unittest.
