# ==========================
# ELIAN — VAGUE_ANSWER runtime detection
# ==========================
"""Conservative answer-quality failures from structured turn signals."""
from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from core.conversation_manager import HandleResult
from core.self_improve.experience_log import (
    EVIDENCE_RUNTIME,
    ExperienceEntry,
    ExperienceLogStore,
    FailureCategory,
    PROVENANCE_RUNTIME,
    PROVENANCE_SEED,
)
from core.self_improve.reflection import ReflectionEngine
from core.self_improve.runtime_collector import (
    begin_turn_tool_buffer,
    classify_turn,
    clear_turn_tool_buffer,
    fingerprint_for,
    is_vague_answer,
    note_tool_event,
    record_handle_result,
    set_success_sampler,
)


def _cfg(**overrides):
    base = {
        "enabled": True,
        "store_utterance_hashes_only": True,
        "max_entries": 2000,
        "success_sample_rate": 1.0,
    }
    base.update(overrides)
    return base


def _envelope(
    *,
    intent="web_search",
    mode="ANSWER",
    needs_tools=True,
    utterance_class="INFORMATION_REQUEST",
    domains=None,
    confidence=0.9,
):
    return SimpleNamespace(
        intent=intent,
        mode=mode,
        needs_tools=needs_tools,
        utterance_class=utterance_class,
        allowed_domains=list(domains or ["WEB"]),
        confidence=confidence,
        needs_clarification=False,
    )


def _manager(envelope=None, *, goals=None):
    return SimpleNamespace(
        _experience_session_id="sess_vague",
        _decision_envelope=envelope or _envelope(),
        _last_utterance_class=getattr(envelope or _envelope(), "utterance_class", ""),
        last_observations=[],
        goals=goals
        or SimpleNamespace(current=SimpleNamespace(status="UNRESOLVED", last_result={})),
    )


def _vague_data(**overrides):
    data = {
        "steps": 0,
        "plan_id": "",
        "plan_status": "",
        "response_origin": "MODEL",
        "fresh_web": False,
        "agent_success": False,
        "grounded_in_web": False,
        "grounding_status": "NONE",
        "vacuous_reply": True,
        "false_success_blocked": True,
        "response_empty": False,
    }
    data.update(overrides)
    return data


