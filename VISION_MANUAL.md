# Vision / Screen Awareness — manual checklist (Phase 5)

## Diagnostics (safe, no clicks)

```bash
python main.py --vision-diagnostics
python main.py --vision-diagnostics --save-screenshot
```

Expect: active window, UIA/OCR/vision status, element list, observation text.

Optional: set local multimodal model in `Data/config.json`:

```json
"ai": {
  "vision_model": "llava",
  "vision_enabled": true
}
```

Use a model already pulled in Ollama. Leave `vision_model` empty to run UIA/OCR-only.

## Manual UI smoke (non-destructive)

1. Open Notepad (Notatnik).
2. Run `--vision-diagnostics` and confirm window title / text-ish elements if UIA works.
3. Start Elian (`python main.py`).
4. Say: „Co mam na ekranie?”
5. Say: „Kliknij Plik.” (only if you accept a menu click)
6. Avoid destructive labels (Usuń, Delete account, Pay).

## Preference order

1. Native tools (`volume_up`, `open_app`, …)
2. UI Automation
3. OCR
4. Vision LLM
5. Raw coordinates (confirmation required)

## Privacy

- Screenshots stay local (RAM / optional temp diagnostic PNG).
- Password fields are never written into observations or memory.
- No cloud vision upload in this phase.
