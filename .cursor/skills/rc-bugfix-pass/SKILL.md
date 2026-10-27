---
name: rc-bugfix-pass
description: >-
  Structured RC bugfix pass for Elian hardware campaign failures. Audits
  campaign diagnostics, finds shared root causes, writes failing regression
  tests first, applies minimal fixes, then gates with unittest and agent-smoke.
  Use when the user starts an RC bugfix phase (e.g. Faza 10 / 10.1 / 10.2),
  mentions RC_VALIDATION_CAMPAIGN, FAIL counts, or hardware smoke regressions.
---

# RC bugfix pass

## When

- User names an RC bugfix phase or campaign FAIL list
- Hardware smoke regresses after a prior pass
- Working under `core/rc_validation/` or `Data/diagnostics/` campaign reports

## Steps

1. **Audit** (read-only first):
   - `Data/diagnostics/RC_VALIDATION_CAMPAIGN_REPORT.md`
   - `Data/diagnostics/rc_validation_campaign_latest.json` (or campaign.json)
   - Group FAILs by shared root cause (Agent clarification, pending, FastPath, wake, etc.)

2. **Failing tests first** — add/adjust regression tests that fail on current bug behavior.

3. **Minimal fix** — prefer one contract/routing/safety fix that clears many FAILs. No feature creep. No per-integration bandaids unless the phase says so.

4. **Verify**:
   ```bash
   python -m unittest discover -s tests -p "test_*.py"
   python main.py --agent-smoke
   ```

5. Update `RC_REGRESSION_RETEST.md` with what was fixed and what remains.

6. Resume mic RC only after agent-smoke PASS. Campaign is **merge/resume**, never wipe history.

## Hard bans

- Do not add new product features mid-pass
- Do not refactor Planner/VoicePipeline architecture wholesale
- Do not touch deferred items (e.g. memory_preference, custom wake) unless named in the phase
- Do not continue unrelated RC tests while a hard smoke gate is red
