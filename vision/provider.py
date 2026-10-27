# ==========================
# ELIAN — vision model provider (Phase 5)
# ==========================

from __future__ import annotations

import base64
import io
import json
import time
from typing import Any, Dict, List, Optional

from Config import get_config_value
from vision.models import Bounds, UIElement


class VisionProvider:
    """Analyze screen images. Separate from text ModelClient."""

    def analyze(
        self,
        image: Any,
        prompt: str,
        *,
        crop_bounds: Optional[Bounds] = None,
    ) -> Dict[str, Any]:
        raise NotImplementedError

    @property
    def available(self) -> bool:
        return True

    @property
    def name(self) -> str:
        return type(self).__name__


class FakeVisionProvider(VisionProvider):
    def __init__(
        self,
        *,
        summary: str = "",
        elements: Optional[List[UIElement]] = None,
        answers: Optional[Dict[str, str]] = None,
    ):
        self.summary = summary
        self.elements = list(elements or [])
        self.answers = dict(answers or {})
        self.calls: List[str] = []
        self.fail = False

    def analyze(
        self,
        image: Any,
        prompt: str,
        *,
        crop_bounds: Optional[Bounds] = None,
    ) -> Dict[str, Any]:
        self.calls.append(prompt)
        if self.fail:
            raise RuntimeError("Fake vision failure")
        # Simple Q&A: if prompt looks like a question, use answers map
        for key, value in self.answers.items():
            if key.lower() in prompt.lower():
                return {
                    "summary": value,
                    "visible_text": [],
                    "elements": [],
                    "answer": value,
                }
        return {
            "summary": self.summary,
            "visible_text": [e.text for e in self.elements if e.text],
            "elements": [e.to_planner_dict() for e in self.elements],
            "answer": self.summary,
        }


class NullVisionProvider(VisionProvider):
    @property
    def available(self) -> bool:
        return False

    def analyze(
        self,
        image: Any,
        prompt: str,
        *,
        crop_bounds: Optional[Bounds] = None,
    ) -> Dict[str, Any]:
        return {
            "summary": "",
            "visible_text": [],
            "elements": [],
            "error": "vision_unavailable",
        }


class OllamaVisionProvider(VisionProvider):
    """
    Local multimodal model via Ollama (e.g. llava, qwen2-vl).
    Never uploads to cloud APIs.
    """

    def __init__(self, model: Optional[str] = None, timeout: Optional[float] = None):
        import ollama

        self.model = model or get_config_value(
            "ai",
            "vision_model",
            default="",
        )
        self._client = ollama.Client(
            timeout=float(
                timeout
                if timeout is not None
                else get_config_value("ai", "request_timeout", default=60)
            )
        )
        self._available = bool(self.model)

    @property
    def available(self) -> bool:
        return self._available

    @property
    def name(self) -> str:
        return f"OllamaVision({self.model})"

    def analyze(
        self,
        image: Any,
        prompt: str,
        *,
        crop_bounds: Optional[Bounds] = None,
    ) -> Dict[str, Any]:
        if not self._available:
            return {"summary": "", "elements": [], "error": "no_vision_model"}

        t0 = time.perf_counter()
        img = image
        if crop_bounds is not None and image is not None and hasattr(image, "crop"):
            img = image.crop(
                (
                    crop_bounds.x,
                    crop_bounds.y,
                    crop_bounds.right,
                    crop_bounds.bottom,
                )
            )

        b64 = _image_to_b64(img)
        if not b64:
            return {"summary": "", "elements": [], "error": "image_encode_failed"}

        system = (
            "You analyze desktop screenshots for a Polish voice assistant. "
            "Reply with JSON only: "
            '{"summary":"...", "visible_text":["..."], '
            '"elements":[{"id":"button_1","role":"button","text":"...",'
            '"x":0,"y":0,"width":10,"height":10,"confidence":0.9}],'
            ' "answer":"..."} '
            "Coordinates are relative to the provided image. "
            "Never include password field values."
        )
        try:
            from core.metrics import get_metrics

            get_metrics().record_model_call("vision")
            response = self._client.chat(
                model=self.model,
                messages=[
                    {"role": "system", "content": system},
                    {
                        "role": "user",
                        "content": prompt,
                        "images": [b64],
                    },
                ],
                options={"temperature": 0.1},
                keep_alive="15m",
            )
            content = (response.get("message") or {}).get("content", "").strip()
        except Exception as exc:
            print(f"[VISION] model error: {exc}")
            return {"summary": "", "elements": [], "error": str(exc)}

        parsed = _parse_json_loose(content)
        ms = (time.perf_counter() - t0) * 1000
        print(f"[VISION] model={self.model} {ms:.0f}ms")
        if not parsed:
            return {
                "summary": content[:400],
                "visible_text": [],
                "elements": [],
                "answer": content[:400],
                "raw": content[:800],
            }
        return parsed


