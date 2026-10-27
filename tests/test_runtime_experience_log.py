# ==========================
# ELIAN — Runtime experience logging (Phase 1)
# ==========================
from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from core.self_improve.experience_log import (
    EVIDENCE_ALL,
    EVIDENCE_RUNTIME,
    PROVENANCE_MANUAL,
    PROVENANCE_RUNTIME,
    PROVENANCE_SEED,
    ExperienceEntry,
    ExperienceLogStore,
    FailureCategory,
    select_evidence,
)
from core.self_improve.gap_analysis import GapAnalyzer
from core.self_improve.reflection import ReflectionEngine
from core.self_improve.runtime_collector import (
    DEDUP_WINDOW,
    RuntimeExperienceCollector,
    _map_domain,
    begin_turn_tool_buffer,
    classify_turn,
    clear_turn_tool_buffer,
    fingerprint_for,
    normalize_fingerprint_error_code,
    note_tool_event,
    record_handle_result,
    sanitize_metadata,
    set_success_sampler,
    take_turn_tool_events,
)
from core.tool_result import ToolResult


def _cfg(**overrides):
    base = {
        "enabled": True,
        "store_utterance_hashes_only": True,
        "max_entries": 2000,
        "success_sample_rate": 1.0,
    }
    base.update(overrides)
    return base


class ClassifyTests(unittest.TestCase):
    def test_timeout(self):
        cat = classify_turn(
            source="agent",
            tool_events=[
                {
                    "tool": "open_app",
                    "status": "timeout",
                    "success": False,
                    "error_code": "TIMEOUT",
                }
            ],
        )
        self.assertIs(cat, FailureCategory.TIMEOUT)

    def test_unknown_tool_is_no_tool_match(self):
        cat = classify_turn(
            source="agent",
            tool_events=[
                {
                    "tool": "missing_thing",
                    "status": "error",
                    "success": False,
                    "error_code": "UNKNOWN_TOOL",
                }
            ],
        )
        self.assertIs(cat, FailureCategory.NO_TOOL_MATCH)

    def test_capability_unavailable_is_tool_error_not_no_match(self):
        cat = classify_turn(
            source="fast_path",
            tool_events=[
                {
                    "tool": "turn_off_device",
                    "status": "unavailable",
                    "success": False,
                    "error_code": "CAPABILITY_UNAVAILABLE",
                }
            ],
        )
        self.assertIs(cat, FailureCategory.TOOL_ERROR)

    def test_tool_failure(self):
        cat = classify_turn(
            source="agent",
            tool_events=[
                {
                    "tool": "play_media",
                    "status": "error",
                    "success": False,
                    "error_code": "EXEC_FAILED",
                }
            ],
        )
        self.assertIs(cat, FailureCategory.TOOL_ERROR)

    def test_correction(self):
        self.assertIs(
            classify_turn(source="correction", utterance_class="CORRECTION"),
            FailureCategory.USER_CORRECTION,
        )
        self.assertIs(
            classify_turn(source="agent", utterance_class="CORRECTION"),
            FailureCategory.USER_CORRECTION,
        )

    def test_success_from_tool(self):
        cat = classify_turn(
            source="fast_path",
            tool_events=[
                {
                    "tool": "set_volume",
                    "status": "ok",
                    "success": True,
                    "error_code": "",
                }
            ],
        )
        self.assertIs(cat, FailureCategory.SUCCESS)

    def test_clarify_with_failed_like_tool_is_not_tool_error(self):
        cat = classify_turn(
            source="clarify",
            tool_events=[
                {
                    "tool": "find_contact",
                    "status": "failure",
                    "success": False,
                    "error_code": "FAILURE",
                }
            ],
        )
        self.assertIs(cat, FailureCategory.SUCCESS)
        self.assertIsNot(cat, FailureCategory.TOOL_ERROR)

    def test_ambiguous_code_is_not_tool_error(self):
        cat = classify_turn(
            source="agent",
            tool_events=[
                {
                    "tool": "find_contact",
                    "status": "failure",
                    "success": False,
                    "error_code": "AMBIGUOUS",
                }
            ],
        )
        self.assertIsNot(cat, FailureCategory.TOOL_ERROR)

    def test_proactive_pref_is_success_not_unknown(self):
        """Preference toggles must not fabricate knowledge/UNKNOWN gaps."""
        from core.self_improve.runtime_collector import _map_domain

        self.assertIs(
            classify_turn(source="proactive_pref"),
            FailureCategory.SUCCESS,
        )
        self.assertIs(
            classify_turn(source="proactive_offer"),
            FailureCategory.SUCCESS,
        )
        self.assertEqual(
            _map_domain(source="proactive_pref", intent="", tool="", domains=None),
            "system",
        )
        self.assertNotEqual(
            _map_domain(source="proactive_pref", intent="", tool="", domains=None),
            "knowledge",
        )


class SanitizeTests(unittest.TestCase):
    def test_strips_secrets_and_truncates(self):
        cleaned = sanitize_metadata(
            {
                "token": "super-secret",
                "api_key": "abc",
                "password": "x",
                "authorization": "Bearer xyz",
                "source": "agent",
                "intent": "smart_home_control",
                "long": "x" * 200,
                "nested_dump": {"a": 1, "b": 2},
                "tools": [
                    {
                        "tool": "open_app",
                        "status": "ok",
                        "success": True,
                        "error_code": "",
                        "password": "nope",
                    }
                ],
            }
        )
        self.assertNotIn("token", cleaned)
        self.assertNotIn("api_key", cleaned)
        self.assertNotIn("password", cleaned)
        self.assertNotIn("authorization", cleaned)
        self.assertNotIn("nested_dump", cleaned)
        self.assertEqual(cleaned["source"], "agent")
        self.assertLessEqual(len(cleaned["long"]), 80)
        self.assertEqual(cleaned["tools"][0]["tool"], "open_app")
        self.assertNotIn("password", cleaned["tools"][0])

    def test_summary_and_entry_never_hold_raw_user_text(self):
        with tempfile.TemporaryDirectory() as tmp:
            store = ExperienceLogStore(Path(tmp) / "log.json")
            manager = SimpleNamespace(
                _experience_session_id="sess_test",
                _decision_envelope=SimpleNamespace(
                    intent="open_app",
                    utterance_class="GOAL",
                    confidence=0.9,
                    allowed_domains=["APP"],
                ),
                _last_utterance_class="GOAL",
                last_observations=[],
            )
            result = SimpleNamespace(
                source="agent",
                data={},
                text="Otwórz Spotify proszę z hasłem secret123",
            )
            begin_turn_tool_buffer()
            note_tool_event(
                tool_name="open_app",
                status="error",
                success=False,
                error_code="EXEC_FAILED",
            )
            with patch(
                "core.self_improve.runtime_collector._runtime_logging_config",
                return_value=_cfg(),
            ):
                entry = record_handle_result(
                    manager, result, started_monotonic=0.0, store=store
                )
            self.assertIsNotNone(entry)
            blob = str(entry.to_dict())
            self.assertNotIn("Otwórz Spotify", blob)
            self.assertNotIn("secret123", blob)
            self.assertNotIn("hasłem", blob)
            self.assertLessEqual(len(entry.summary), 120)


