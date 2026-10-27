# ==========================
# ELIAN — RC console status display (Phase 9.5)
# ==========================
# Observes SpeechStateMachine transitions. Does not touch audio / STT / TTS.

from __future__ import annotations

from typing import Callable, List, Optional

from voice.state import SpeechState


STATUS_LABELS = {
    SpeechState.IDLE: "🟡 IDLE — czekam na wake word „Elian”",
    SpeechState.WAKE_LISTENING: "🟡 IDLE — czekam na wake word „Elian”",
    SpeechState.ACTIVE_LISTENING: "🟢 LISTENING — słucham...",
    SpeechState.PROCESSING: "🔵 PROCESSING — przetwarzam...",
    SpeechState.ACTING: "🟠 ACTING — wykonuję...",
    SpeechState.SPEAKING: "🟣 SPEAKING — odpowiadam...",
    SpeechState.INTERRUPTED: "🔴 INTERRUPTED — przerwałem, słucham",
    SpeechState.PAUSED: "⏸️ PAUSED — czekam",
    SpeechState.WAITING: "⚪ ACTIVE SESSION — możesz mówić bez „Elian”",
}


class RCStatusDisplay:
    """Prints human-readable voice status for --rc-validation."""

    def __init__(
        self,
        *,
        enabled: bool = True,
        print_fn: Optional[Callable[[str], None]] = None,
    ):
        self.enabled = enabled
        self._print = print_fn or print
        self._last_label: str = ""
        self.history: List[str] = []

    def attach(self, state_machine) -> None:
        """Register as observer on SpeechStateMachine (no VoicePipeline changes)."""
        if not self.enabled:
            return
        state_machine.add_listener(self.on_state)

    def detach(self, state_machine) -> None:
        try:
            state_machine.remove_listener(self.on_state)
        except Exception:
            pass

    def on_state(self, old: SpeechState, new: SpeechState) -> None:
        if not self.enabled or old is new:
            return
        label = STATUS_LABELS.get(new)
        if not label:
            return
        self._emit(label)

    def wake_detected(self) -> None:
        if not self.enabled:
            return
        self._emit("WAKE DETECTED")
        self._emit(STATUS_LABELS[SpeechState.WAITING])

    def heard(self, transcript: str) -> None:
        if not self.enabled:
            return
        text = (transcript or "").strip()
        if not text:
            self._emit('HEARD: ""')
        else:
            self._emit(f'HEARD: "{text}"')

    def processing(self) -> None:
        """Explicit PROCESSING when CM.handle runs (pipeline may not set it)."""
        if not self.enabled:
            return
        self._emit(STATUS_LABELS[SpeechState.PROCESSING])

    def _emit(self, line: str) -> None:
        if line == self._last_label:
            return
        self._last_label = line
        self.history.append(line)
        self._print(f"[STATUS] {line}")
