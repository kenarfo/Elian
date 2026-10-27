# ==========================
# ELIAN — UI semantic risk (Phase 5)
# ==========================

from __future__ import annotations

import re
from typing import Iterable, Optional

from core.risk import RiskLevel
from vision.models import UIElement


DANGEROUS_LABELS = (
    "usuń konto",
    "usun konto",
    "delete account",
    "delete",
    "usuń",
    "usun",
    "format",
    "wipe",
    "uninstall",
    "odinstaluj",
    "factory reset",
    "resetuj ustawienia",
    "zapłać",
    "zaplac",
    "pay now",
    "confirm purchase",
    "potwierdź zakup",
    "potwierdz zakup",
    "wyślij",
    "wyslij",
    "send money",
    "przelej",
    "shutdown",
    "wyłącz komputer",
    "wylacz komputer",
    "empty recycle",
    "opróżnij kosz",
)

PASSWORD_ROLES = ("password", "securetext", "secure text field")


def normalize_label(text: str) -> str:
    text = str(text or "").lower().strip()
    text = re.sub(r"\s+", " ", text)
    return text


def is_dangerous_label(text: str) -> bool:
    label = normalize_label(text)
    if not label:
        return False
    for phrase in DANGEROUS_LABELS:
        if phrase in label:
            return True
    return False


def escalate_element_risk(
    element: UIElement,
    *,
    base: RiskLevel = RiskLevel.MEDIUM,
) -> RiskLevel:
    if element.is_password or normalize_label(element.role) in PASSWORD_ROLES:
        return RiskLevel.DANGEROUS
    if is_dangerous_label(element.text):
        return RiskLevel.DANGEROUS
    role = normalize_label(element.role)
    if role in ("button", "link", "menuitem", "checkbox", "tab"):
        return base if base != RiskLevel.SAFE else RiskLevel.MEDIUM
    if role in ("textbox", "edit", "input", "combobox"):
        return RiskLevel.MEDIUM
    return base


def confirmation_prompt_for(element: UIElement) -> str:
    if element.is_password:
        return "To pole wygląda na hasło. Mam kontynuować wpisywanie?"
    label = element.text.strip() or element.role
    if is_dangerous_label(element.text):
        return f'Element „{label}” wygląda na niebezpieczny. Mam to zrobić?'
    return f'Czy na pewno mam kliknąć „{label}”?'


def sanitize_element_for_observation(element: UIElement) -> dict:
    data = element.to_planner_dict()
    if element.is_password:
        data["text"] = ""
        data.pop("value", None)
    return data


def filter_password_from_texts(texts: Iterable[str]) -> list:
    out = []
    for item in texts:
        low = normalize_label(item)
        if "password" in low or "hasło" in low or "haslo" in low:
            continue
        out.append(item)
    return out