class UtteranceClassMetadataTests(unittest.TestCase):
    """C1: metadata.utterance_class must be a stable class label, not a dataclass repr."""

    def tearDown(self):
        clear_turn_tool_buffer()

    def test_normalize_helper_handles_object_string_none(self):
        from core.self_improve.runtime_collector import _normalize_utterance_class
        from core.utterance_class import UtteranceClassification

        self.assertEqual(
            _normalize_utterance_class(
                UtteranceClassification(utterance_class="ACTION_REQUEST")
            ),
            "ACTION_REQUEST",
        )
        self.assertEqual(_normalize_utterance_class("GOAL"), "GOAL")
        self.assertEqual(_normalize_utterance_class("  GOAL  "), "GOAL")
        self.assertEqual(_normalize_utterance_class(None), "")
        self.assertEqual(_normalize_utterance_class(""), "")
        # Truncated/corrupt repr must not be kept as a label.
        self.assertEqual(
            _normalize_utterance_class(
                "UtteranceClassification(utterance_class="
            ),
            "",
        )

    def test_record_handle_result_uses_classification_object_label(self):
        from core.utterance_class import UtteranceClassification

        with tempfile.TemporaryDirectory() as tmp:
            store = ExperienceLogStore(Path(tmp) / "log.json")
            manager = SimpleNamespace(
                _experience_session_id="sess_uc",
                _decision_envelope=SimpleNamespace(
                    intent="open_app",
                    utterance_class="",  # falsy → fall back to object on manager
                    confidence=0.9,
                    allowed_domains=["APP"],
                ),
                _last_utterance_class=UtteranceClassification(
                    utterance_class="GOAL"
                ),
                last_observations=[],
            )
            result = SimpleNamespace(source="agent", data={}, text="ok")
            begin_turn_tool_buffer()
            note_tool_event(
                tool_name="open_app",
                status="error",
                success=False,
                error_code="TOOL_FAILED",
            )
            with patch(
                "core.self_improve.runtime_collector._runtime_logging_config",
                return_value=_cfg(),
            ):
                entry = record_handle_result(
                    manager, result, started_monotonic=0.0, store=store
                )
            self.assertIsNotNone(entry)
            uc = (entry.metadata or {}).get("utterance_class")
            self.assertEqual(uc, "GOAL")
            self.assertNotIn("UtteranceClassification(", str(uc))
            self.assertNotIn("UtteranceClassification(", str(entry.to_dict()))

    def test_record_handle_result_keeps_plain_string_and_tolerates_none(self):
        with tempfile.TemporaryDirectory() as tmp:
            store = ExperienceLogStore(Path(tmp) / "log.json")
            manager = SimpleNamespace(
                _experience_session_id="sess_uc2",
                _decision_envelope=SimpleNamespace(
                    intent="open_app",
                    utterance_class="ACTION_REQUEST",
                    confidence=0.9,
                    allowed_domains=["APP"],
                ),
                _last_utterance_class=None,
                last_observations=[],
            )
            result = SimpleNamespace(source="agent", data={}, text="ok")
            begin_turn_tool_buffer()
            note_tool_event(
                tool_name="open_app",
                status="error",
                success=False,
                error_code="TOOL_FAILED",
            )
            with patch(
                "core.self_improve.runtime_collector._runtime_logging_config",
                return_value=_cfg(),
            ):
                entry = record_handle_result(
                    manager, result, started_monotonic=0.0, store=store
                )
            self.assertIsNotNone(entry)
            self.assertEqual(
                (entry.metadata or {}).get("utterance_class"), "ACTION_REQUEST"
            )


class ToolBufferTests(unittest.TestCase):
    def tearDown(self):
        clear_turn_tool_buffer()

    def test_buffer_collects_without_writing_store(self):
        begin_turn_tool_buffer()
        note_tool_event(
            tool_name="open_app", status="ok", success=True, error_code=""
        )
        note_tool_event(
            tool_name="play_media",
            status="error",
            success=False,
            error_code="EXEC_FAILED",
        )
        events = take_turn_tool_events()
        self.assertEqual(len(events), 2)
        self.assertEqual(events[0]["tool"], "open_app")
        self.assertEqual(events[1]["error_code"], "EXEC_FAILED")


