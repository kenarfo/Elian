# Integration manual tests (Phase 7) — safe scenarios

## Diagnostics (no writes)

```bash
python main.py --integration-diagnostics
```

Expect status lines for Calendar / Reminders / Media / Messaging / HA. No secrets printed.

## Reminders (local)

1. Start Elian.
2. Say: „Przypomnij mi za 2 minuty, żebym napił się wody.”
3. Wait / leave Elian running — Proactive should speak once when due.
4. Restart Elian before due time — reminder must still fire (Data/reminders.json).

## Calendar (local file provider)

Default provider is **local** (`Data/calendar.json`), not Google.

1. „Co mam dzisiaj?”
2. „Dodaj spotkanie jutro o 17 zatytułowane Trening.”
3. „Przesuń je na 18.” (uses event_id follow-up via agent/tools)
4. Delete requires confirmation — do not confirm unless intentional.

## Media

1. Set preference: „Preferuję Spotify do muzyki.” (or Memory preference `music.default_app`)
2. „Puść Black or White.” — opens/search via app layer; Spotify Web API is NOT_CONFIGURED unless tokens set in secrets (never logged).
3. „Co teraz leci?” — may be limited without SMTC metadata.

## Home Assistant

Only if `smart_home.enabled` and token configured:

1. „Czy lampa jest włączona?”
2. „Włącz scenę wieczór.” — calls `scene.turn_on` (real action).

## Messaging

Without configured provider:

> „Nie mam jeszcze skonfigurowanej integracji…”

Do **not** use diagnostics to send messages.
