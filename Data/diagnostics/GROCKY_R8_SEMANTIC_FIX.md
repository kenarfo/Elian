# Grocky R8 fix — semantic / cancel / pronouns / compound domains

Date: 2026-09-10. Owner: **Grocky** (shared compound with Memik). Evidence: local probes + `tests.test_active_goal_semantics` 17/17 + `main.py --agent-smoke` 11/11.

## Observed → fix

| Case | Observed | Fix |
|------|----------|-----|
| `zgaś światło i ciszej` tool=None | `POWER_OFF_VERBS` missed `zgaś`; `_domains_from_text` stem `\bświatł\b` missed `światło` | `core/target_resolve.py` + `semantic_executive._domains_from_text` |
| `włącz lampę i ciszej` → APP | `HA_EXPLICIT` gated by `not APP_VERBS` so dual-use `włącz` demoted to APP | `intent_family`: HA noun beats APP_VERBS |
| mid-goal `stop`/`stój`/`cisza` → TALK | STOP_PHRASES only in clarification; pass-1 never cancelled | `_STOP` + same envelope as cancel |
| `anuluj to` → execute_discussed_goal | pass-1 cancel + utterance REFERENCE → CONTINUATION overwrite | `_REJECT` includes `anuluj to`; apply_utterance: cancel wins |
| `zrób ją ciemniejszą` hint empty | `_DIM` missed comparative | `device_pronoun._DIM` + CONTINUATION must not stomp smart_home_control |
| `włącz ją znowu` ASK/app | APP incomplete; no active_domain to classify | pass active_domain (+ active_device) into classify; APP→SMART_HOME on pronoun power |

## Memik still owns
- `partial_success_message` bad-shape → false „Gotowe.” (R8 #3b)

## Verify
- compound: zgaś/włącz/wyłącz + ciszej → SMART_HOME turn_* + SYSTEM adjust_volume; domains SYSTEM+SMART_HOME
- mid-goal stop/anuluj → WAIT cancel_goal
- pronoun chain turns 2+5 → ACT smart_home_control
- smoke 11/11; active_goal_semantics 17/17