class SamplingAndDedupTests(unittest.TestCase):
    def tearDown(self):
        clear_turn_tool_buffer()
        set_success_sampler(None)

    def _manager(self, intent="play_media", domains=None):
        return SimpleNamespace(
            _experience_session_id="sess_sd",
            _decision_envelope=SimpleNamespace(
                intent=intent,
                utterance_class="GOAL",
                confidence=0.9,
                allowed_domains=list(domains or ["MEDIA"]),
            ),
            _last_utterance_class="GOAL",
            last_observations=[],
        )

    def _record_success(self, store, sampler):
        begin_turn_tool_buffer()
        note_tool_event(
            tool_name="play_media", status="ok", success=True, error_code=""
        )
        with patch(
            "core.self_improve.runtime_collector._runtime_logging_config",
            return_value=_cfg(success_sample_rate=0.5),
        ):
            return record_handle_result(
                self._manager(),
                SimpleNamespace(source="agent", data={}),
                started_monotonic=0.0,
                store=store,
                success_sampler=sampler,
            )

    def _record_failure(self, store, *, tool="play_media", error_code="EXEC_FAILED", intent="play_media"):
        begin_turn_tool_buffer()
        note_tool_event(
            tool_name=tool,
            status="error",
            success=False,
            error_code=error_code,
        )
        with patch(
            "core.self_improve.runtime_collector._runtime_logging_config",
            return_value=_cfg(),
        ):
            return record_handle_result(
                self._manager(intent=intent),
                SimpleNamespace(source="agent", data={}),
                started_monotonic=0.0,
                store=store,
            )

    def test_per_occurrence_success_sampling(self):
        draws = iter([0.01, 0.99, 0.02])  # keep, drop, keep

        def sampler():
            return next(draws)

        with tempfile.TemporaryDirectory() as tmp:
            store = ExperienceLogStore(Path(tmp) / "log.json")
            e1 = self._record_success(store, sampler)
            e2 = self._record_success(store, sampler)
            e3 = self._record_success(store, sampler)
            self.assertIsNotNone(e1)
            self.assertIsNone(e2)
            self.assertIsNotNone(e3)
            self.assertEqual(len(store.load()), 2)
            # Same fingerprint across all three — not permanently keep/drop.
            fp = e1.metadata.get("fingerprint")
            self.assertEqual(e3.metadata.get("fingerprint"), fp)

    def test_sampling_rate_zero_still_persists_failures(self):
        with tempfile.TemporaryDirectory() as tmp:
            store = ExperienceLogStore(Path(tmp) / "log.json")
            begin_turn_tool_buffer()
            note_tool_event(
                tool_name="play_media",
                status="error",
                success=False,
                error_code="EXEC_FAILED",
            )
            with patch(
                "core.self_improve.runtime_collector._runtime_logging_config",
                return_value=_cfg(success_sample_rate=0.0),
            ):
                entry = record_handle_result(
                    self._manager(),
                    SimpleNamespace(source="agent", data={}),
                    started_monotonic=0.0,
                    store=store,
                )
            self.assertIsNotNone(entry)
            self.assertEqual(entry.category, FailureCategory.TOOL_ERROR)
            self.assertEqual(entry.repeat_count, 1)

    def test_failure_repeat_count_increments(self):
        with tempfile.TemporaryDirectory() as tmp:
            store = ExperienceLogStore(Path(tmp) / "log.json")
            e1 = self._record_failure(store)
            e2 = self._record_failure(store)
            e3 = self._record_failure(store)
            self.assertEqual(e1.id, e2.id)
            self.assertEqual(e2.id, e3.id)
            self.assertEqual(e3.repeat_count, 3)
            self.assertEqual(len(store.load()), 1)

    def test_dedup_window_creates_new_row(self):
        with tempfile.TemporaryDirectory() as tmp:
            store = ExperienceLogStore(Path(tmp) / "log.json")
            first = self._record_failure(store, tool="play_media", intent="play_media")
            # Fill the window with distinct other runtime failures.
            for i in range(DEDUP_WINDOW):
                self._record_failure(
                    store,
                    tool=f"open_app",
                    error_code="EXEC_FAILED",
                    intent=f"open_app_{i}",
                )
            # Original fingerprint is outside the window → new physical row.
            again = self._record_failure(store, tool="play_media", intent="play_media")
            self.assertNotEqual(first.id, again.id)
            self.assertEqual(again.repeat_count, 1)
            self.assertEqual(first.repeat_count, 1)

    def test_different_categories_do_not_merge(self):
        with tempfile.TemporaryDirectory() as tmp:
            store = ExperienceLogStore(Path(tmp) / "log.json")
            begin_turn_tool_buffer()
            note_tool_event(
                tool_name="open_app",
                status="error",
                success=False,
                error_code="EXEC_FAILED",
            )
            with patch(
                "core.self_improve.runtime_collector._runtime_logging_config",
                return_value=_cfg(),
            ):
                err = record_handle_result(
                    self._manager(intent="open_app", domains=["APP"]),
                    SimpleNamespace(source="agent", data={}),
                    started_monotonic=0.0,
                    store=store,
                )
            begin_turn_tool_buffer()
            note_tool_event(
                tool_name="open_app",
                status="timeout",
                success=False,
                error_code="TIMEOUT",
            )
            with patch(
                "core.self_improve.runtime_collector._runtime_logging_config",
                return_value=_cfg(),
            ):
                timeout = record_handle_result(
                    self._manager(intent="open_app", domains=["APP"]),
                    SimpleNamespace(source="agent", data={}),
                    started_monotonic=0.0,
                    store=store,
                )
            self.assertEqual(err.category, FailureCategory.TOOL_ERROR)
            self.assertEqual(timeout.category, FailureCategory.TIMEOUT)
            self.assertNotEqual(err.id, timeout.id)
            self.assertEqual(len(store.load()), 2)

    def test_legacy_repeat_count_defaults_to_one(self):
        entry = ExperienceEntry.from_dict(
            {
                "domain": "web",
                "category": "TOOL_ERROR",
                "summary": "legacy",
                "provenance": "runtime",
            }
        )
        self.assertEqual(entry.repeat_count, 1)
        self.assertEqual(entry.occurrence_weight, 1)

    def test_seed_row_not_incremented_by_runtime(self):
        with tempfile.TemporaryDirectory() as tmp:
            store = ExperienceLogStore(Path(tmp) / "log.json")
            seed = store.append(
                ExperienceEntry(
                    domain="media",
                    category=FailureCategory.TOOL_ERROR,
                    summary="seed fail",
                    tool="play_media",
                    provenance=PROVENANCE_SEED,
                    metadata={
                        "fingerprint": fingerprint_for(
                            intent="play_media",
                            tool="play_media",
                            category="TOOL_ERROR",
                            error_code="EXEC_FAILED",
                        )
                    },
                )
            )
            runtime = self._record_failure(store)
            self.assertNotEqual(seed.id, runtime.id)
            reloaded = {e.id: e for e in store.load()}
            self.assertEqual(reloaded[seed.id].repeat_count, 1)
            self.assertEqual(reloaded[runtime.id].repeat_count, 1)

    def test_different_error_codes_do_not_dedup(self):
        with tempfile.TemporaryDirectory() as tmp:
            store = ExperienceLogStore(Path(tmp) / "log.json")
            a = self._record_failure(
                store,
                tool="find_contact",
                error_code="FAILURE",
                intent="send_message",
            )
            b = self._record_failure(
                store,
                tool="find_contact",
                error_code="RATE_LIMIT",
                intent="send_message",
            )
            self.assertIsNotNone(a)
            self.assertIsNotNone(b)
            self.assertNotEqual(a.id, b.id)
            self.assertNotEqual(
                a.metadata.get("fingerprint"), b.metadata.get("fingerprint")
            )
            self.assertEqual(a.repeat_count, 1)
            self.assertEqual(b.repeat_count, 1)
            self.assertEqual(len(store.load()), 2)

    def test_independent_repeat_counts_per_error_code(self):
        """FAILURE×3 and RATE_LIMIT×2 stay independent within the dedup window."""
        with tempfile.TemporaryDirectory() as tmp:
            store = ExperienceLogStore(Path(tmp) / "log.json")
            codes = ["FAILURE", "FAILURE", "RATE_LIMIT", "RATE_LIMIT", "FAILURE"]
            for code in codes:
                self._record_failure(
                    store,
                    tool="find_contact",
                    error_code=code,
                    intent="send_message",
                )
            rows = store.load()
            self.assertEqual(len(rows), 2)
            by_code = {str(r.metadata.get("error_code") or ""): r for r in rows}
            self.assertEqual(by_code["FAILURE"].repeat_count, 3)
            self.assertEqual(by_code["RATE_LIMIT"].repeat_count, 2)
            self.assertNotEqual(
                by_code["FAILURE"].metadata.get("fingerprint"),
                by_code["RATE_LIMIT"].metadata.get("fingerprint"),
            )

    def test_missing_error_code_dedups_consistently(self):
        with tempfile.TemporaryDirectory() as tmp:
            store = ExperienceLogStore(Path(tmp) / "log.json")
            e1 = self._record_failure(
                store, tool="find_contact", error_code="", intent="send_message"
            )
            e2 = self._record_failure(
                store, tool="find_contact", error_code="", intent="send_message"
            )
            self.assertEqual(e1.id, e2.id)
            self.assertEqual(e2.repeat_count, 2)
            self.assertEqual(len(store.load()), 1)

    def test_old_fingerprint_without_error_code_does_not_merge(self):
        """Legacy fingerprints (pre-error_code) must not absorb new runtime failures."""
        with tempfile.TemporaryDirectory() as tmp:
            store = ExperienceLogStore(Path(tmp) / "log.json")
            import hashlib

            old_base = "|".join(["send_message", "find_contact", "TOOL_ERROR"])
            old_fp = hashlib.sha256(old_base.encode("utf-8")).hexdigest()[:16]
            new_fp = fingerprint_for(
                intent="send_message",
                tool="find_contact",
                category="TOOL_ERROR",
                error_code="FAILURE",
            )
            self.assertNotEqual(old_fp, new_fp)
            store.append(
                ExperienceEntry(
                    domain="messaging",
                    category=FailureCategory.TOOL_ERROR,
                    summary="legacy runtime",
                    tool="find_contact",
                    provenance=PROVENANCE_RUNTIME,
                    metadata={
                        "fingerprint": old_fp,
                        "error_code": "FAILURE",
                        "intent": "send_message",
                    },
                )
            )
            anew = self._record_failure(
                store,
                tool="find_contact",
                error_code="FAILURE",
                intent="send_message",
            )
            rows = store.load()
            self.assertEqual(len(rows), 2)
            self.assertEqual(rows[0].repeat_count, 1)
            self.assertEqual(anew.repeat_count, 1)
            self.assertNotEqual(rows[0].id, anew.id)

    def test_reflection_sees_separate_error_code_rows(self):
        with tempfile.TemporaryDirectory() as tmp:
            store = ExperienceLogStore(Path(tmp) / "log.json")

            def _msg_failure(error_code: str):
                begin_turn_tool_buffer()
                note_tool_event(
                    tool_name="find_contact",
                    status="error",
                    success=False,
                    error_code=error_code,
                )
                with patch(
                    "core.self_improve.runtime_collector._runtime_logging_config",
                    return_value=_cfg(),
                ):
                    return record_handle_result(
                        self._manager(
                            intent="send_message", domains=["MESSAGING"]
                        ),
                        SimpleNamespace(source="agent", data={}),
                        started_monotonic=0.0,
                        store=store,
                    )

            for _ in range(3):
                _msg_failure("FAILURE")
            for _ in range(2):
                _msg_failure("RATE_LIMIT")
            rows = store.load()
            self.assertEqual(len(rows), 2)
            self.assertTrue(all(r.domain == "messaging" for r in rows))
            report = ReflectionEngine(store).reflect(evidence=EVIDENCE_RUNTIME)
            cand = report.candidate_for("messaging")
            self.assertIsNotNone(cand)
            self.assertEqual(cand.observed_tool, "find_contact")
            self.assertEqual(cand.error_code, "FAILURE")
            self.assertEqual(cand.error_code_counts.get("FAILURE"), 3)
            self.assertEqual(cand.error_code_counts.get("RATE_LIMIT"), 2)

    def test_reflection_uses_occurrence_weights(self):
        with tempfile.TemporaryDirectory() as tmp:
            store = ExperienceLogStore(Path(tmp) / "log.json")
            store.append(
                ExperienceEntry(
                    domain="web",
                    category=FailureCategory.TOOL_ERROR,
                    summary="weighted fail",
                    tool="web_search",
                    provenance=PROVENANCE_RUNTIME,
                    repeat_count=5,
                )
            )
            store.append(
                ExperienceEntry(
                    domain="web",
                    category=FailureCategory.SUCCESS,
                    summary="one success",
                    tool="web_search",
                    provenance=PROVENANCE_RUNTIME,
                    repeat_count=1,
                )
            )
            report = ReflectionEngine(store).reflect(evidence=EVIDENCE_RUNTIME)
            self.assertEqual(report.entry_count, 6)
            web = next(d for d in report.domains if d.domain == "web")
            self.assertEqual(web.failures, 5)
            self.assertEqual(web.total, 6)
            self.assertAlmostEqual(web.failure_rate, 5 / 6, places=3)


