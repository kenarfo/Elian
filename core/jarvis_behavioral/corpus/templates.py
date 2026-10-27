# ==========================
# ELIAN — corpus template helpers (P2)
# ==========================
from __future__ import annotations

import hashlib
import itertools
import random
from typing import Iterable, List, Sequence, Tuple


def seeded_rng(seed: int) -> random.Random:
    return random.Random(int(seed) & 0xFFFFFFFF)


def expand_template(template: str, rng: random.Random, *, limit: int = 0) -> List[str]:
    """Expand {a|b|c} alternations. Deterministic given rng state."""
    parts: List[List[str]] = []
    buf = ""
    i = 0
    while i < len(template):
        ch = template[i]
        if ch == "{":
            if buf:
                parts.append([buf])
                buf = ""
            j = template.find("}", i)
            if j < 0:
                buf += template[i:]
                break
            alts = [a.strip() for a in template[i + 1 : j].split("|") if a.strip() or a == ""]
            parts.append(alts or [""])
            i = j + 1
            continue
        buf += ch
        i += 1
    if buf:
        parts.append([buf])

    if not parts:
        return [template]
    # Cap cartesian product
    total = 1
    for p in parts:
        total *= max(1, len(p))
        if total > 5000:
            # Sample instead of full product
            out = []
            n = limit or 32
            for _ in range(n):
                out.append("".join(rng.choice(p) for p in parts).strip())
            return list(dict.fromkeys(out))
    combos = [" ".join("".join(combo).split()) for combo in itertools.product(*parts)]
    combos = [c.strip() for c in combos if c.strip()]
    if limit and len(combos) > limit:
        rng.shuffle(combos)
        combos = combos[:limit]
    return list(dict.fromkeys(combos))


def stable_id(prefix: str, text: str, seed: int = 0) -> str:
    h = hashlib.sha1(f"{seed}|{text}".encode("utf-8")).hexdigest()[:10]
    return f"{prefix}_{h}"


def strip_diacritics(text: str) -> str:
    table = str.maketrans(
        {
            "ą": "a",
            "ć": "c",
            "ę": "e",
            "ł": "l",
            "ń": "n",
            "ó": "o",
            "ś": "s",
            "ź": "z",
            "ż": "z",
            "Ą": "A",
            "Ć": "C",
            "Ę": "E",
            "Ł": "L",
            "Ń": "N",
            "Ó": "O",
            "Ś": "S",
            "Ź": "Z",
            "Ż": "Z",
        }
    )
    return text.translate(table)


def noisy_variants(text: str, rng: random.Random, *, n: int = 3) -> List[str]:
    """STT-like corruptions — not a production synonym dict."""
    out: List[str] = []
    base = text.strip()
    if not base:
        return out
    fillers = ("yyy", "no", "ten", "tego", "weź", "wez", "ej", "kurde")
    transforms = [
        lambda s: strip_diacritics(s),
        lambda s: s.replace(" ", "  "),
        lambda s: " ".join(reversed(s.split())) if len(s.split()) <= 4 else s,
        lambda s: f"{rng.choice(fillers)} {s}",
        lambda s: f"{s} {rng.choice(fillers)}",
        lambda s: s[:-1] if len(s) > 6 else s,
        lambda s: s.replace("rz", "sz").replace("ch", "h"),
        lambda s: s.lower(),
        lambda s: s.replace("ą", "a").replace("ę", "e"),
        lambda s: " ".join([w for w in s.split()] + [rng.choice(fillers)]),
    ]
    for _ in range(max(1, n)):
        fn = rng.choice(transforms)
        cand = " ".join(str(fn(base)).split())
        if cand and cand != base:
            out.append(cand)
    # Always include no-diacritics variant
    nd = strip_diacritics(base)
    if nd != base:
        out.append(nd)
    return list(dict.fromkeys(out))


def take_unique(items: Iterable[str], n: int) -> List[str]:
    seen = set()
    out: List[str] = []
    for item in items:
        key = " ".join(item.casefold().split())
        if not key or key in seen:
            continue
        seen.add(key)
        out.append(item.strip())
        if len(out) >= n:
            break
    return out
