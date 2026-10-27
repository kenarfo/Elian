# ==========================
# ELIAN — UIA + pixel fusion (C.5)
# ==========================
from __future__ import annotations

from typing import Any, Dict, List, Optional


SOURCE_PRIORITY = (
    "LOCAL_RUNTIME",
    "LOCAL_COMPUTER_UIA",
    "LOCAL_SCREEN_PIXELS",
    "VISION_LOCAL",
    "BROWSER_DOM",
    "LOCAL_HA",
    "WEB",
    "MEMORY",
    "MODEL_KNOWLEDGE",
)


def fuse_elements(
    uia_elements: Optional[List[Dict[str, Any]]] = None,
    vision_candidates: Optional[List[Dict[str, Any]]] = None,
) -> List[Dict[str, Any]]:
    fused: List[Dict[str, Any]] = []
    for el in list(uia_elements or []):
        row = dict(el)
        row.setdefault("source", ["LOCAL_COMPUTER_UIA"])
        fused.append(row)
    for cand in list(vision_candidates or []):
        label = str(cand.get("label") or cand.get("text") or "").strip().lower()
        matched = False
        if label:
            for row in fused:
                name = str(row.get("name") or row.get("text") or "").strip().lower()
                if name and name == label:
                    sources = list(row.get("source") or [])
                    if "VISION_LOCAL" not in sources:
                        sources.append("VISION_LOCAL")
                    row["source"] = sources
                    row["vision_confidence"] = cand.get("confidence")
                    matched = True
                    break
        if not matched:
            fused.append(
                {
                    "label": cand.get("label"),
                    "role": cand.get("role"),
                    "bbox": cand.get("bbox"),
                    "confidence": cand.get("confidence"),
                    "source": ["VISION_LOCAL", "LOCAL_SCREEN_PIXELS"],
                }
            )
    return fused