class DomainMappingTests(unittest.TestCase):
    def tearDown(self):
        clear_turn_tool_buffer()

    def test_messaging_from_find_contact_tool(self):
        self.assertEqual(
            _map_domain(
                intent="general_conversation",
                domains=[],
                tool="find_contact",
                source="agent",
            ),
            "messaging",
        )
        self.assertEqual(
            _map_domain(intent="", domains=[], tool="send_message", source="agent"),
            "messaging",
        )

    def test_smart_home_from_tool_and_allowed_domains(self):
        self.assertEqual(
            _map_domain(
                intent="general_conversation",
                domains=[],
                tool="set_device_value",
                source="agent",
            ),
            "smart_home",
        )
        self.assertEqual(
            _map_domain(
                intent="unclassified_request",
                domains=["SMART_HOME"],
                tool="",
                source="agent",
            ),
            "smart_home",
        )

    def test_app_from_open_app(self):
        self.assertEqual(
            _map_domain(
                intent="general_conversation",
                domains=[],
                tool="open_app",
                source="fast_path",
            ),
            "app",
        )
        self.assertEqual(
            _map_domain(intent="open_app", domains=[], tool="", source="agent"),
            "app",
        )

    def test_allowed_domains_beat_weaker_intent(self):
        self.assertEqual(
            _map_domain(
                intent="knowledge_explanation",
                domains=["MEDIA"],
                tool="",
                source="agent",
            ),
            "media",
        )

    def test_tool_beats_generic_intent_and_empty_domains(self):
        # Audit case: Napisz do Kuby → general_conversation + find_contact
        self.assertEqual(
            _map_domain(
                intent="general_conversation",
                domains=[],
                tool="find_contact",
                source="clarify",
            ),
            "messaging",
        )

    def test_weak_system_allowed_defers_to_tool(self):
        self.assertEqual(
            _map_domain(
                intent="general_conversation",
                domains=["SYSTEM"],
                tool="play_media",
                source="agent",
            ),
            "media",
        )

    def test_unknown_stays_system(self):
        self.assertEqual(
            _map_domain(
                intent="general_conversation",
                domains=[],
                tool="",
                source="agent",
            ),
            "system",
        )

    def test_runtime_entry_stores_messaging_domain(self):
        with tempfile.TemporaryDirectory() as tmp:
            store = ExperienceLogStore(Path(tmp) / "log.json")
            begin_turn_tool_buffer()
            note_tool_event(
                tool_name="find_contact",
                status="error",
                success=False,
                error_code="EXEC_FAILED",
            )
            manager = SimpleNamespace(
                _experience_session_id="sess_dom",
                _decision_envelope=SimpleNamespace(
                    intent="general_conversation",
                    utterance_class="UNKNOWN",
                    confidence=0.8,
                    allowed_domains=[],
                ),
                _last_utterance_class="UNKNOWN",
                last_observations=[],
            )
            with patch(
                "core.self_improve.runtime_collector._runtime_logging_config",
                return_value=_cfg(),
            ):
                entry = record_handle_result(
                    manager,
                    SimpleNamespace(source="agent", data={}),
                    started_monotonic=0.0,
                    store=store,
                )
            self.assertIsNotNone(entry)
            self.assertEqual(entry.domain, "messaging")
            self.assertEqual(entry.provenance, PROVENANCE_RUNTIME)


