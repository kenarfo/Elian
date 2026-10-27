"""Long-term facts with stable IDs (persistent)."""

from __future__ import annotations

from typing import Any

from memory.store import clean_text, new_id, now_iso, query_terms


class LongTermMemory:
    def __init__(self, facts=None):
        self.facts: list[dict[str, Any]] = list(facts or [])

    def all(self):
        return list(self.facts)

    def remember(
        self,
        text,
        *,
        category="fact",
        tags=None,
        explicit=True,
        source="user",
        confidence=1.0,
    ):
        text = clean_text(text, 500)
        if not text:
            return None

        normalized = text.casefold()
        for fact in self.facts:
            if fact.get("text", "").casefold() == normalized:
                fact["updated_at"] = now_iso()
                fact["last_used_at"] = now_iso()
                fact["explicit"] = bool(explicit) or bool(fact.get("explicit"))
                return fact

        entry = {
            "id": new_id("fact"),
            "text": text,
            "category": clean_text(category, 40) or "fact",
            "tags": [clean_text(tag, 40) for tag in (tags or []) if clean_text(tag, 40)],
            "explicit": bool(explicit),
            "source": clean_text(source, 40) or "user",
            "confidence": float(confidence),
            "created_at": now_iso(),
            "updated_at": now_iso(),
            "last_used_at": now_iso(),
        }
        self.facts.append(entry)
        self.facts = self.facts[-250:]
        return entry

    def search(self, query, limit=6):
        terms = query_terms(query)
        scored = []
        for index, fact in enumerate(self.facts):
            fact_terms = query_terms(fact.get("text", ""))
            tag_terms = query_terms(" ".join(fact.get("tags") or []))
            overlap = len(terms & (fact_terms | tag_terms))
            if terms and overlap == 0:
                if str(query or "").casefold() not in fact.get("text", "").casefold():
                    continue
                overlap = 1
            score = overlap * 10 + (2 if fact.get("explicit") else 0) + index / 1000
            scored.append((score, fact))

        if not terms:
            scored = [(index, fact) for index, fact in enumerate(self.facts)]

        selected = [
            fact
            for _score, fact in sorted(scored, key=lambda item: item[0], reverse=True)[
                : max(1, limit)
            ]
        ]
        for fact in selected:
            fact["last_used_at"] = now_iso()
        return [dict(fact) for fact in selected]

    def forget(self, query):
        terms = query_terms(query)
        query_cf = str(query or "").casefold().strip()
        if not terms and not query_cf:
            return []

        removed = []
        remaining = []
        for fact in self.facts:
            text = fact.get("text", "")
            fact_terms = query_terms(text)
            match = False
            if query_cf and query_cf in text.casefold():
                match = True
            elif terms and terms.issubset(fact_terms):
                match = True
            elif terms and len(terms & fact_terms) >= max(1, (len(terms) + 1) // 2):
                match = True
            if match:
                removed.append(dict(fact))
            else:
                remaining.append(fact)
        self.facts = remaining
        return removed

    def forget_by_id(self, fact_id):
        before = len(self.facts)
        self.facts = [fact for fact in self.facts if fact.get("id") != fact_id]
        return before - len(self.facts)
