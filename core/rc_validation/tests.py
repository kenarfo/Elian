# ==========================
# ELIAN — RC validation test catalog (Phase 9.5)
# ==========================

from __future__ import annotations

from dataclasses import dataclass, field
from typing import List, Optional


@dataclass
class RCTestCase:
    id: str
    category: str
    title: str
    instructions: str
    phrases: List[str] = field(default_factory=list)
    verify: List[str] = field(default_factory=list)
    skip_if: Optional[str] = None  # e.g. "no_battery", "no_ha", "no_vision_model"


RC_TEST_CASES: List[RCTestCase] = [
    RCTestCase(
        id="wake_word",
        category="wake",
        title="Wake word (10 attempts)",
        instructions=(
            "Powiedz wake word ok. 10 razy w normalnych warunkach. "
            "Zapiszemy wykrycia automatycznie. Po teście oznacz false positive "
            "poleceniem: rc wake fp — oraz missed: rc wake miss."
        ),
        verify=[
            "10 prób wake",
            "Brak nadmiernych false positive (ręczna ocena)",
            "CUSTOM ELIAN WAKE MODEL — sprawdź w raporcie",
        ],
    ),
    RCTestCase(
        id="wake_false_positive",
        category="wake",
        title="False positive check",
        instructions=(
            "Przez krótki okres mów normalne zdania BEZ wake word. "
            "Jeśli Elian się aktywuje — wpisz: rc wake fp"
        ),
        verify=["Ręczne oznaczenie false wake"],
    ),
    RCTestCase(
        id="stt_phrases",
        category="stt",
        title="STT natural phrases",
        instructions="Przeczytaj/wypowiedz poniższe zdania. Porównaj transcript w konsoli.",
        phrases=[
            "Elian, otwórz Spotify.",
            "Zwiększ trochę głośność.",
            "Włącz lampę w salonie.",
            "Przypomnij mi jutro o siedemnastej o treningu.",
            "Otwórz Spotify… nie, jednak YouTube Music.",
            "Moment, zapomniałem co chciałem powiedzieć.",
        ],
        verify=["Transcript sensowny", "Brak zapisu nagrań audio"],
    ),
    RCTestCase(
        id="natural_speech",
        category="stt",
        title="Natural speech",
        instructions="Wypowiedz naturalnie (nie idealne komendy):",
        phrases=[
            "Ej Elian, możesz mi odpalić Spotify?",
            "Elian, puść coś Michaela Jacksona.",
            "Trochę tu ciemno.",
            "Czekaj chwilę.",
            "Nie, nie o to mi chodziło.",
        ],
        verify=["Routing sensowny", "Brak crashy"],
    ),
    RCTestCase(
        id="fastpath_volume",
        category="routing",
        title="FastPath — głośniej",
        instructions='Powiedz: „głośniej". Sprawdź FastPath, zero Agent/Vision/Legacy.',
        phrases=["głośniej"],
        verify=[
            "route=fast_path",
            "agent_calls=0",
            "legacy=0",
            "Zmierz latency po STT (rc status)",
        ],
    ),
    RCTestCase(
        id="agent_multi",
        category="routing",
        title="Agent multi-step",
        instructions='Powiedz: „Otwórz Spotify i zwiększ głośność."',
        phrases=["Otwórz Spotify i zwiększ głośność."],
        verify=["planner_steps >= 2", "legacy=0"],
    ),
    RCTestCase(
        id="memory_preference",
        category="memory",
        title="Memory preference",
        instructions=(
            '1) „Zapamiętaj, że wolę Spotify do muzyki."\n'
            "2) Zrestartuj Eliana (Ctrl+C, ponownie --rc-validation).\n"
            '3) „Puść muzykę." — sprawdź preference Spotify.'
        ),
        phrases=[
            "Zapamiętaj, że wolę Spotify do muzyki.",
            "Puść muzykę.",
        ],
        verify=["Preference persisted", "play_media używa Spotify"],
    ),
    RCTestCase(
        id="context_pronoun",
        category="context",
        title="Context — zamknij / przyciemnij",
        instructions="Sekwencja:",
        phrases=[
            "Otwórz Spotify.",
            "Zamknij je.",
            "Włącz lampę w salonie.",
            "Przyciemnij ją.",
        ],
        verify=["Follow-up rozumiany", "active entity/context"],
        skip_if="no_ha",
    ),
    RCTestCase(
        id="wait_phrase",
        category="session",
        title="Wait phrase",
        instructions=(
            '„Elian, przypomnij mi… czekaj." — pauza — '
            '„Dobra, jutro o siedemnastej." Sesja nie może się zgubić.'
        ),
        phrases=[
            "Elian, przypomnij mi… czekaj.",
            "Dobra, jutro o siedemnastej.",
        ],
        verify=["Sesja aktywna po wait", "Reminder intent"],
    ),
    RCTestCase(
        id="correction",
        category="utterance",
        title="Mid-utterance correction",
        instructions="Tylko final target:",
        phrases=["Otwórz Spotify… nie, jednak YouTube Music."],
        verify=["open_app → YouTube Music", "nie Spotify"],
    ),
    RCTestCase(
        id="negation",
        category="utterance",
        title="Negation",
        instructions="Nie wykonuj akcji:",
        phrases=["Nie otwieraj Spotify."],
        verify=["open_app calls = 0"],
    ),
    RCTestCase(
        id="hypothetical",
        category="utterance",
        title="Hypothetical",
        instructions="Wyjaśnienie bez wykonania:",
        phrases=["Co by się stało, gdybym wyłączył komputer?"],
        verify=["shutdown calls = 0"],
    ),
    RCTestCase(
        id="reminder_real",
        category="integrations",
        title="Reminder (2 min)",
        instructions=(
            '„Przypomnij mi za dwie minuty, żebym sprawdził Eliana." '
            "Poczekaj na proactive voice."
        ),
        phrases=["Przypomnij mi za dwie minuty, żebym sprawdził Eliana."],
        verify=["save", "EventBus", "ProactiveManager", "Voice notify"],
    ),
    RCTestCase(
        id="calendar_local",
        category="integrations",
        title="Calendar [ELIAN TEST]",
        instructions=(
            "Utwórz bezpieczne wydarzenie z tytułem [ELIAN TEST]. "
            "Po teście usuń przez normalny flow z confirmation."
        ),
        verify=["Local calendar only if configured", "Cleanup after test"],
    ),
    RCTestCase(
        id="media_controls",
        category="integrations",
        title="Media controls",
        instructions="Sprawdź na tym PC: play, pause, resume, next, previous, now playing.",
        verify=["Zapisz które działają w rc note"],
    ),
    RCTestCase(
        id="ha_basic",
        category="integrations",
        title="Home Assistant safe device",
        instructions=(
            "Jedno bezpieczne urządzenie: state, turn on, turn off, set value. "
            "NIE testuj zamków/alarmów."
        ),
        verify=["HA tool success", "state read"],
        skip_if="no_ha",
    ),
    RCTestCase(
        id="ha_followup",
        category="integrations",
        title="HA follow-up entity",
        instructions="Włącz lampę… potem: Ustaw ją na 50%.",
        phrases=["Włącz lampę w salonie.", "Ustaw ją na pięćdziesiąt procent."],
        verify=["active entity preserved"],
        skip_if="no_ha",
    ),
    RCTestCase(
        id="vision_screen",
        category="vision",
        title="Vision — Notepad",
        instructions=(
            "Otwórz Notatnik. Powiedz: Co mam na ekranie? potem: Kliknij Plik. "
            "Sprawdź UIA/OCR/Vision fallback."
        ),
        phrases=["Co mam na ekranie?", "Kliknij Plik."],
        verify=["describe/click tools", "snapshot refresh"],
    ),
    RCTestCase(
        id="vision_risk",
        category="vision",
        title="Vision risk — NEEDS_CONFIRMATION",
        instructions=(
            "Bezpieczna strona/fake UI z przyciskiem Usuń konto. "
            "Sprawdź NEEDS_CONFIRMATION. NIE klikaj prawdziwego przycisku."
        ),
        verify=["NEEDS_CONFIRMATION", "no blind click"],
    ),
    RCTestCase(
        id="barge_in",
        category="voice",
        title="Barge-in",
        instructions=(
            "Poproś o dłuższą odpowiedź (np. wyjaśnienie). W trakcie powiedz: Stop."
        ),
        verify=["TTS zatrzymany", "sesja aktywna"],
    ),
    RCTestCase(
        id="session_timeout",
        category="session",
        title="Session timeout",
        instructions=(
            "Wake → komenda → follow-up ×2 bez wake. "
            "Poczekaj > idle timeout. Następna komenda wymaga wake."
        ),
        verify=["Session bez wake w follow-up", "Po timeout wymaga wake"],
    ),
    RCTestCase(
        id="proactive_battery",
        category="proactive",
        title="Proactive battery",
        instructions="Tylko laptop z baterią. Nie rozładowuj sztucznie.",
        verify=["Alert baterii jeśli warunki"],
        skip_if="no_battery",
    ),
    RCTestCase(
        id="idle_stability",
        category="performance",
        title="Idle stability (kilka minut)",
        instructions=(
            "Zostaw Eliana bez rozmowy 3–5 min. "
            "Sprawdź rc status — brak unexpected agent/vision/disk."
        ),
        verify=["Niskie CPU", "Brak unexpected calls"],
    ),
    RCTestCase(
        id="gpu_coexistence",
        category="performance",
        title="GPU model coexistence",
        instructions=(
            "Kolejno: voice command → agent request → vision request → voice command. "
            "Sprawdź brak OOM."
        ),
        verify=["Whisper + text + vision bez crash", "VRAM w raporcie jeśli dostępne"],
    ),
]
