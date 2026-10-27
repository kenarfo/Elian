# ==========================
# ELIAN — fresh screen observation replies (C.5.3)
# ==========================
"""Screen questions answer from CURRENT foreground, never last_working_app."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional


SCREEN_QUERY_MAX_AGE_MS = 500
UNCERTAIN_SPEECH = "Nie mam teraz pewnego odczytu ekranu."


@dataclass
class ScreenObservation:
    ok: bool = False
    hwnd: int = 0
    title: str = ""
    process: str = ""
    bounds: Dict[str, Any] = field(default_factory=dict)
    age_ms: float = 0.0
    source: str = "foreground"
    refreshed: bool = False
    certainty: str = "UNCERTAIN"
    uia_elements: List[Dict[str, Any]] = field(default_factory=list)
    cached_hwnd: int = 0
    cached_app: str = ""
    fresh_app: str = ""
    used_stale: bool = False

    def to_log_dict(self) -> Dict[str, Any]:
        return {
            "query": "screen_observation",
            "cached_hwnd": self.cached_hwnd,
            "fresh_hwnd": self.hwnd,
            "cached_app": self.cached_app,
            "fresh_app": self.fresh_app,
            "age_ms": int(self.age_ms),
            "refreshed": 1 if self.refreshed else 0,
            "source": self.source,
            "certainty": self.certainty,
        }


def looks_screen_observation_query(text: str) -> bool:
    import re

    raw = str(text or "").strip().lower()
    if not raw:
        return False
    raw = re.sub(
        r"^(?:okej|ok|dobra|no\s+dobra|a|no)[,.]?\s+",
        "",
        raw,
        flags=re.I,
    )
    return bool(
        re.search(r"\bco\s+(?:teraz\s+)?widz(?:isz|[eę](?:sz)?)\b", raw)
        or re.search(r"\bco\s+(?:teraz\s+)?widz(?:isz|[eę](?:sz)?)\s+na\s+(?:ekranie|monitorze)\b", raw)
        or re.search(r"\bco\s+(?:mam\s+)?(?:teraz\s+)?na\s+ekranie\b", raw)
        or re.search(r"\bno\s+dobra,?\s+co\s+mam\s+na\s+ekranie\b", raw)
        or re.search(r"\bco\s+tu\s+jest\s+napisane\b", raw)
        or re.search(r"\bco\s+jest\s+(?:teraz\s+)?(?:otwarte|aktywne)\b", raw)
        or re.search(r"\bco\s+mam\s+(?:teraz\s+)?otwarte\b", raw)
        or re.search(r"\bjakie\s+okno\s+jest\s+aktywne\b", raw)
        or re.search(r"\bna\s+co\s+(?:teraz\s+)?patrz[eę]\b", raw)
        or re.search(r"\bco\s+teraz\s+jest\s+przed\s+tob", raw)
        or re.search(r"\bpoka[zż]\s+ekran\b", raw)
    )


def app_label(process: str, title: str) -> str:
    blob = f"{process} {title}".lower()
    if "chatgpt" in blob:
        return "ChatGPT"
    if "chrome" in blob:
        return "Chrome"
    if "msedge" in blob or "edge" in (process or "").lower():
        return "Edge"
    if "firefox" in blob:
        return "Firefox"
    if "notepad" in blob or "notatnik" in blob:
        return "Notatnik"
    if "code" in blob or "cursor" in blob:
        title_s = str(title or "").strip()
        if title_s:
            return title_s.split(" - ")[-1] if " - " in title_s else title_s
        return "Cursor" if "cursor" in blob else "VS Code"
    title_s = str(title or "").strip()
    if title_s:
        if " - " in title_s:
            return title_s.split(" - ")[0].strip() or title_s
        return title_s
    proc = str(process or "").strip()
    if proc.lower().endswith(".exe"):
        proc = proc[:-4]
    return proc


def format_screen_observation(obs: ScreenObservation) -> str:
    if not obs.ok or obs.certainty == "UNCERTAIN" or not obs.hwnd:
        return UNCERTAIN_SPEECH
    process = str(obs.process or "")
    title = str(obs.title or "")
    blob = f"{process} {title}".lower()
    if "chatgpt" in blob and "chrome" in blob:
        return "Masz otwarty ChatGPT w Chrome."
    if "chatgpt" in blob:
        return "Masz otwarty ChatGPT."
    label = app_label(process, title)
    if obs.certainty == "BASIC":
        if label:
            return f"Masz teraz aktywne okno {label}."
        return UNCERTAIN_SPEECH
    if label == "Notatnik":
        return "Masz otwarty Notatnik."
    if label:
        if label.lower() in ("chrome", "edge", "firefox"):
            return f"Masz teraz aktywne okno {label}."
        return f"Masz otwarty {label}."
    return UNCERTAIN_SPEECH


def answer_screen_query(text: str = "") -> tuple[str, ScreenObservation]:
    from core.perception.screen_runtime import get_screen_runtime

    runtime = get_screen_runtime()
    obs = runtime.fresh_screen_observation(force=True, max_age_ms=SCREEN_QUERY_MAX_AGE_MS)
    log = obs.to_log_dict()
    print(
        "[SCREEN TRUTH]\n"
        f"query=screen_observation\n"
        f"cached_hwnd={log['cached_hwnd']}\n"
        f"fresh_hwnd={log['fresh_hwnd']}\n"
        f"cached_app={log['cached_app']}\n"
        f"fresh_app={log['fresh_app']}\n"
        f"age_ms={log['age_ms']}\n"
        f"refreshed={log['refreshed']}\n"
        f"source={log['source']}"
    )
    if obs.used_stale:
        from core.perception.metrics import note_perception

        note_perception("stale_screen_claim_rate")
        spoken = UNCERTAIN_SPEECH
    else:
        spoken = format_screen_observation(obs)
    return spoken, obs
