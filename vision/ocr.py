# ==========================
# ELIAN — OCR provider (Phase 5)
# ==========================

from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Any, List, Optional

from vision.models import Bounds, UIElement


@dataclass
class OCRBox:
    text: str
    bounds: Bounds
    confidence: float = 0.8


class OCRProvider:
    def extract(self, image: Any, *, snapshot_id: str = "") -> List[UIElement]:
        raise NotImplementedError

    @property
    def available(self) -> bool:
        return True


class FakeOCR(OCRProvider):
    def __init__(self, boxes: Optional[List[OCRBox]] = None):
        self.boxes = list(boxes or [])
        self.calls = 0
        self.fail = False

    def extract(self, image: Any, *, snapshot_id: str = "") -> List[UIElement]:
        self.calls += 1
        if self.fail:
            raise RuntimeError("Fake OCR failure")
        elements = []
        for idx, box in enumerate(self.boxes, start=1):
            elements.append(
                UIElement(
                    id=f"ocr_{idx}",
                    role="text",
                    text=box.text,
                    bounds=box.bounds,
                    source="ocr",
                    snapshot_id=snapshot_id,
                    confidence=box.confidence,
                )
            )
        return elements


class NullOCR(OCRProvider):
    @property
    def available(self) -> bool:
        return False

    def extract(self, image: Any, *, snapshot_id: str = "") -> List[UIElement]:
        return []


class RapidOCRProvider(OCRProvider):
    """Optional RapidOCR if installed; otherwise unavailable."""

    def __init__(self):
        self._engine = None
        self._available = False
        try:
            from rapidocr_onnxruntime import RapidOCR  # type: ignore

            self._engine = RapidOCR()
            self._available = True
        except Exception:
            self._available = False

    @property
    def available(self) -> bool:
        return self._available

    def extract(self, image: Any, *, snapshot_id: str = "") -> List[UIElement]:
        if not self._available or image is None:
            return []
        t0 = time.perf_counter()
        try:
            # RapidOCR accepts numpy / path / PIL depending on version
            import numpy as np

            if hasattr(image, "convert"):
                arr = np.array(image.convert("RGB"))
            else:
                arr = image
            result, _ = self._engine(arr)
        except Exception as exc:
            print(f"[VISION] OCR failed: {exc}")
            return []

        elements: List[UIElement] = []
        if not result:
            return elements
        for idx, item in enumerate(result, start=1):
            try:
                box, text, score = item[0], item[1], float(item[2])
                xs = [p[0] for p in box]
                ys = [p[1] for p in box]
                x0, y0, x1, y1 = int(min(xs)), int(min(ys)), int(max(xs)), int(max(ys))
                elements.append(
                    UIElement(
                        id=f"ocr_{idx}",
                        role="text",
                        text=str(text),
                        bounds=Bounds(x0, y0, max(1, x1 - x0), max(1, y1 - y0)),
                        source="ocr",
                        snapshot_id=snapshot_id,
                        confidence=score,
                    )
                )
            except Exception:
                continue
        print(f"[VISION] OCR elements={len(elements)} {(time.perf_counter()-t0)*1000:.0f}ms")
        return elements


def create_default_ocr() -> OCRProvider:
    rapid = RapidOCRProvider()
    if rapid.available:
        return rapid
    print("[VISION] OCR unavailable — continuing without OCR")
    return NullOCR()