class ProvenanceIsolationTests(unittest.TestCase):
    def tearDown(self):
        clear_turn_tool_buffer()

    def test_runtime_entry_has_runtime_provenance(self):
        with tempfile.TemporaryDirectory() as tmp:
            store = ExperienceLogStore(Path(tmp) / "log.json")
            begin_turn_tool_buffer()
            note_tool_event(
                tool_name="play_media",
                status="error",
                success=False,
                error_code="EXEC_FAILED",
            )
            manager = SimpleNamespace(
                _experience_session_id="sess_p",
                _decision_envelope=SimpleNamespace(
                    intent="play_media",
                    utterance_class="GOAL",
                    confidence=0.9,
                    allowed_domains=["MEDIA"],
                ),
                _last_utterance_class="GOAL",
                last_observations=[],
            )
            with patch(
                "core.self_improve.runtime_collector._runtime_logging_config",
                return_value=_cfg(),
            ):
                entry = record_handle_result(
                    manager,
                    SimpleNamespace(source="agent", data={}),
                    started_monotonic=0.0,
                    store=store,
                )
            self.assertIsNotNone(entry)
            self.assertEqual(entry.provenance, PROVENANCE_RUNTIME)
            self.assertTrue(entry.is_runtime)

    def test_seed_failures_excluded_from_default_reflection(self):
        with tempfile.TemporaryDirectory() as tmp:
            store = ExperienceLogStore(Path(tmp) / "log.json")
            for i in range(3):
                store.append(
                    ExperienceEntry(
                        domain="scheduling",
                        category=FailureCategory.NO_TOOL_MATCH,
                        summary=f"seed fail {i}",
                        provenance=PROVENANCE_SEED,
                    )
                )
            store.append(
                ExperienceEntry(
                    domain="scheduling",
                    category=FailureCategory.SUCCESS,
                    summary="runtime ok",
                    tool="create_reminder",
                    provenance=PROVENANCE_RUNTIME,
                )
            )
            report = ReflectionEngine(store).reflect()  # default = runtime
            self.assertEqual(report.entry_count, 1)
            self.assertEqual(report.candidates, [])
            gaps = GapAnalyzer(
                store,
                gaps_path=Path(tmp) / "gaps.json",
                map_path=Path(tmp) / "map.json",
                manifest_path=Path(tmp) / "adopted.json",
                learned_dir=Path(tmp) / "learned",
            ).analyze()
            self.assertEqual(gaps, [])

    def test_runtime_failures_counted_not_seed(self):
        with tempfile.TemporaryDirectory() as tmp:
            store = ExperienceLogStore(Path(tmp) / "log.json")
            for i in range(2):
                store.append(
                    ExperienceEntry(
                        domain="web",
                        category=FailureCategory.TOOL_ERROR,
                        summary=f"seed {i}",
                        provenance=PROVENANCE_SEED,
                    )
                )
            for i in range(3):
                store.append(
                    ExperienceEntry(
                        domain="web",
                        category=FailureCategory.TOOL_ERROR,
                        summary=f"runtime {i}",
                        tool="web_search",
                        provenance=PROVENANCE_RUNTIME,
                    )
                )
            report = ReflectionEngine(store).reflect(evidence=EVIDENCE_RUNTIME)
            self.assertEqual(report.entry_count, 3)
            web = next(d for d in report.domains if d.domain == "web")
            self.assertEqual(web.failures, 3)
            self.assertEqual(web.total, 3)
            # all-evidence would see 5
            all_report = ReflectionEngine(store).reflect(evidence=EVIDENCE_ALL)
            all_web = next(d for d in all_report.domains if d.domain == "web")
            self.assertEqual(all_web.failures, 5)

    def test_legacy_missing_provenance_is_seed(self):
        entry = ExperienceEntry.from_dict(
            {
                "domain": "scheduling",
                "category": "NO_TOOL_MATCH",
                "summary": "legacy row",
            }
        )
        self.assertIsNotNone(entry)
        self.assertEqual(entry.provenance, PROVENANCE_SEED)
        self.assertFalse(entry.is_runtime)
        selected = select_evidence([entry], EVIDENCE_RUNTIME)
        self.assertEqual(selected, [])

    def test_select_evidence_never_falls_back_to_all_on_bad_scope(self):
        rows = [
            ExperienceEntry(
                domain="a",
                category=FailureCategory.TOOL_ERROR,
                summary="seed",
                provenance=PROVENANCE_SEED,
            ),
            ExperienceEntry(
                domain="a",
                category=FailureCategory.TOOL_ERROR,
                summary="rt",
                provenance=PROVENANCE_RUNTIME,
            ),
        ]
        # Unknown scope must behave like runtime-only, not all.
        selected = select_evidence(rows, evidence="garbage")
        self.assertEqual(len(selected), 1)
        self.assertEqual(selected[0].provenance, PROVENANCE_RUNTIME)

    def test_manual_provenance_excluded_from_runtime(self):
        entry = ExperienceEntry(
            domain="web",
            category=FailureCategory.VAGUE_ANSWER,
            summary="hand logged",
            provenance=PROVENANCE_MANUAL,
        )
        self.assertEqual(entry.provenance, PROVENANCE_MANUAL)
        self.assertEqual(select_evidence([entry], EVIDENCE_RUNTIME), [])