def _image_to_b64(image: Any) -> str:
    if image is None:
        return ""
    try:
        if isinstance(image, (bytes, bytearray)):
            return base64.b64encode(image).decode("ascii")
        buf = io.BytesIO()
        if hasattr(image, "save"):
            image.save(buf, format="PNG")
            return base64.b64encode(buf.getvalue()).decode("ascii")
    except Exception as exc:
        print(f"[VISION] encode failed: {exc}")
    return ""


def _parse_json_loose(text: str) -> Optional[Dict[str, Any]]:
    text = (text or "").strip()
    if not text:
        return None
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        start = text.find("{")
        end = text.rfind("}")
        if start >= 0 and end > start:
            try:
                return json.loads(text[start : end + 1])
            except json.JSONDecodeError:
                return None
    return None


def elements_from_vision_payload(
    payload: Dict[str, Any],
    *,
    snapshot_id: str,
    image_width: int,
    image_height: int,
    screen_width: int,
    screen_height: int,
) -> List[UIElement]:
    from vision.coordinates import scale_bounds, validate_bounds_in_screen
    from vision.models import Bounds as B
    from vision.models import ScreenCapture

    raw_elements = payload.get("elements") or []
    out: List[UIElement] = []
    screen = B(0, 0, screen_width, screen_height)
    for idx, item in enumerate(raw_elements, start=1):
        if not isinstance(item, dict):
            continue
        try:
            x = int(item.get("x", 0))
            y = int(item.get("y", 0))
            w = int(item.get("width", item.get("w", 10)))
            h = int(item.get("height", item.get("h", 10)))
            bounds = B(x, y, max(1, w), max(1, h))
            if (image_width, image_height) != (screen_width, screen_height):
                bounds = scale_bounds(
                    bounds,
                    from_width=image_width,
                    from_height=image_height,
                    to_width=screen_width,
                    to_height=screen_height,
                )
            if validate_bounds_in_screen(bounds, screen):
                continue
            role = str(item.get("role") or "element")
            text = str(item.get("text") or "")
            eid = str(item.get("id") or f"vision_{idx}")
            out.append(
                UIElement(
                    id=eid,
                    role=role,
                    text=text,
                    bounds=bounds,
                    source="vision",
                    snapshot_id=snapshot_id,
                    confidence=float(item.get("confidence", 0.6)),
                )
            )
        except Exception:
            continue
    return out


vision_model_init_calls = 0


def create_default_vision_provider() -> VisionProvider:
    global vision_model_init_calls
    model = get_config_value("ai", "vision_model", default="")
    if not model:
        print("[VISION] No ai.vision_model configured — vision LLM disabled")
        return NullVisionProvider()
    try:
        vision_model_init_calls += 1
        provider = OllamaVisionProvider(model=model)
        if provider.available:
            return provider
    except Exception as exc:
        print(f"[VISION] Ollama vision init failed: {exc}")
    return NullVisionProvider()
