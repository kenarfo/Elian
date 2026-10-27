# ==========================
# ELIAN — vision router (C.5)
# ==========================
"""Event-driven local vision. Never clicks. Structured candidates only."""

from __future__ import annotations

import threading
import time
from typing import Any, Dict, List, Optional

from Config import get_config_value
from core.perception.metrics import note_perception


VISION_REASONS_ALLOWED = frozenset(
    {
        "user_screen_question",
        "uia_unknown",
        "custom_drawn",
        "spatial",
        "major_change",
        "pixel_verify",
        "visual_referent",
        "game_ui",
        "test",
    }
)


class VisionResourcePolicy:
    def __init__(self):
        self.stt_active = False
        self.tts_active = False
        self.llm_active = False
        self.vision_inflight = False

    def allow_background(self) -> bool:
        if self.stt_active or self.tts_active:
            return False
        return not self.vision_inflight


class LocalVisionProvider:
    def __init__(self, *, mock: Optional[Dict[str, Any]] = None):
        self.model = str(get_config_value("vision", "model", default="qwen3-vl:8b") or "qwen3-vl:8b")
        self.provider = str(get_config_value("vision", "provider", default="ollama") or "ollama")
        self.timeout = float(get_config_value("vision", "timeout_seconds", default=12) or 12)
        self.mock = mock
        self.available = False
        self.last_error = ""
        self.last_latency_ms = 0.0
        self.calls = 0
        if mock is not None:
            self.available = True

    def probe(self, timeout: float = 1.5) -> bool:
        if self.mock is not None:
            self.available = True
            return True
        found = {"ok": False}

        def _check():
            try:
                import ollama

                client = ollama.Client(timeout=timeout)
                listing = client.list()
                models = listing.get("models") if isinstance(listing, dict) else listing
                names = []
                for item in models or []:
                    if isinstance(item, dict):
                        names.append(str(item.get("name") or item.get("model") or ""))
                    else:
                        names.append(str(getattr(item, "model", "") or getattr(item, "name", "")))
                found["ok"] = any(self.model in str(name) for name in names)
            except Exception as exc:
                self.last_error = str(exc)
                found["ok"] = False

        thread = threading.Thread(target=_check, daemon=True)
        thread.start()
        thread.join(timeout=max(0.2, float(timeout)))
        self.available = bool(found["ok"])
        print(
            f"[VISION]\nprovider={self.provider}\nmodel={self.model}\n"
            f"available={1 if self.available else 0}\nmode=ON_DEMAND"
        )
        if not self.available:
            print(f"[VISION] provider={self.provider} model={self.model} status=UNAVAILABLE")
        return self.available

    def localize(self, question: str, *, frame=None, region=None) -> Dict[str, Any]:
        self.calls += 1
        note_perception("vision_requests")
        t0 = time.perf_counter()
        if self.mock is not None:
            payload = dict(self.mock)
            self.last_latency_ms = (time.perf_counter() - t0) * 1000.0
            return payload
        if not self.available:
            return {"description": "", "candidates": [], "error": "vision_unavailable"}
        # Real Ollama path is on-demand; structured parse only.
        try:
            from vision.provider import OllamaVisionProvider

            real = OllamaVisionProvider(model=self.model, timeout=self.timeout)
            image = getattr(frame, "pixels", None)
            raw = real.analyze(image, question)
        except Exception as exc:
            self.last_error = str(exc)
            return {"description": "", "candidates": [], "error": str(exc)}
        self.last_latency_ms = (time.perf_counter() - t0) * 1000.0
        candidates = []
        for item in list(raw.get("elements") or raw.get("candidates") or []):
            if not isinstance(item, dict):
                continue
            bbox = item.get("bbox")
            if not bbox:
                bbox = [
                    item.get("x", 0),
                    item.get("y", 0),
                    int(item.get("x", 0) or 0) + int(item.get("width") or item.get("w") or 0),
                    int(item.get("y", 0) or 0) + int(item.get("height") or item.get("h") or 0),
                ]
            candidates.append(
                {
                    "label": item.get("text") or item.get("label") or "",
                    "role": item.get("role") or "",
                    "bbox": bbox,
                    "confidence": float(item.get("confidence") or 0.0),
                }
            )
        return {
            "description": raw.get("summary") or raw.get("answer") or raw.get("description") or "",
            "candidates": candidates,
        }


class VisionRouter:
    def __init__(self, provider: Optional[LocalVisionProvider] = None, policy: Optional[VisionResourcePolicy] = None):
        self.provider = provider or LocalVisionProvider()
        self.policy = policy or VisionResourcePolicy()
        self.enabled = bool(get_config_value("vision", "enabled", default=True))

    def maybe_request(
        self,
        reason: str,
        question: str = "",
        *,
        frame=None,
        region=None,
        force: bool = False,
    ) -> Optional[Dict[str, Any]]:
        if not self.enabled:
            return None
        if reason not in VISION_REASONS_ALLOWED and not force:
            note_perception("unnecessary_vision_call")
            return None
        if reason not in ("user_screen_question", "visual_referent", "uia_unknown", "custom_drawn", "test", "pixel_verify"):
            if not self.policy.allow_background() and not force:
                return None
        if reason in ("keyboard_type", "keyboard_select"):
            note_perception("unnecessary_vision_call")
            return None
        print(
            f"[VISION] provider={self.provider.provider} model={self.provider.model} "
            f"reason={reason} frame_id={getattr(frame, 'frame_id', 0)}"
        )
        result = self.provider.localize(question or reason, frame=frame, region=region)
        print(
            f"[VISION] provider={self.provider.provider} model={self.provider.model} "
            f"reason={reason} frame_id={getattr(frame, 'frame_id', 0)} "
            f"latency_ms={self.provider.last_latency_ms:.0f} "
            f"confidence={_top_confidence(result):.2f}"
        )
        return result


def _top_confidence(payload: Dict[str, Any]) -> float:
    cands = list((payload or {}).get("candidates") or [])
    if not cands:
        return 0.0
    return max(float(item.get("confidence") or 0) for item in cands if isinstance(item, dict))