class ClarificationSemanticsTests(unittest.TestCase):
    """Final clarify outcome must not create reliability failures."""

    def tearDown(self):
        clear_turn_tool_buffer()

    def _manager(self):
        return SimpleNamespace(
            _experience_session_id="sess_clarify",
            _decision_envelope=SimpleNamespace(
                intent="general_conversation",
                utterance_class="UNKNOWN",
                confidence=0.8,
                allowed_domains=[],
            ),
            _last_utterance_class="UNKNOWN",
            last_observations=[],
        )

    def test_normal_clarification_not_persisted_as_failure(self):
        with tempfile.TemporaryDirectory() as tmp:
            store = ExperienceLogStore(Path(tmp) / "log.json")
            begin_turn_tool_buffer()
            note_tool_event(
                tool_name="find_contact",
                status="failure",
                success=False,
                error_code="FAILURE",
            )
            with patch(
                "core.self_improve.runtime_collector._runtime_logging_config",
                return_value=_cfg(),
            ):
                entry = record_handle_result(
                    self._manager(),
                    SimpleNamespace(source="clarify", data={}),
                    started_monotonic=0.0,
                    store=store,
                )
            self.assertIsNone(entry)
            self.assertEqual(store.load(), [])

    def test_ambiguous_contact_clarification_not_reliability_failure(self):
        with tempfile.TemporaryDirectory() as tmp:
            store = ExperienceLogStore(Path(tmp) / "log.json")
            begin_turn_tool_buffer()
            # Mirror ToolResult.fail default + data.error_code=AMBIGUOUS via buffer.
            note_tool_event(
                tool_name="find_contact",
                status="failure",
                success=False,
                error_code="AMBIGUOUS",
            )
            with patch(
                "core.self_improve.runtime_collector._runtime_logging_config",
                return_value=_cfg(),
            ):
                entry = record_handle_result(
                    self._manager(),
                    SimpleNamespace(source="clarify", data={}),
                    started_monotonic=0.0,
                    store=store,
                )
            self.assertIsNone(entry)
            for row in store.load():
                self.assertNotEqual(row.category, FailureCategory.TOOL_ERROR)
                self.assertNotEqual(row.outcome, "failure")

    def test_tool_registry_preserves_ambiguous_from_data(self):
        from core.tools import Tool, ToolRegistry

        begin_turn_tool_buffer()
        registry = ToolRegistry()

        def ambiguous(_args):
            return ToolResult.fail(
                "Znalazłem kilku Kubów. Który?",
                tool_name="find_contact",
                data={"error_code": "AMBIGUOUS", "matches": ["A", "B"]},
            )

        registry.register(
            Tool(
                name="find_contact",
                description="test",
                parameters={},
                handler=ambiguous,
                available=True,
            )
        )
        result = registry.invoke("find_contact", {"name": "Kuba"}, force=True, skip_validation=True)
        self.assertFalse(result.success)
        self.assertEqual(result.error_code, "FAILURE")  # global fail() unchanged
        events = take_turn_tool_events()
        self.assertTrue(events)
        self.assertEqual(events[-1].get("error_code"), "AMBIGUOUS")
        clear_turn_tool_buffer()


