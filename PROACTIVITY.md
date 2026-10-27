# Proactivity

Existing engine: `core/proactive.py`.

Decisions already exist in spirit: silent vs notify vs queue. Internal events (window changed, frame updated, vision completed) must not become TTS.

## Allowed to speak

- User-requested monitoring completed
- Task blocked when the user asked to be told
- Critical failure of something Elian was asked to do
- Calendar/reminder the user enabled

## Not allowed

- Window/focus chatter
- Vision finished
- Tiny background state
- Call-center “czy mogę jeszcze pomóc?”

Timeline / “co mnie ominęło?” is master Phase 6 — not implemented. Do not fake background monitoring after process exit.
