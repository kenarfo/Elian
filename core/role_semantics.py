# ==========================
# ELIAN — office / role semantics (Phase 11.4)
# ==========================
"""Requested political/office roles vs country — false-premise aware."""

from __future__ import annotations

import re
from dataclasses import asdict, dataclass, field
from typing import Any, Dict, List, Optional


ROLE_PRESIDENT = "president"
ROLE_PRIME_MINISTER = "prime_minister"
ROLE_HEAD_OF_STATE = "head_of_state"
ROLE_MONARCH = "monarch"
ROLE_HEAD_OF_GOVERNMENT = "head_of_government"
ROLE_CHANCELLOR = "chancellor"
ROLE_UNKNOWN = "unknown"


@dataclass
class RoleRequest:
    country: str = ""
    country_key: str = ""
    requested_role: str = ROLE_UNKNOWN
    role_exists: bool = True
    closest_roles: List[str] = field(default_factory=list)
    search_query: str = ""
    correction_pl: str = ""
    correction_en: str = ""

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


# Countries that are constitutional monarchies without a presidential office.
_NO_PRESIDENT = {
    "uk": {
        "names": ("wielkiej brytanii", "wielka brytania", "bryta", "uk", "england",
                  "united kingdom", "britain", "anglii", "anglia"),
        "closest": [ROLE_HEAD_OF_STATE, ROLE_MONARCH, ROLE_PRIME_MINISTER],
        "correction_pl": (
            "Wielka Brytania nie ma prezydenta. Jest monarchią konstytucyjną; "
            "głową państwa jest monarcha. Jeśli chodziło ci o premiera, "
            "mogę sprawdzić, kto nim obecnie jest."
        ),
        "correction_en": (
            "The United Kingdom does not have a president. It is a constitutional "
            "monarchy; the head of state is the monarch. If you meant the prime "
            "minister, I can check who currently holds that office."
        ),
        "search_if_forced": "United Kingdom head of state monarch",
    },
    "canada": {
        "names": ("kanady", "kanada", "canada"),
        "closest": [ROLE_HEAD_OF_STATE, ROLE_PRIME_MINISTER],
        "correction_pl": (
            "Kanada nie ma prezydenta w stylu republiki — to monarchia konstytucyjna "
            "z premierem jako szefem rządu. Mogę sprawdzić, kto jest obecnie premierem."
        ),
        "correction_en": (
            "Canada does not have a republican-style president — it is a "
            "constitutional monarchy with a prime minister as head of government."
        ),
        "search_if_forced": "Canada prime minister",
    },
    "australia": {
        "names": ("australii", "australia", "australię", "australie"),
        "closest": [ROLE_HEAD_OF_STATE, ROLE_PRIME_MINISTER],
        "correction_pl": (
            "Australia nie ma prezydenta republiki — to monarchia konstytucyjna "
            "z premierem. Mogę sprawdzić aktualnego premiera."
        ),
        "correction_en": (
            "Australia does not have a republican president — it is a "
            "constitutional monarchy with a prime minister."
        ),
        "search_if_forced": "Australia prime minister",
    },
}


_ROLE_PATTERNS = (
    (ROLE_PRIME_MINISTER, re.compile(r"\b(?:premier\w*|prime\s+minister)\b", re.I)),
    (ROLE_PRESIDENT, re.compile(r"\b(?:prezydent\w*|president)\b", re.I)),
    (ROLE_CHANCELLOR, re.compile(r"\b(?:kanclerz\w*|chancellor)\b", re.I)),
    (ROLE_MONARCH, re.compile(r"\b(?:kr[oó]l\w*|monarch\w*|queen|king)\b", re.I)),
    (ROLE_HEAD_OF_STATE, re.compile(r"\bg[lł]ow[aą]\s+pa[nń]stwa|head\s+of\s+state\b", re.I)),
    (
        ROLE_HEAD_OF_GOVERNMENT,
        re.compile(r"\bszef\w*\s+rz[aą]du|head\s+of\s+government\b", re.I),
    ),
)

_USA = re.compile(
    r"\b(?:usa|stan[oó]w\s+zjednoczonych|stanow\s+zjednoczonych|ameryk|"
    r"united\s+states)\b",
    re.I,
)


def detect_country(text: str) -> tuple[str, str]:
    low = str(text or "").lower()
    if _USA.search(low):
        return "United States", "us"
    for key, meta in _NO_PRESIDENT.items():
        for name in meta["names"]:
            if name in low:
                label = {
                    "uk": "United Kingdom",
                    "canada": "Canada",
                    "australia": "Australia",
                }.get(key, key)
                return label, key
    return "", ""


def detect_requested_role(text: str) -> str:
    raw = str(text or "")
    for role, pattern in _ROLE_PATTERNS:
        if pattern.search(raw):
            return role
    return ROLE_UNKNOWN


def analyze_role_request(text: str) -> RoleRequest:
    raw = str(text or "").strip()
    country, key = detect_country(raw)
    role = detect_requested_role(raw)
    info = RoleRequest(
        country=country,
        country_key=key,
        requested_role=role,
        role_exists=True,
    )
    if role == ROLE_PRESIDENT and key in _NO_PRESIDENT:
        meta = _NO_PRESIDENT[key]
        info.role_exists = False
        info.closest_roles = list(meta["closest"])
        info.correction_pl = meta["correction_pl"]
        info.correction_en = meta["correction_en"]
        info.search_query = ""  # no silent president→head_of_state remap
        return info

    if role == ROLE_PRIME_MINISTER and key == "uk":
        info.search_query = "current Prime Minister of the United Kingdom"
        return info
    if role == ROLE_PRESIDENT and key == "us":
        info.search_query = "current president of the United States"
        return info
    if role == ROLE_PRIME_MINISTER and key == "canada":
        info.search_query = "current Prime Minister of Canada"
        return info
    if role == ROLE_PRIME_MINISTER and key == "australia":
        info.search_query = "current Prime Minister of Australia"
        return info
    return info


def false_premise_reply(role: RoleRequest, *, lang: str = "pl") -> str:
    if role.role_exists:
        return ""
    if (lang or "pl").lower() == "en":
        return role.correction_en or role.correction_pl
    return role.correction_pl or role.correction_en