class RecordTurnTests(unittest.TestCase):
    def tearDown(self):
        clear_turn_tool_buffer()

    def _manager(self, **envelope_kw):
        env = SimpleNamespace(
            intent=envelope_kw.get("intent", "play_media"),
            utterance_class=envelope_kw.get("utterance_class", "GOAL"),
            confidence=envelope_kw.get("confidence", 0.91),
            allowed_domains=envelope_kw.get("allowed_domains", ["MEDIA"]),
        )
        return SimpleNamespace(
            _experience_session_id="sess_abc",
            _decision_envelope=env,
            _last_utterance_class=env.utterance_class,
            last_observations=[],
        )

    def test_successful_turn_sampled(self):
        with tempfile.TemporaryDirectory() as tmp:
            store = ExperienceLogStore(Path(tmp) / "log.json")
            begin_turn_tool_buffer()
            note_tool_event(
                tool_name="set_volume", status="ok", success=True, error_code=""
            )
            with patch(
                "core.self_improve.runtime_collector._runtime_logging_config",
                return_value=_cfg(success_sample_rate=1.0),
            ):
                entry = record_handle_result(
                    self._manager(),
                    SimpleNamespace(source="fast_path", data={}),
                    started_monotonic=0.0,
                    store=store,
                )
            self.assertIsNotNone(entry)
            self.assertIs(entry.category, FailureCategory.SUCCESS)
            self.assertEqual(entry.tool, "set_volume")
            self.assertEqual(entry.session_id, "sess_abc")
            self.assertIn("fingerprint", entry.metadata)

    def test_tool_failure_logged(self):
        with tempfile.TemporaryDirectory() as tmp:
            store = ExperienceLogStore(Path(tmp) / "log.json")
            begin_turn_tool_buffer()
            note_tool_event(
                tool_name="play_media",
                status="error",
                success=False,
                error_code="EXEC_FAILED",
            )
            with patch(
                "core.self_improve.runtime_collector._runtime_logging_config",
                return_value=_cfg(),
            ):
                entry = record_handle_result(
                    self._manager(),
                    SimpleNamespace(source="agent", data={}),
                    started_monotonic=0.0,
                    store=store,
                )
            self.assertIs(entry.category, FailureCategory.TOOL_ERROR)
            self.assertEqual(entry.metadata.get("error_code"), "EXEC_FAILED")

    def test_noise_sources_skipped(self):
        with tempfile.TemporaryDirectory() as tmp:
            store = ExperienceLogStore(Path(tmp) / "log.json")
            with patch(
                "core.self_improve.runtime_collector._runtime_logging_config",
                return_value=_cfg(),
            ):
                for source in ("wake", "ack", "wait", "ambient_bleed", "noise_unknown"):
                    entry = record_handle_result(
                        self._manager(),
                        SimpleNamespace(source=source, data={}),
                        started_monotonic=0.0,
                        store=store,
                    )
                    self.assertIsNone(entry, source)
            self.assertEqual(store.load(), [])

    def test_growth_control(self):
        with tempfile.TemporaryDirectory() as tmp:
            store = ExperienceLogStore(Path(tmp) / "log.json")
            with patch(
                "core.self_improve.runtime_collector._runtime_logging_config",
                return_value=_cfg(max_entries=5, success_sample_rate=1.0),
            ):
                for i in range(8):
                    begin_turn_tool_buffer()
                    note_tool_event(
                        tool_name=f"tool_{i}",
                        status="error",
                        success=False,
                        error_code="EXEC_FAILED",
                    )
                    record_handle_result(
                        self._manager(intent=f"intent_{i}"),
                        SimpleNamespace(source="agent", data={}),
                        started_monotonic=0.0,
                        store=store,
                    )
            loaded = store.load()
            self.assertLessEqual(len(loaded), 5)

    def test_disabled_config(self):
        with tempfile.TemporaryDirectory() as tmp:
            store = ExperienceLogStore(Path(tmp) / "log.json")
            begin_turn_tool_buffer()
            note_tool_event(
                tool_name="open_app",
                status="error",
                success=False,
                error_code="EXEC_FAILED",
            )
            with patch(
                "core.self_improve.runtime_collector._runtime_logging_config",
                return_value=_cfg(enabled=False),
            ):
                entry = record_handle_result(
                    self._manager(),
                    SimpleNamespace(source="agent", data={}),
                    started_monotonic=0.0,
                    store=store,
                )
            self.assertIsNone(entry)
            self.assertEqual(store.load(), [])

    def test_fingerprint_deterministic(self):
        a = fingerprint_for(
            intent="open_app",
            tool="open_app",
            category="TOOL_ERROR",
            error_code="EXEC_FAILED",
        )
        b = fingerprint_for(
            intent="open_app",
            tool="open_app",
            category="TOOL_ERROR",
            error_code="EXEC_FAILED",
        )
        self.assertEqual(a, b)
        self.assertEqual(len(a), 16)

    def test_fingerprint_includes_normalized_error_code(self):
        failure = fingerprint_for(
            intent="find_contact",
            tool="find_contact",
            category="TOOL_ERROR",
            error_code="FAILURE",
        )
        rate = fingerprint_for(
            intent="find_contact",
            tool="find_contact",
            category="TOOL_ERROR",
            error_code="RATE_LIMIT",
        )
        padded = fingerprint_for(
            intent="find_contact",
            tool="find_contact",
            category="TOOL_ERROR",
            error_code=" FAILURE ",
        )
        missing = fingerprint_for(
            intent="find_contact",
            tool="find_contact",
            category="TOOL_ERROR",
            error_code="",
        )
        none_like = fingerprint_for(
            intent="find_contact",
            tool="find_contact",
            category="TOOL_ERROR",
            error_code=None,  # type: ignore[arg-type]
        )
        self.assertNotEqual(failure, rate)
        self.assertEqual(failure, padded)
        self.assertEqual(missing, none_like)
        self.assertNotEqual(failure, missing)
        self.assertEqual(normalize_fingerprint_error_code(" FAILURE "), "FAILURE")
        # Category still separates identical error_code strings.
        tool_err_timeout = fingerprint_for(
            intent="open_app",
            tool="open_app",
            category="TOOL_ERROR",
            error_code="TIMEOUT",
        )
        timeout_cat = fingerprint_for(
            intent="open_app",
            tool="open_app",
            category="TIMEOUT",
            error_code="TIMEOUT",
        )
        self.assertNotEqual(tool_err_timeout, timeout_cat)


