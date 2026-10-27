# ==========================
# ELIAN — vision session / hybrid understand (Phase 5)
# ==========================

from __future__ import annotations

import re
import time
from difflib import SequenceMatcher
from typing import List, Optional, Tuple

from core.latency import current_tracker
from vision.capture import ScreenCaptureBackend, create_default_capture_backend
from vision.models import ScreenCapture, ScreenSnapshot, UIElement
from vision.ocr import OCRProvider, create_default_ocr
from vision.provider import (
    VisionProvider,
    create_default_vision_provider,
    elements_from_vision_payload,
)
from vision.risk_policy import filter_password_from_texts
from vision.uia import UIBackend, create_default_ui_backend


class VisionSession:
    """
    Short-lived screen awareness. Not MemoryManager.
    Caches current snapshot briefly; UI actions invalidate it.
    """

    def __init__(
        self,
        *,
        capture: Optional[ScreenCaptureBackend] = None,
        ui: Optional[UIBackend] = None,
        ocr: Optional[OCRProvider] = None,
        vision: Optional[VisionProvider] = None,
        cache_ttl_ms: float = 250.0,
        max_elements: int = 40,
    ):
        self.capture_backend = capture or create_default_capture_backend()
        self.ui = ui or create_default_ui_backend()
        self.ocr = ocr or create_default_ocr()
        self._vision = vision
        self.cache_ttl_ms = cache_ttl_ms
        self.max_elements = max_elements
        self.current: Optional[ScreenSnapshot] = None
        self._role_counters: dict = {}

    @property
    def vision(self):
        if self._vision is None:
            self._vision = create_default_vision_provider()
        return self._vision

    def invalidate(self) -> None:
        if self.current is not None:
            self.current.invalidate()
        self.current = None

    def get_active_window(self) -> Tuple[str, str]:
        return self.capture_backend.get_active_window_info()

    def capture_raw(
        self,
        *,
        monitor: int = 0,
        active_window_only: bool = False,
    ) -> ScreenCapture:
        tracker = current_tracker()
        t0 = time.perf_counter()
        cap = self.capture_backend.capture(
            monitor=monitor,
            active_window_only=active_window_only,
        )
        if tracker:
            tracker.record_ms(
                "screen_capture_ms", (time.perf_counter() - t0) * 1000
            )
        return cap

    def refresh(
        self,
        *,
        force: bool = False,
        use_vision: bool = True,
        use_ocr: bool = True,
        use_uia: bool = True,
        question: str = "",
        active_window_only: bool = False,
    ) -> ScreenSnapshot:
        if (
            not force
            and self.current is not None
            and self.current.valid
            and (time.time() - self.current.created_at) * 1000 < self.cache_ttl_ms
        ):
            return self.current

        capture = self.capture_raw(active_window_only=active_window_only)
        snapshot_id = ScreenSnapshot.new_id()
        elements: List[UIElement] = []
        sources: List[str] = []
        visible_text: List[str] = []
        summary = ""
        tracker = current_tracker()

        if use_uia:
            t0 = time.perf_counter()
            try:
                uia_els = self.ui.list_elements(max_elements=self.max_elements * 2)
                for el in uia_els:
                    el.snapshot_id = snapshot_id
                    el.id = self._assign_id(el.role, el.id)
                    elements.append(el)
                    if el.text and not el.is_password:
                        visible_text.append(el.text)
                if uia_els:
                    sources.append("uia")
            except Exception as exc:
                print(f"[VISION] UIA error: {exc}")
            if tracker:
                tracker.record_ms("uia_ms", (time.perf_counter() - t0) * 1000)

        if use_ocr and self.ocr.available:
            t0 = time.perf_counter()
            try:
                ocr_els = self.ocr.extract(capture.image, snapshot_id=snapshot_id)
                for el in ocr_els:
                    el.id = self._assign_id("ocr", el.id)
                    elements.append(el)
                    if el.text:
                        visible_text.append(el.text)
                if ocr_els:
                    sources.append("ocr")
            except Exception as exc:
                print(f"[VISION] OCR error: {exc}")
            if tracker:
                tracker.record_ms("ocr_ms", (time.perf_counter() - t0) * 1000)

        if use_vision and self.vision.available:
            t0 = time.perf_counter()
            prompt = question.strip() or (
                "Describe this desktop screen briefly. List interactive elements "
                "with approximate bounding boxes."
            )
            try:
                payload = self.vision.analyze(capture.image, prompt)
                summary = str(
                    payload.get("answer") or payload.get("summary") or ""
                ).strip()
                if not summary:
                    summary = str(payload.get("summary") or "").strip()
                for t in payload.get("visible_text") or []:
                    if t:
                        visible_text.append(str(t))
                vision_els = elements_from_vision_payload(
                    payload,
                    snapshot_id=snapshot_id,
                    image_width=capture.width,
                    image_height=capture.height,
                    screen_width=capture.width,
                    screen_height=capture.height,
                )
                for el in vision_els:
                    el.id = self._assign_id(el.role, el.id)
                    elements.append(el)
                if payload.get("summary") or vision_els:
                    sources.append("vision")
            except Exception as exc:
                print(f"[VISION] vision model error: {exc}")
            if tracker:
                tracker.record_ms(
                    "vision_model_ms", (time.perf_counter() - t0) * 1000
                )

        elements = self._dedupe_and_rank(elements)[: self.max_elements]
        visible_text = filter_password_from_texts(_unique(visible_text))[:40]

        if not summary:
            summary = self._fallback_summary(capture, elements, visible_text)

        self._role_counters.clear()
        snap = ScreenSnapshot(
            id=snapshot_id,
            capture=capture,
            elements=elements,
            summary=summary,
            visible_text=visible_text,
            sources=sources or ["capture"],
        )
        for el in snap.elements:
            el.snapshot_id = snapshot_id

        self.current = snap
        print(
            f"[VISION] snapshot={snap.id} window={capture.active_window!r} "
            f"elements={len(elements)} source={'+'.join(snap.sources)}"
        )
        return snap

    def require_current(self) -> ScreenSnapshot:
        if self.current is None or not self.current.valid:
            return self.refresh(force=True)
        return self.current

    def find_elements(
        self,
        query: str,
        *,
        snapshot: Optional[ScreenSnapshot] = None,
        limit: int = 5,
    ) -> List[Tuple[UIElement, float]]:
        tracker = current_tracker()
        t0 = time.perf_counter()
        snap = snapshot or self.require_current()
        q = _norm(query)
        scored: List[Tuple[UIElement, float]] = []
        for el in snap.elements:
            if el.is_password and "hasło" not in q and "password" not in q:
                # Still matchable by role query "password field"
                pass
            score = _match_score(q, el)
            if score >= 0.35:
                scored.append((el, score))
        scored.sort(key=lambda item: (-item[1], item[0].id))
        if tracker:
            tracker.record_ms(
                "element_match_ms", (time.perf_counter() - t0) * 1000
            )
        return scored[:limit]

    def ask_about_screen(self, question: str) -> str:
        snap = self.refresh(force=True, question=question)
        if snap.summary:
            return snap.summary
        q = _norm(question)
        for text in snap.visible_text:
            if any(tok in _norm(text) for tok in q.split() if len(tok) > 3):
                return text
        return "Nie potrafię teraz poprawnie odczytać tego ekranu."

    def _assign_id(self, role: str, preferred: str) -> str:
        preferred = str(preferred or "").strip()
        if preferred and not preferred.startswith("el_"):
            # Keep stable preferred if unique-ish
            if re.match(r"^(button|input|link|ocr|vision|menu|el)_\d+$", preferred):
                return preferred
        key = role if role in ("button", "textbox", "edit", "link", "menu") else "el"
        if key == "textbox" or key == "edit":
            key = "input"
        self._role_counters[key] = self._role_counters.get(key, 0) + 1
        return f"{key}_{self._role_counters[key]}"

    def _dedupe_and_rank(self, elements: List[UIElement]) -> List[UIElement]:
        seen = set()
        out = []
        for el in sorted(
            elements,
            key=lambda e: (
                0 if e.source == "uia" else 1 if e.source == "ocr" else 2,
                0 if e.role in ("button", "textbox", "edit", "link") else 1,
                -e.confidence,
            ),
        ):
            key = (_norm(el.text), el.role, el.bounds.x // 8, el.bounds.y // 8)
            if key in seen and el.text:
                continue
            seen.add(key)
            out.append(el)
        return out

    def _fallback_summary(
        self,
        capture: ScreenCapture,
        elements: List[UIElement],
        texts: List[str],
    ) -> str:
        window = capture.active_window or "Nieznane okno"
        buttons = [e.text for e in elements if e.role == "button" and e.text][:5]
        inputs = [e.text for e in elements if e.role in ("textbox", "edit", "input") and e.text][:3]
        parts = [f"Aktywne okno: {window}."]
        if buttons:
            parts.append("Przyciski: " + ", ".join(buttons) + ".")
        if inputs:
            parts.append("Pola: " + ", ".join(inputs) + ".")
        if not buttons and not inputs and texts:
            parts.append("Widoczny tekst: " + "; ".join(texts[:5]) + ".")
        if not buttons and not inputs and not texts:
            parts.append("Nie wykryłem interaktywnych elementów.")
        return " ".join(parts)


def _norm(text: str) -> str:
    text = str(text or "").lower().strip()
    return re.sub(r"\s+", " ", text)


def _unique(items: List[str]) -> List[str]:
    seen = set()
    out = []
    for item in items:
        key = _norm(item)
        if not key or key in seen:
            continue
        seen.add(key)
        out.append(item)
    return out


def _match_score(query: str, element: UIElement) -> float:
    if not query:
        return 0.0
    text = _norm(element.text)
    role = _norm(element.role)
    hay = f"{role} {text}".strip()
    if not hay:
        return 0.0
    if query == text:
        return 1.0
    if query in text or text in query:
        return 0.9
    # Color / position hints in query don't match text — lower base from role
    ratio = SequenceMatcher(None, query, text).ratio() if text else 0.0
    if any(tok in query for tok in ("button", "przycisk")) and element.role == "button":
        ratio = max(ratio, 0.45)
    if any(tok in query for tok in ("pole", "textbox", "input", "nazwa")) and element.role in (
        "textbox",
        "edit",
        "input",
    ):
        ratio = max(ratio, 0.5)
    # Token overlap
    q_tokens = set(query.split())
    t_tokens = set(text.split())
    if q_tokens and t_tokens:
        overlap = len(q_tokens & t_tokens) / max(1, len(q_tokens))
        ratio = max(ratio, overlap)
    return float(ratio) * float(element.confidence)


_session: Optional[VisionSession] = None


def get_vision_session() -> VisionSession:
    global _session
    if _session is None:
        _session = VisionSession()
    return _session


def set_vision_session(session: Optional[VisionSession]) -> None:
    global _session
    _session = session


def reset_vision_session_for_tests(session: Optional[VisionSession] = None) -> None:
    set_vision_session(session)