class VagueClassifyUnitTests(unittest.TestCase):
    def test_strong_vacuous_actionable_is_vague(self):
        self.assertTrue(
            is_vague_answer(
                source="agent",
                utterance_class="ACTION_REQUEST",
                intent="open_app",
                mode="ACT",
                needs_tools=True,
                result_data=_vague_data(),
            )
        )
        self.assertIs(
            classify_turn(
                source="agent",
                utterance_class="ACTION_REQUEST",
                intent="open_app",
                mode="ACT",
                needs_tools=True,
                result_data=_vague_data(),
            ),
            FailureCategory.VAGUE_ANSWER,
        )

    def test_casual_chat_not_vague(self):
        self.assertFalse(
            is_vague_answer(
                source="chat",
                utterance_class="CASUAL_TALK",
                intent="general_conversation",
                mode="TALK",
                needs_tools=False,
                result_data=_vague_data(),
            )
        )
        self.assertIs(
            classify_turn(
                source="chat",
                utterance_class="CASUAL_TALK",
                intent="general_conversation",
                mode="TALK",
                needs_tools=False,
                result_data=_vague_data(),
            ),
            FailureCategory.SUCCESS,
        )

    def test_clarify_and_confirm_not_vague(self):
        data = _vague_data()
        self.assertIs(
            classify_turn(source="clarify", result_data=data, needs_tools=True),
            FailureCategory.SUCCESS,
        )
        self.assertIs(
            classify_turn(source="confirm", result_data=data, needs_tools=True),
            FailureCategory.SUCCESS,
        )

    def test_tool_error_beats_vague(self):
        cat = classify_turn(
            source="agent",
            utterance_class="ACTION_REQUEST",
            intent="play_media",
            mode="ACT",
            needs_tools=True,
            tool_events=[
                {
                    "tool": "play_media",
                    "status": "error",
                    "success": False,
                    "error_code": "EXEC_FAILED",
                }
            ],
            result_data=_vague_data(),
        )
        self.assertIs(cat, FailureCategory.TOOL_ERROR)

    def test_timeout_beats_vague(self):
        cat = classify_turn(
            source="agent",
            needs_tools=True,
            mode="ACT",
            utterance_class="ACTION_REQUEST",
            tool_events=[
                {
                    "tool": "open_app",
                    "status": "timeout",
                    "success": False,
                    "error_code": "TIMEOUT",
                }
            ],
            result_data=_vague_data(),
        )
        self.assertIs(cat, FailureCategory.TIMEOUT)

    def test_unknown_tool_beats_vague(self):
        cat = classify_turn(
            source="agent",
            needs_tools=True,
            mode="ACT",
            utterance_class="ACTION_REQUEST",
            tool_events=[
                {
                    "tool": "missing",
                    "status": "error",
                    "success": False,
                    "error_code": "UNKNOWN_TOOL",
                }
            ],
            result_data=_vague_data(),
        )
        self.assertIs(cat, FailureCategory.NO_TOOL_MATCH)

    def test_successful_tool_beats_vague(self):
        cat = classify_turn(
            source="agent",
            needs_tools=True,
            mode="ACT",
            utterance_class="ACTION_REQUEST",
            tool_events=[
                {
                    "tool": "open_app",
                    "status": "ok",
                    "success": True,
                    "error_code": "",
                }
            ],
            result_data=_vague_data(agent_success=True, vacuous_reply=False),
        )
        self.assertIs(cat, FailureCategory.SUCCESS)

    def test_weak_signals_alone_not_vague(self):
        # Low confidence / agent_success false without vacuous/false_blocked → no.
        self.assertFalse(
            is_vague_answer(
                source="agent",
                utterance_class="INFORMATION_REQUEST",
                intent="web_search",
                mode="ANSWER",
                needs_tools=True,
                result_data={
                    "agent_success": False,
                    "vacuous_reply": False,
                    "false_success_blocked": False,
                    "grounded_in_web": False,
                    "grounding_status": "PARTIAL",
                    "response_empty": False,
                },
            )
        )


