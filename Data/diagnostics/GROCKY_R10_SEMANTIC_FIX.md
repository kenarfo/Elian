# Grocky R10 fix — ASR filler, bare verb, room_hint, ActiveDevice×room

Date: 2026-09-10. Owner: **Grocky** (+ CM room gate shared with Memik).
Evidence: local probes; `tests.test_active_goal_semantics`; `main.py --agent-smoke` 11/11.

## Observed → fix

| Case | Observed | Fix |
|------|----------|-----|
| `ehh`/`eh`/`ahh`/`aaa`/`mm` | `FILLER_ONLY` only y+/e+/uhm… → act/TALK | Structural hesitation in `core/utterance.py` (`[aeiouy]+h+` / multi-vowel / mm+) |
| bare/truncated `włącz…` | APP ASK | Bare dual-use verb → UNKNOWN `open_verb_bare`; pass-1 → ASK `incomplete_request` (no APP domains) |
| `w sypialni jaśniej` | no room slot | `extract_room_hint` + `_implicit_lighting` slots `room`/`room_hint` |
| ActiveDevice salon + sypialnia utt. | bind without room check | CM gate: `room_matches_device` — mismatch leaves incomplete for resolver |

## Still Toolik
- DeviceResolver short object_id substring (`a` ⊆ `sypialnia`)
- room unmatched pool → false `light.a`

## Verify
- filler ehh/eh/ahh wait; filler+command still act
- włącz…/otwórz… → ASK incomplete_request []
- włącz lampę / otwórz spotify unchanged ACT
- room_hint=sypialnia; salon ActiveDevice no-bind
- smoke 11/11