class FailOpenAndSessionTests(unittest.TestCase):
    def test_handle_survives_collector_exception(self):
        from core.conversation_manager import ConversationManager, HandleResult

        manager = ConversationManager.__new__(ConversationManager)
        expected = HandleResult.speak("działa", source="chat")
        with patch.object(ConversationManager, "_handle_turn", return_value=expected):
            with patch(
                "core.self_improve.runtime_collector.record_handle_result",
                side_effect=Exception("telemetry down"),
            ):
                out = ConversationManager.handle(manager, "cześć")
        self.assertEqual(out.text, "działa")
        self.assertEqual(out.source, "chat")

    def test_handle_survives_store_save_failure(self):
        from core.conversation_manager import ConversationManager, HandleResult

        manager = ConversationManager.__new__(ConversationManager)
        manager._experience_session_id = "sess_x"
        manager._decision_envelope = SimpleNamespace(
            intent="open_app",
            utterance_class="GOAL",
            confidence=0.8,
            allowed_domains=["APP"],
        )
        manager._last_utterance_class = "GOAL"
        manager.last_observations = []
        expected = HandleResult.speak("ok", source="agent")

        with patch.object(ConversationManager, "_handle_turn", return_value=expected):
            with patch(
                "core.self_improve.runtime_collector._runtime_logging_config",
                return_value=_cfg(success_sample_rate=1.0),
            ):
                with patch(
                    "core.self_improve.experience_log.ExperienceLogStore.load",
                    return_value=[],
                ):
                    with patch(
                        "core.self_improve.experience_log.ExperienceLogStore.save",
                        side_effect=OSError("disk full"),
                    ):
                        begin_turn_tool_buffer()
                        note_tool_event(
                            tool_name="open_app",
                            status="ok",
                            success=True,
                            error_code="",
                        )
                        out = ConversationManager.handle(manager, "x")
        self.assertEqual(out.source, "agent")
        self.assertEqual(out.text, "ok")

    def test_session_id_activate_deactivate(self):
        from core.conversation_manager import ConversationManager

        manager = ConversationManager.__new__(ConversationManager)
        manager.active = False
        manager.pending_tool = None
        manager.pending_clarification = None
        manager.pending_executable_goal = None
        manager._retry_failed_only = False
        manager.clarification_rounds = 0
        manager._pending_slot_classification = None
        manager.session_language = "pl"
        manager._language_locked = False
        manager._session_briefing_done = False
        manager._experience_session_id = ""
        manager._last_utterance_class = None
        manager._user_goal = None
        manager._goal_context = None
        manager._cognitive_assessment = None
        manager._cognitive_guidance = None
        manager.mem = MagicMock()
        manager.cognitive = MagicMock()
        manager.collab = MagicMock()
        manager.goals = MagicMock()

        with patch("core.conversation_manager.clear_pending_action"), patch(
            "core.conversation_manager.clear_context"
        ), patch("core.conversation_manager.log_turn"), patch.object(
            ConversationManager, "_restore_pending_from_ttl"
        ):
            ConversationManager.activate(manager)
            sid1 = manager._experience_session_id
            self.assertTrue(str(sid1).startswith("sess_"))
            ConversationManager.activate(manager)
            sid2 = manager._experience_session_id
            self.assertTrue(str(sid2).startswith("sess_"))
            self.assertNotEqual(sid1, sid2)
            stable = manager._experience_session_id
            self.assertEqual(manager._experience_session_id, stable)
            ConversationManager.deactivate(manager, reason="test")
            self.assertEqual(manager._experience_session_id, "")
            ConversationManager.activate(manager)
            self.assertTrue(str(manager._experience_session_id).startswith("sess_"))
            self.assertNotEqual(manager._experience_session_id, sid2)


class CompatibilityTests(unittest.TestCase):
    def test_runtime_entry_compatible_with_reflection(self):
        from core.self_improve.reflection import ReflectionEngine

        with tempfile.TemporaryDirectory() as tmp:
            store = ExperienceLogStore(Path(tmp) / "log.json")
            for i in range(4):
                store.append(
                    ExperienceEntry(
                        domain="scheduling",
                        category=FailureCategory.NO_TOOL_MATCH,
                        summary=f"agent:NO_TOOL_MATCH:missing_{i}",
                        metadata={"source": "agent", "fingerprint": f"fp{i}"},
                        provenance=PROVENANCE_RUNTIME,
                    )
                )
            # Mirror a runtime-shaped success
            store.append(
                ExperienceEntry(
                    domain="scheduling",
                    category=FailureCategory.SUCCESS,
                    summary="fast_path:SUCCESS:set_volume",
                    tool="set_volume",
                    metadata={"source": "fast_path", "fingerprint": "ok1"},
                    provenance=PROVENANCE_RUNTIME,
                )
            )
            report = ReflectionEngine(store=store).reflect()
            self.assertTrue(report.domains)
            scheduling = next(d for d in report.domains if d.domain == "scheduling")
            self.assertGreaterEqual(scheduling.failures, 3)

    def test_collector_facade(self):
        with tempfile.TemporaryDirectory() as tmp:
            store = ExperienceLogStore(Path(tmp) / "log.json")
            collector = RuntimeExperienceCollector(store=store)
            manager = SimpleNamespace(
                _experience_session_id="sess_f",
                _decision_envelope=SimpleNamespace(
                    intent="x",
                    utterance_class="CORRECTION",
                    confidence=0.5,
                    allowed_domains=[],
                ),
                _last_utterance_class="CORRECTION",
                last_observations=[],
            )
            with patch(
                "core.self_improve.runtime_collector._runtime_logging_config",
                return_value=_cfg(),
            ):
                entry = collector.record_turn(
                    manager,
                    SimpleNamespace(source="correction", data={}),
                    started_monotonic=0.0,
                )
            self.assertIs(entry.category, FailureCategory.USER_CORRECTION)

    def test_tool_registry_buffer_hook(self):
        from core.tools import Tool, ToolRegistry

        begin_turn_tool_buffer()
        registry = ToolRegistry()

        def boom(_args):
            return ToolResult.fail(
                "x",
                tool_name="t_fail",
                error="boom",
                error_code="EXEC_FAILED",
            )

        registry.register(
            Tool(
                name="t_fail",
                description="test",
                parameters={},
                handler=boom,
                available=True,
            )
        )
        result = registry.invoke("t_fail", {}, force=True, skip_validation=True)
        self.assertFalse(result.success)
        events = take_turn_tool_events()
        self.assertTrue(any(e.get("tool") == "t_fail" for e in events))
        clear_turn_tool_buffer()


if __name__ == "__main__":
    unittest.main()