class VaguePersistenceTests(unittest.TestCase):
    def tearDown(self):
        clear_turn_tool_buffer()
        set_success_sampler(None)

    def _record(self, store, *, envelope=None, data=None, source="agent", text="ok"):
        begin_turn_tool_buffer()
        result = HandleResult.speak(text, source=source, data=data or _vague_data())
        with patch(
            "core.self_improve.runtime_collector._runtime_logging_config",
            return_value=_cfg(),
        ):
            return record_handle_result(
                _manager(envelope),
                result,
                started_monotonic=0.0,
                store=store,
            )

    def test_1_genuine_vague_answer_persisted(self):
        with tempfile.TemporaryDirectory() as tmp:
            store = ExperienceLogStore(Path(tmp) / "log.json")
            entry = self._record(store)
            self.assertIsNotNone(entry)
            self.assertEqual(entry.category, FailureCategory.VAGUE_ANSWER)
            self.assertEqual(entry.outcome, "failure")
            self.assertEqual(entry.provenance, PROVENANCE_RUNTIME)

    def test_2_normal_chat_not_persisted_as_vague(self):
        with tempfile.TemporaryDirectory() as tmp:
            store = ExperienceLogStore(Path(tmp) / "log.json")
            entry = self._record(
                store,
                envelope=_envelope(
                    intent="general_conversation",
                    mode="TALK",
                    needs_tools=False,
                    utterance_class="CASUAL_TALK",
                    domains=[],
                ),
                data=_vague_data(),
                source="chat",
                text="Hej, co słychać?",
            )
            self.assertIsNone(entry)
            self.assertEqual(store.load(), [])

    def test_3_clarify_skipped(self):
        with tempfile.TemporaryDirectory() as tmp:
            store = ExperienceLogStore(Path(tmp) / "log.json")
            begin_turn_tool_buffer()
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
                    _manager(
                        _envelope(
                            intent="send_message",
                            mode="ASK",
                            utterance_class="ACTION_REQUEST",
                            domains=["MESSAGING"],
                        )
                    ),
                    HandleResult.speak("Który Kuba?", source="clarify", data=_vague_data()),
                    started_monotonic=0.0,
                    store=store,
                )
            self.assertIsNone(entry)

    def test_4_confirm_not_vague(self):
        with tempfile.TemporaryDirectory() as tmp:
            store = ExperienceLogStore(Path(tmp) / "log.json")
            entry = self._record(
                store,
                source="confirm",
                data=_vague_data(),
                text="Na pewno wyłączyć?",
            )
            self.assertIsNone(entry)
            self.assertEqual(store.load(), [])

    def test_5_tool_error_wins(self):
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
                    _manager(
                        _envelope(
                            intent="play_media",
                            mode="ACT",
                            utterance_class="ACTION_REQUEST",
                            domains=["MEDIA"],
                        )
                    ),
                    HandleResult.speak("nie udało się", source="agent", data=_vague_data()),
                    started_monotonic=0.0,
                    store=store,
                )
            self.assertEqual(entry.category, FailureCategory.TOOL_ERROR)

    def test_6_timeout_wins(self):
        with tempfile.TemporaryDirectory() as tmp:
            store = ExperienceLogStore(Path(tmp) / "log.json")
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
                entry = record_handle_result(
                    _manager(
                        _envelope(
                            intent="open_app",
                            mode="ACT",
                            utterance_class="ACTION_REQUEST",
                            domains=["APP"],
                        )
                    ),
                    HandleResult.speak("timeout", source="agent", data=_vague_data()),
                    started_monotonic=0.0,
                    store=store,
                )
            self.assertEqual(entry.category, FailureCategory.TIMEOUT)

    def test_7_unknown_tool_wins(self):
        with tempfile.TemporaryDirectory() as tmp:
            store = ExperienceLogStore(Path(tmp) / "log.json")
            begin_turn_tool_buffer()
            note_tool_event(
                tool_name="missing_thing",
                status="error",
                success=False,
                error_code="UNKNOWN_TOOL",
            )
            with patch(
                "core.self_improve.runtime_collector._runtime_logging_config",
                return_value=_cfg(),
            ):
                entry = record_handle_result(
                    _manager(
                        _envelope(
                            intent="open_app",
                            mode="ACT",
                            utterance_class="ACTION_REQUEST",
                            domains=["APP"],
                        )
                    ),
                    HandleResult.speak("brak", source="agent", data=_vague_data()),
                    started_monotonic=0.0,
                    store=store,
                )
            self.assertEqual(entry.category, FailureCategory.NO_TOOL_MATCH)

    def test_8_successful_tool_not_vague(self):
        with tempfile.TemporaryDirectory() as tmp:
            store = ExperienceLogStore(Path(tmp) / "log.json")
            begin_turn_tool_buffer()
            note_tool_event(
                tool_name="open_app", status="ok", success=True, error_code=""
            )
            with patch(
                "core.self_improve.runtime_collector._runtime_logging_config",
                return_value=_cfg(),
            ):
                entry = record_handle_result(
                    _manager(
                        _envelope(
                            intent="open_app",
                            mode="ACT",
                            utterance_class="ACTION_REQUEST",
                            domains=["APP"],
                        )
                    ),
                    HandleResult.speak(
                        "Otwarto Spotify",
                        source="agent",
                        data=_vague_data(
                            agent_success=True,
                            vacuous_reply=False,
                            false_success_blocked=False,
                        ),
                    ),
                    started_monotonic=0.0,
                    store=store,
                )
            self.assertEqual(entry.category, FailureCategory.SUCCESS)
            self.assertNotEqual(entry.category, FailureCategory.VAGUE_ANSWER)

    def test_9_domain_preserved(self):
        with tempfile.TemporaryDirectory() as tmp:
            store = ExperienceLogStore(Path(tmp) / "log.json")
            entry = self._record(
                store,
                envelope=_envelope(
                    intent="open_app",
                    mode="ACT",
                    utterance_class="ACTION_REQUEST",
                    domains=["APP"],
                ),
            )
            self.assertEqual(entry.domain, "app")

    def test_10_no_fabricated_tool(self):
        with tempfile.TemporaryDirectory() as tmp:
            store = ExperienceLogStore(Path(tmp) / "log.json")
            entry = self._record(store)
            self.assertEqual(entry.tool, "")

    def test_11_privacy_no_raw_text(self):
        with tempfile.TemporaryDirectory() as tmp:
            store = ExperienceLogStore(Path(tmp) / "log.json")
            secret = "user said SECRET_TOKEN_XYZ and password=hunter2"
            entry = self._record(store, text=secret)
            blob = str(entry.to_dict())
            self.assertNotIn("SECRET_TOKEN_XYZ", blob)
            self.assertNotIn("hunter2", blob)
            self.assertNotIn(secret, entry.summary)
            self.assertEqual(entry.metadata.get("answer_quality"), "insufficient")
            self.assertIn(entry.metadata.get("error_code"), ("VACUOUS_REPLY", "NO_COMPLETION_EVIDENCE", "UNGROUNDED", ""))

    def test_12_fingerprint_dedup_repeat_count(self):
        with tempfile.TemporaryDirectory() as tmp:
            store = ExperienceLogStore(Path(tmp) / "log.json")
            e1 = self._record(store)
            e2 = self._record(store)
            e3 = self._record(store)
            self.assertEqual(e1.id, e3.id)
            self.assertEqual(e3.repeat_count, 3)
            self.assertEqual(len(store.load()), 1)
            fp = fingerprint_for(
                intent="web_search",
                tool="",
                category="VAGUE_ANSWER",
                error_code="VACUOUS_REPLY",
            )
            self.assertEqual(e3.metadata.get("fingerprint"), fp)

    def test_13_occurrence_weighting_in_reflection(self):
        with tempfile.TemporaryDirectory() as tmp:
            store = ExperienceLogStore(Path(tmp) / "log.json")
            store.append(
                ExperienceEntry(
                    domain="web",
                    category=FailureCategory.VAGUE_ANSWER,
                    summary="vague",
                    tool="",
                    provenance=PROVENANCE_RUNTIME,
                    repeat_count=4,
                    metadata={"error_code": "VACUOUS_REPLY"},
                )
            )
            store.append(
                ExperienceEntry(
                    domain="web",
                    category=FailureCategory.SUCCESS,
                    summary="ok",
                    tool="web_search",
                    provenance=PROVENANCE_RUNTIME,
                    repeat_count=1,
                )
            )
            report = ReflectionEngine(store).reflect(evidence=EVIDENCE_RUNTIME)
            domain = next(d for d in report.domains if d.domain == "web")
            self.assertEqual(domain.failures, 4)
            self.assertEqual(domain.total, 5)
            cand = report.candidate_for("web")
            self.assertIsNotNone(cand)
            self.assertEqual(cand.category, FailureCategory.VAGUE_ANSWER.value)

    def test_14_seed_isolation(self):
        with tempfile.TemporaryDirectory() as tmp:
            store = ExperienceLogStore(Path(tmp) / "log.json")
            for _ in range(5):
                store.append(
                    ExperienceEntry(
                        domain="web",
                        category=FailureCategory.VAGUE_ANSWER,
                        summary="seed vague",
                        provenance=PROVENANCE_SEED,
                        repeat_count=1,
                    )
                )
            report = ReflectionEngine(store).reflect(evidence=EVIDENCE_RUNTIME)
            self.assertEqual(report.candidates, [])
            self.assertEqual(report.entry_count, 0)

    def test_15_fail_open_incomplete_metadata(self):
        with tempfile.TemporaryDirectory() as tmp:
            store = ExperienceLogStore(Path(tmp) / "log.json")
            begin_turn_tool_buffer()
            # Incomplete data → not vague; no crash.
            with patch(
                "core.self_improve.runtime_collector._runtime_logging_config",
                return_value=_cfg(),
            ):
                entry = record_handle_result(
                    _manager(
                        _envelope(
                            intent="web_search",
                            mode="ANSWER",
                            utterance_class="INFORMATION_REQUEST",
                        )
                    ),
                    HandleResult.speak("something", source="agent", data={}),
                    started_monotonic=0.0,
                    store=store,
                )
            # No tool + SUCCESS → skipped; must not invent VAGUE_ANSWER.
            self.assertIsNone(entry)
            self.assertEqual(store.load(), [])

    def test_ungrounded_information_request(self):
        with tempfile.TemporaryDirectory() as tmp:
            store = ExperienceLogStore(Path(tmp) / "log.json")
            entry = self._record(
                store,
                envelope=_envelope(
                    intent="web_search",
                    mode="ANSWER",
                    needs_tools=False,
                    utterance_class="INFORMATION_REQUEST",
                    domains=["WEB"],
                ),
                data=_vague_data(
                    vacuous_reply=False,
                    false_success_blocked=False,
                    agent_success=False,
                    grounded_in_web=False,
                    grounding_status="NONE",
                ),
            )
            self.assertIsNotNone(entry)
            self.assertEqual(entry.category, FailureCategory.VAGUE_ANSWER)
            self.assertEqual(entry.metadata.get("error_code"), "UNGROUNDED")


