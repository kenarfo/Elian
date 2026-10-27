# ==========================
# ELIAN — visual referents (C.5)
# ==========================
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

from core.perception.metrics import note_perception


@dataclass
class VisualReferent:
    referent_id: str = ""
    label: str = ""
    role: str = ""
    bbox: List[float] = field(default_factory=list)
    uia_element_id: str = ""
    confidence: float = 0.0
    source: List[str] = field(default_factory=list)
    frame_id: int = 0
    hwnd: int = 0

    def to_dict(self) -> Dict[str, Any]:
        return {
            "referent_id": self.referent_id,
            "label": self.label,
            "role": self.role,
            "bbox": list(self.bbox),
            "uia_element_id": self.uia_element_id,
            "confidence": self.confidence,
            "source": list(self.source),
            "frame_id": self.frame_id,
            "hwnd": self.hwnd,
        }


class VisualReferentContext:
    def __init__(self):
        self.last: Optional[VisualReferent] = None

    def remember(self, referent: VisualReferent) -> None:
        self.last = referent
        print(
            f"[VISUAL REFERENT] label={referent.label!r} confidence={referent.confidence:.2f} "
            f"sources={','.join(referent.source) or '-'} frame_id={referent.frame_id}"
        )

    def resolve(
        self,
        phrase: str,
        *,
        candidates: Optional[List[VisualReferent]] = None,
        cursor_element: Optional[VisualReferent] = None,
        min_confidence: float = 0.80,
        action_confidence: float = 0.90,
        for_action: bool = False,
    ) -> Dict[str, Any]:
        text = str(phrase or "").strip().lower()
        rows = list(candidates or [])
        deictic = "to" in text or "ten" in text or "tutaj" in text or "to jest" in text
        if cursor_element is not None and deictic:
            chosen = cursor_element
            need = action_confidence if for_action else min_confidence
            if float(chosen.confidence or 0) < need:
                note_perception("low_confidence_visual_action")
                return {
                    "ok": False,
                    "clarify": True,
                    "message": "Nie jestem pewien, który element wskazać.",
                    "confidence": chosen.confidence,
                }
            self.remember(chosen)
            return {"ok": True, "referent": chosen, "clarify": False}
        if (not rows) and self.last is not None and (
            text in ("to", "ten", "tamto", "ten obok") or "obok" in text
        ):
            rows = [self.last]
        if len(rows) >= 2:
            top = sorted(rows, key=lambda r: float(r.confidence or 0), reverse=True)
            if abs(float(top[0].confidence) - float(top[1].confidence)) < 0.05:
                note_perception("ambiguous_visual_action")
                return {
                    "ok": False,
                    "clarify": True,
                    "message": "Widzę dwa możliwe elementy. Górny czy dolny?",
                    "candidates": [item.to_dict() for item in top[:2]],
                }
        if not rows:
            return {"ok": False, "clarify": True, "message": "Nie wiem, który element masz na myśli."}
        chosen = rows[0]
        need = action_confidence if for_action else min_confidence
        if float(chosen.confidence or 0) < need:
            note_perception("low_confidence_visual_action")
            return {
                "ok": False,
                "clarify": True,
                "message": "Nie jestem pewien, który element wskazać.",
                "confidence": chosen.confidence,
            }
        self.remember(chosen)
        return {"ok": True, "referent": chosen, "clarify": False}

    def invalidate_hwnd(self, hwnd: int) -> None:
        if self.last is None:
            return
        if int(getattr(self.last, "hwnd", 0) or 0) == int(hwnd or 0):
            self.last = None
