---
name: elian-regression-test
description: >-
  Adds a minimal hardware or agent regression test for Elian, then fixes until
  scoped and full unittest pass. Prefer ConversationManager → Agent → Planner
  integration tests for intent/safety contracts. Use when the user provides a
  REPRO utterance, asks for a regression test, or reports a bug from mic RC /
  agent logs (json_retry, clarification, pending merge, ACK tools).
---

# Elian regression test

## When

- User pastes a REPRO (utterance + expected vs actual log)
- Bug found in hardware RC or agent-smoke
- Need a test that locks Phase 10.x contract behavior

## Steps

1. Capture **minimal REPRO**: input text, expected family/decision/tools, forbidden outcomes (e.g. `Czy chcesz otworzyć aplikację?`).

2. Choose test home:
   - Intent/ACK/UNKNOWN safety → `tests/test_agent_intent_contract.py` or `tests/test_agent_safety.py`
   - RC cross-cutting → `tests/test_rc_regressions.py`
   - Planner loop only → `tests/test_planner.py`

3. Prefer **CM → Agent → Planner** with `FakeModelClient` + dry `ToolRegistry` when the bug is routing/contract. Simulate bad model outputs (invalid JSON, generic open_app clarification) when that is the failure mode.

4. Run scoped test, then full suite:

   ```bash
   python -m unittest tests.test_<module> -v
   python -m unittest discover -s tests -p "test_*.py"
   ```

5. If Agent path changed, also run `python main.py --agent-smoke`.

6. Report: new test names, total test count, smoke result.

## Hard rules

- Test must fail before the fix (or prove the fix with a previously failing assertion).
- No real mic/HA side effects in unit tests.
- Do not weaken assertions to get green.