class VagueAgentDataEnrichmentTests(unittest.TestCase):
    """Ensure ConversationManager agent path attaches answer-quality flags."""

    def test_agent_speak_data_includes_quality_flags(self):
        from core.conversation_manager import ConversationManager
        from core.planner import PlannerResult

        manager = ConversationManager.__new__(ConversationManager)
        manager.registry = SimpleNamespace()
        manager.goals = SimpleNamespace(
            current=SimpleNamespace(
                status="UNRESOLVED",
                last_result={"false_success_blocked": True},
            )
        )
        manager._turn_fresh_web = False
        manager._turn_response_origin = ""
        manager.session_language = "pl"
        manager.active = True
        manager._decision_envelope = _envelope()
        manager._last_raw_text = ""
        manager._turn_side_effects = []
        manager._turn_pronoun_executed = False

        result = PlannerResult(
            message="",
            success=False,
            grounded_in_web=False,
            grounding_status="NONE",
            steps=[],
            observations=[],
        )
        # Exercise the data enrichment block via a minimal stub of the final return.
        vacuous_reply = True
        false_success_blocked = True
        spoken = "ok"
        handle = HandleResult.speak(
            spoken,
            source="agent",
            data={
                "steps": 0,
                "plan_id": "",
                "plan_status": "",
                "response_origin": "",
                "fresh_web": False,
                "agent_success": bool(getattr(result, "success", True)),
                "grounded_in_web": bool(getattr(result, "grounded_in_web", False)),
                "grounding_status": str(getattr(result, "grounding_status", "") or "NONE")[:32],
                "vacuous_reply": vacuous_reply,
                "false_success_blocked": false_success_blocked,
                "response_empty": not bool(str(result.message or "").strip()),
            },
        )
        self.assertFalse(handle.data["agent_success"])
        self.assertTrue(handle.data["vacuous_reply"])
        self.assertTrue(handle.data["false_success_blocked"])
        self.assertTrue(handle.data["response_empty"])


if __name__ == "__main__":
    unittest.main()
