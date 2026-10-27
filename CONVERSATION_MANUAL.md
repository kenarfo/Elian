# Conversation manual tests (Phase 8) â€” natural speech

Use a quiet room. Prefer short answers from Elian.

## Setup

```bash
python main.py --conversation-diagnostics
python -m unittest tests.test_conversation_phase8 -v
```

## Natural scenarios

1. **Simple action**
   â€žEj Elian, otwĂłrz Spotify.â€ť
   Expect: short ack, not a long narration.

2. **Preference music**
   â€žZapamiÄ™taj, ĹĽe wolÄ™ Spotify do muzyki.â€ť
   later: â€žPuĹ›Ä‡ muzykÄ™.â€ť
   Expect: Spotify without asking which app.

3. **Mid-correction**
   â€žOtwĂłrz Spotifyâ€¦ nie, YouTube Music.â€ť
   Expect: only YouTube Music.

4. **Wait**
   â€žPrzypomnij miâ€¦ czekaj.â€ť
   then: â€žJutro o 17 o treningu.â€ť
   Expect: no reminder on â€žczekajâ€ť; reminder after the second turn.

5. **Ambiguity**
   â€žNapisz do Kuby.â€ť (if two contacts)
   Expect: short question, no send.

6. **Negation**
   â€žNie otwieraj Spotify.â€ť
   Expect: no open.

7. **Hypothetical**
   â€žCo by siÄ™ staĹ‚o, gdybym wyĹ‚Ä…czyĹ‚ komputer?â€ť
   Expect: explanation, no shutdown.

8. **Partial honesty**
   Force a play failure after open (e.g. offline media).
   Expect: not just â€žGotowe.â€ť

9. **Follow-up context**
   â€žOtwĂłrz Chrome.â€ť then â€žWyszukaj tam pogodÄ™.â€ť
   Expect: Chrome used as context.

10. **Frustration**
    After a wrong action: â€žNie, nie o to chodziĹ‚o.â€ť
    Expect: correction, not a long apology.

11. **Stop TTS**
    While Elian speaks: â€žStop.â€ť
    Expect: speech stops; no lecture about stopping.

12. **Quiet success**
    â€žGĹ‚oĹ›niej.â€ť
    Expect: very short or silent success.

## Safety

- Do not confirm dangerous actions unless intentional.
- Diagnostics must not send messages or delete calendar events.
