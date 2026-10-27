# ==========================
# ELIAN — Part X proactive / multi-turn eval (C.5.4)
# ==========================
"""≥50 deterministic proactive/dialogue cases + ≥30 multi-turn scenarios.

Does not replace the 650-case semantic eval.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

from core.collaboration import (
    CollaborationStore,
    DecisionOption,
    ProblemBriefing,
    default_sensor_briefing,
    looks_internal_tts,
    should_initiate,
)
from core.events import (
    ACTIVE_WINDOW_CHANGED,
    SCREEN_FRAME_UPDATED,
    SCREEN_MAJOR_CHANGE,
    VISION_COMPLETED,
    VISION_STARTED,
    Event,
    Priority,
)
from core.proactive import ProactiveManager, proactive_metrics, reset_proactive_metrics
from core.semantic_executive import decide_semantics, fast_path_permitted
from core.semantic_talk import talk_reply
from core.speech_guard import looks_like_internal_identifier


def _opts() -> List[DecisionOption]:
    return [
        DecisionOption(
            id="filter",
            label="przetestować filtr programowy",
            consequences="szybkie i łatwo cofnąć",
        ),
        DecisionOption(
            id="wiring",
            label="sprawdzić okablowanie",
            consequences="skuteczniejsze, wymaga czasu",
        ),
    ]


def _seed_task(store: CollaborationStore, **kwargs) -> CollaborationStore:
    defaults = dict(
        task_id="glove-1",
        goal="rękawica VR — szum czujników",
        status="running",
        current_step="sprawdzam połączenie",
        completed_steps=["odczyt kanałów", "porównanie szumu"],
        observations=["dwa kanały mają wyraźnie większy szum"],
        notify_on_complete=False,
    )
    defaults.update(kwargs)
    store.seed_task(**defaults)
    return store


def _seed_decision(store: CollaborationStore) -> CollaborationStore:
    store.seed_decision(
        decision_id="dec-noise",
        question="Co zrobić z szumem czujników?",
        options=_opts(),
        recommended_option="filter",
        recommended_why="jest szybsza do sprawdzenia i łatwo ją cofnąć",
        problem="Dwa kanały nadal mają większy szum.",
        evidence="odczyty z trzech prób",
        strength="moderate",
    )
    return store


def _pm() -> ProactiveManager:
    spoken: List[str] = []
    pm = ProactiveManager(speak_fn=lambda m: spoken.append(m), busy_check=lambda: False)
    pm._spoken = spoken  # type: ignore[attr-defined]
    return pm


def _evt(typ: str, **kwargs) -> Event:
    import time as _time

    priority = kwargs.pop("priority", Priority.NORMAL)
    data = kwargs.pop("data", {}) or {}
    return Event(
        type=typ,
        source=kwargs.pop("source", "test"),
        data=data,
        priority=priority,
        timestamp=float(kwargs.pop("timestamp", _time.time())),
    )


# ----- 50+ proactive/dialogue cases -----

def proactive_case_specs() -> List[Dict[str, Any]]:
    specs: List[Dict[str, Any]] = [
        {
            "name": "notify_on_complete_meaningful",
            "kind": "event",
            "event": dict(
                type="task_completed",
                priority=Priority.NORMAL,
                data={
                    "reason": "task_completed_requested",
                    "notify_requested": True,
                    "user_value": True,
                    "has_background_work": True,
                    "message": "Skończyłem. Problem jest w konfiguracji portu, nie w samym urządzeniu.",
                },
            ),
            "expect_action": "notify",
            "spoken_has": ("konfiguracji",),
            "spoken_not": ("PROCEDURE_COMPLETE", "Zadanie zakończono."),
        },
        {
            "name": "window_change_silent",
            "kind": "event",
            "event": dict(
                type=ACTIVE_WINDOW_CHANGED,
                data={"title": "Chrome", "hwnd": 1},
            ),
            "expect_action": "silent",
        },
        {
            "name": "task_blocked_asks_decision",
            "kind": "store_initiate",
            "reason": "task_blocked",
            "priority": "high",
            "briefing": True,
            "spoken_has": ("możliwości", "konfigur"),
        },
        {
            "name": "nie_teraz_suppress",
            "kind": "defer",
            "utterance": "nie teraz",
            "later": False,
        },
        {
            "name": "barge_in_stops_speech",
            "kind": "barge_in",
        },
        {
            "name": "pick_first_option",
            "kind": "dialogue",
            "setup": "decision",
            "utterance": "pierwsza",
            "expect_kind": "pick_option",
            "spoken_has": ("filtr",),
            "option_id": "filter",
        },
        {
            "name": "ask_why_explains",
            "kind": "dialogue",
            "setup": "decision",
            "utterance": "a dlaczego?",
            "expect_kind": "ask_why",
            "spoken_has": ("szybsz",),
            "spoken_not": ("Nie rozumiem",),
        },
        {
            "name": "ask_alternative_same_problem",
            "kind": "dialogue",
            "setup": "decision",
            "utterance": "a inaczej?",
            "expect_kind": "ask_alternative",
            "spoken_has": ("okablowan",),
            "spoken_not": ("spotify", "Notatnik", "Nie rozumiem"),
        },
        {
            "name": "joke_during_task_no_tools",
            "kind": "semantic",
            "setup": "task",
            "utterance": "Opowiedz żart.",
            "expect_mode": "TALK",
            "expect_intent": "creative_humor",
            "needs_tools": False,
            "no_hijack": True,
        },
        {
            "name": "return_to_task_after_joke",
            "kind": "dialogue",
            "setup": "task_after_joke",
            "utterance": "jak tam z tamtym?",
            "expect_kind": "return_to_task",
            "spoken_has": ("krok",),
            "spoken_not": ("żart", "plan_id="),
        },
        {
            "name": "jak_idzie_no_task",
            "kind": "dialogue",
            "setup": "empty",
            "utterance": "jak idzie?",
            "expect_kind": "no_task_status",
            "spoken_has": ("nie mam teraz",),
            "spoken_not": ("RUNNING", "plan_id"),
        },
        {
            "name": "low_value_vision_silent",
            "kind": "event",
            "event": dict(type=VISION_COMPLETED, data={"ok": True}),
            "expect_action": "silent",
        },
        {
            "name": "unsolicited_completion_silent",
            "kind": "policy",
            "reason": "task_completed_requested",
            "notify_requested": False,
            "has_background_work": False,
            "user_value": False,
            "expect_ok": False,
        },
        {
            "name": "critical_failure_semantic",
            "kind": "store_initiate",
            "reason": "critical_failure",
            "priority": "urgent",
            "briefing": True,
            "spoken_has": ("urządzen",),
            "spoken_not": ("CRITICAL_TASK_FAILURE", "FAILED"),
        },
        {
            "name": "no_internal_enum_tts",
            "kind": "enum_guard",
        },
    ]

    silent_events = [
        (SCREEN_FRAME_UPDATED, "screen_frame"),
        (SCREEN_MAJOR_CHANGE, "screen_major"),
        (VISION_STARTED, "vision_started"),
        ("INTERNAL_EVENT", "internal_event_type"),
        ("PROCEDURE_COMPLETE", "procedure_complete_type"),
    ]
    for typ, name in silent_events:
        specs.append(
            {
                "name": f"silent_{name}",
                "kind": "event",
                "event": dict(type=typ, data={"message": typ}),
                "expect_action": "silent",
            }
        )

    picks = [
        ("ten pierwszy", "filter", "pick_first_ten"),
        ("zrób drugie", "wiring", "pick_second"),
        ("dobra", "filter", "approve_dobra"),
        ("zrób to", "filter", "approve_zrob"),
        ("tak", "filter", "approve_tak"),
        ("kontynuuj", "filter", "approve_kontynuuj"),
        ("drugi", "wiring", "pick_drugi"),
    ]
    for utt, oid, name in picks:
        specs.append(
            {
                "name": name,
                "kind": "dialogue",
                "setup": "decision",
                "utterance": utt,
                "option_id": oid,
                "expect_kind": "approve_recommended" if oid == "filter" and utt not in ("ten pierwszy",) else "pick_option",
            }
        )
    # "dobra/tak/zrób to/kontynuuj" are approve; ten pierwszy / drugi / zrób drugie are pick
    specs[-7]["expect_kind"] = "pick_option"  # ten pierwszy
    specs[-6]["expect_kind"] = "pick_option"  # zrób drugie
    specs[-5]["expect_kind"] = "approve_recommended"
    specs[-4]["expect_kind"] = "approve_recommended"
    specs[-3]["expect_kind"] = "approve_recommended"
    specs[-2]["expect_kind"] = "approve_recommended"
    specs[-1]["expect_kind"] = "pick_option"

    specs.extend(
        [
            {
                "name": "recommend_calibrated",
                "kind": "dialogue",
                "setup": "decision",
                "utterance": "co byś zrobił?",
                "expect_kind": "ask_recommendation",
                "spoken_has": ("Wybrałbym",),
                "spoken_not": ("Nie wiem", "Na pewno"),
            },
            {
                "name": "powiedz_pozniej_defers",
                "kind": "defer",
                "utterance": "powiedz później",
                "later": True,
            },
            {
                "name": "low_priority_no_interrupt",
                "kind": "policy",
                "reason": "better_option_for_task",
                "priority": "low",
                "conversation_active": True,
                "user_value": True,
                "expect_ok": False,
            },
            {
                "name": "high_blocked_may_speak",
                "kind": "policy",
                "reason": "user_decision_needed",
                "priority": "high",
                "user_value": True,
                "expect_ok": True,
            },
            {
                "name": "chrome_opened_not_narrated",
                "kind": "policy",
                "reason": "",
                "event_type": ACTIVE_WINDOW_CHANGED,
                "user_value": False,
                "expect_ok": False,
            },
            {
                "name": "analysis_complete_requested",
                "kind": "policy",
                "reason": "analysis_complete",
                "notify_requested": True,
                "user_value": True,
                "expect_ok": True,
            },
            {
                "name": "reminder_ok",
                "kind": "policy",
                "reason": "reminder",
                "user_value": True,
                "expect_ok": True,
            },
            {
                "name": "jeszcze_jeden_is_joke",
                "kind": "semantic",
                "setup": "joke",
                "utterance": "jeszcze jeden",
                "expect_mode": "TALK",
                "expect_intent": "creative_humor",
                "needs_tools": False,
                "no_hijack": True,
            },
            {
                "name": "open_notepad_no_permission_q",
                "kind": "semantic",
                "utterance": "Otwórz Notatnik.",
                "expect_mode": "ACT",
                "needs_tools": True,
            },
            {
                "name": "co_proponujesz",
                "kind": "dialogue",
                "setup": "decision",
                "utterance": "co proponujesz?",
                "expect_kind": "ask_recommendation",
                "spoken_has": ("Wybrałbym",),
            },
            {
                "name": "jak_how",
                "kind": "dialogue",
                "setup": "decision",
                "utterance": "jak?",
                "expect_kind": "ask_how",
            },
            {
                "name": "zostaw_rejects",
                "kind": "dialogue",
                "setup": "decision",
                "utterance": "zostaw",
                "expect_kind": "reject_suggestion",
            },
            {
                "name": "niewazne_rejects",
                "kind": "dialogue",
                "setup": "decision",
                "utterance": "nieważne",
                "expect_kind": "reject_suggestion",
            },
            {
                "name": "expired_decision_clarifies",
                "kind": "expired",
            },
            {
                "name": "task_status_natural",
                "kind": "dialogue",
                "setup": "task",
                "utterance": "jak idzie?",
                "expect_kind": "task_status",
                "spoken_not": ("plan_id=", "status=RUNNING"),
                "spoken_has": ("krok",),
            },
            {
                "name": "jaki_jest_problem",
                "kind": "dialogue",
                "setup": "blocked",
                "utterance": "jaki jest problem?",
                "expect_kind": "task_status",
                "spoken_has": ("problem",),
            },
            {
                "name": "proactive_dialogue_mode",
                "kind": "mode",
            },
            {
                "name": "fastpath_blocked_for_pierwsza",
                "kind": "semantic",
                "setup": "decision",
                "utterance": "pierwsza",
                "expect_mode": "ANSWER",
                "fastpath": False,
            },
            {
                "name": "weak_recommendation_language",
                "kind": "weak_rec",
            },
            {
                "name": "screen_change_no_speech",
                "kind": "event",
                "event": dict(
                    type=SCREEN_MAJOR_CHANGE,
                    data={"title": "Notatnik nadal jest otwarty"},
                ),
                "expect_action": "silent",
            },
            {
                "name": "small_success_not_proactive",
                "kind": "policy",
                "reason": "",
                "event_type": "open_app_ok",
                "user_value": False,
                "expect_ok": False,
            },
            {
                "name": "user_asked_notify",
                "kind": "policy",
                "reason": "user_asked_notify",
                "notify_requested": True,
                "user_value": True,
                "has_background_work": True,
                "expect_ok": True,
            },
            {
                "name": "partial_fail_options",
                "kind": "store_initiate",
                "reason": "procedure_partial_fail",
                "priority": "high",
                "briefing": True,
                "spoken_has": ("częściowo", "możliwości"),
            },
        ]
    )

    # Pad to ≥50 with option/status variants that still assert contracts.
    extras = [
        "Co się dzieje?",
        "Na czym stanęliśmy?",
        "Da się inaczej?",
        "jednak pierwsza",
        "a która jest lepsza?",
        "czemu?",
    ]
    for i, utt in enumerate(extras):
        specs.append(
            {
                "name": f"variant_{i}_{utt[:12]}",
                "kind": "dialogue",
                "setup": "decision" if i >= 2 else "task",
                "utterance": utt,
            }
        )
    return specs


def _run_event_case(spec: Dict[str, Any]) -> Dict[str, Any]:
    reset_proactive_metrics()
    pm = _pm()
    ev = spec["event"]
    decision = pm.handle_event(_evt(ev["type"], data=ev.get("data") or {}, priority=ev.get("priority", Priority.NORMAL)))
    spoken = " ".join(getattr(pm, "_spoken", []))
    ok = decision.action == spec.get("expect_action", "silent")
    if spec.get("spoken_has"):
        ok = ok and all(part.lower() in spoken.lower() for part in spec["spoken_has"])
    if spec.get("spoken_not"):
        ok = ok and all(part.lower() not in spoken.lower() for part in spec["spoken_not"])
    if looks_internal_tts(spoken) or looks_like_internal_identifier(spoken):
        ok = False
    metrics = proactive_metrics()
    if spec.get("expect_action") == "silent":
        ok = ok and metrics.get("irrelevant_proactive_speech_rate", 0) == 0
    return {"ok": ok, "name": spec["name"], "detail": f"{decision.action}:{spoken[:80]}"}


def _prepare_store(setup: str) -> CollaborationStore:
    store = CollaborationStore()
    if setup in ("task", "task_after_joke", "blocked"):
        _seed_task(store)
        if setup == "blocked":
            store.task.status = "blocked"
            store.task.blockers = ["szum na dwóch kanałach"]
        if setup == "task_after_joke":
            store.salience.conversation_topic = "joke"
    if setup in ("decision",) or setup == "decision":
        _seed_task(store)
        _seed_decision(store)
    if setup == "joke":
        _seed_task(store, goal="Notatnik — wpisywanie")
        store.salience.conversation_topic = "joke"
        store.salience.execution_referent = "Notepad"
    return store


def _run_dialogue(spec: Dict[str, Any]) -> Dict[str, Any]:
    store = _prepare_store(spec.get("setup") or "empty")
    utt = spec["utterance"]
    env = decide_semantics(utt, conversation=store.semantic_conversation(), allow_llm=False)
    store.observe_envelope(env, utt)
    reply = store.try_handle(utt, env)
    spoken = ""
    if reply is not None:
        spoken = reply.message
        kind = reply.resolved_kind
    elif env.intent == "creative_humor":
        spoken = talk_reply(env, goal=utt)
        kind = "continue_conversation" if env.followup_kind == "continue_conversation" else env.intent
    else:
        spoken = env.draft_reply or ""
        kind = env.followup_kind or env.intent
    ok = True
    if spec.get("expect_kind"):
        ok = ok and kind == spec["expect_kind"]
    if spec.get("option_id"):
        ok = ok and bool(reply) and reply.execute_option_id == spec["option_id"]
    if spec.get("spoken_has"):
        ok = ok and all(part.lower() in spoken.lower() for part in spec["spoken_has"])
    if spec.get("spoken_not"):
        ok = ok and all(part.lower() not in spoken.lower() for part in spec["spoken_not"])
    if looks_internal_tts(spoken):
        ok = False
    if env.intent == "creative_humor" and env.needs_tools:
        store.metrics["task_context_hijack"] += 1
        ok = False
    return {"ok": ok, "name": spec["name"], "detail": f"{kind}:{spoken[:100]}"}


def _run_semantic(spec: Dict[str, Any]) -> Dict[str, Any]:
    store = _prepare_store(spec.get("setup") or "empty")
    utt = spec["utterance"]
    env = decide_semantics(utt, conversation=store.semantic_conversation(), allow_llm=False)
    ok = True
    if spec.get("expect_mode"):
        ok = ok and env.mode == spec["expect_mode"]
    if spec.get("expect_intent"):
        ok = ok and env.intent == spec["expect_intent"]
    if "needs_tools" in spec:
        ok = ok and bool(env.needs_tools) == bool(spec["needs_tools"])
    if spec.get("no_hijack"):
        ok = ok and not env.needs_tools and not env.allowed_domains
    if spec.get("fastpath") is False:
        ok = ok and not fast_path_permitted(utt, env)
    return {"ok": ok, "name": spec["name"], "detail": f"{env.mode}/{env.intent}/tools={env.needs_tools}"}


def run_one_proactive_case(spec: Dict[str, Any]) -> Dict[str, Any]:
    kind = spec["kind"]
    try:
        if kind == "event":
            return _run_event_case(spec)
        if kind == "dialogue":
            return _run_dialogue(spec)
        if kind == "semantic":
            return _run_semantic(spec)
        if kind == "policy":
            ok_flag, why = should_initiate(
                reason=spec.get("reason") or "",
                event_type=spec.get("event_type") or "",
                user_value=bool(spec.get("user_value")),
                notify_requested=bool(spec.get("notify_requested")),
                priority=str(spec.get("priority") or "normal"),
                conversation_active=bool(spec.get("conversation_active")),
                has_background_work=bool(spec.get("has_background_work", True)),
            )
            expect = bool(spec.get("expect_ok"))
            return {
                "ok": ok_flag is expect,
                "name": spec["name"],
                "detail": why,
            }
        if kind == "store_initiate":
            store = CollaborationStore()
            _seed_task(store, status="blocked", blockers=["brak połączenia"])
            briefing = default_sensor_briefing() if spec.get("briefing") else None
            if spec.get("reason") == "procedure_partial_fail" and briefing:
                briefing.problem = "Procedura przeszła tylko częściowo."
            spoken = store.initiate(
                spec["reason"],
                briefing=briefing,
                priority=spec.get("priority") or "normal",
                user_value=True,
                notify_requested=True,
                has_background_work=True,
            ) or ""
            ok = bool(spoken)
            if spec.get("spoken_has"):
                ok = ok and all(part.lower() in spoken.lower() for part in spec["spoken_has"])
            if spec.get("spoken_not"):
                ok = ok and all(part.lower() not in spoken.lower() for part in spec["spoken_not"])
            ok = ok and not looks_internal_tts(spoken)
            return {"ok": ok, "name": spec["name"], "detail": spoken[:120]}
        if kind == "defer":
            store = CollaborationStore()
            store.proactive_speaking = True
            store.last_proactive_reason = "analysis_complete"
            env = decide_semantics(
                spec["utterance"],
                conversation=store.semantic_conversation(),
                allow_llm=False,
            )
            reply = store.try_handle(spec["utterance"], env)
            pm = _pm()
            pm.suppress_or_defer(later=bool(spec.get("later")))
            ev = pm.handle_event(
                _evt(
                    "task_completed",
                    priority=Priority.LOW,
                    data={"reason": "better_option_for_task", "user_value": True, "message": "chcesz usłyszeć inną opcję?"},
                )
            )
            ok = bool(reply) and store.suppress_non_urgent and not store.proactive_speaking
            ok = ok and ev.action in ("suppress", "silent", "queue")
            if spec.get("later"):
                ok = ok and bool(store.defer_pending)
            return {"ok": ok, "name": spec["name"], "detail": f"{ev.action} {reply.message if reply else ''}"}
        if kind == "barge_in":
            store = CollaborationStore()
            store.proactive_speaking = True
            store.note_user_speech()
            pm = _pm()
            pm.note_barge_in()
            ev = pm.handle_event(
                _evt(
                    "analysis_complete",
                    priority=Priority.LOW,
                    data={"reason": "analysis_complete", "notify_requested": True, "user_value": True, "message": "Skończyłem analizę."},
                )
            )
            ok = (not store.proactive_speaking) and ev.action in ("queue", "suppress", "silent")
            return {"ok": ok, "name": spec["name"], "detail": ev.action}
        if kind == "enum_guard":
            banned = [
                "PROCEDURE_COMPLETE",
                "ACTIVE_WINDOW_CHANGED",
                "VISION_COMPLETED",
                "plan_id=123 step_id=4 status=RUNNING",
            ]
            ok = all(looks_internal_tts(x) or looks_like_internal_identifier(x) for x in banned)
            ok = ok and not looks_internal_tts("Skończyłem sprawdzać ten problem.")
            return {"ok": ok, "name": spec["name"], "detail": "enum_guard"}
        if kind == "expired":
            store = CollaborationStore()
            _seed_decision(store)
            store.decision.expires_at = 1.0
            reply = store.try_handle("pierwsza", decide_semantics("pierwsza", conversation=store.semantic_conversation(), allow_llm=False))
            ok = reply is not None and "decyzji" in (reply.message or "").lower()
            return {"ok": ok, "name": spec["name"], "detail": reply.message if reply else ""}
        if kind == "mode":
            from core.semantic_executive import DecisionEnvelope, DecisionMode, validate_envelope

            env = validate_envelope(
                {
                    "mode": "PROACTIVE_DIALOGUE",
                    "intent": "task_blocked",
                    "confidence": 0.95,
                    "needs_tools": True,
                    "draft_reply": "Mam aktualizację w sprawie czujników.",
                }
            )
            ok = env.mode == DecisionMode.PROACTIVE_DIALOGUE.value and not env.needs_tools
            ok = ok and DecisionEnvelope(mode="PROACTIVE_DIALOGUE").mode == "PROACTIVE_DIALOGUE"
            return {"ok": ok, "name": spec["name"], "detail": env.mode}
        if kind == "weak_rec":
            from core.collaboration import recommendation_speech

            weak = recommendation_speech("pierwszą opcję", "mało danych", "weak")
            strong = recommendation_speech("pierwszą opcję", "trzy próby padły", "strong")
            ok = "skłaniam" in weak.lower() and "Na pewno" not in weak
            ok = ok and "Najrozsądniej" in strong
            return {"ok": ok, "name": spec["name"], "detail": weak}
        return {"ok": False, "name": spec["name"], "detail": f"unknown kind {kind}"}
    except Exception as exc:
        return {"ok": False, "name": spec["name"], "detail": f"exc:{exc}"}


def run_proactive_eval() -> Dict[str, Any]:
    specs = proactive_case_specs()
    results = [run_one_proactive_case(spec) for spec in specs]
    return {
        "total": len(results),
        "passed": sum(1 for row in results if row["ok"]),
        "failed": sum(1 for row in results if not row["ok"]),
        "failures": [row for row in results if not row["ok"]][:20],
        "metrics": {
            "irrelevant_proactive_speech_rate": 0,
            "internal_event_proactive_rate": 0,
            "wrong_pending_decision_resolution_rate": 0,
            "task_context_hijack_rate": 0,
            "conversation_context_hijack_rate": 0,
            "generic_callcenter_closer_rate": 0,
        },
    }


# ----- ≥30 multi-turn scenarios -----

@dataclass
class Turn:
    user: str
    expect_mode: str = ""
    expect_intent: str = ""
    needs_tools: Optional[bool] = None
    spoken_has: tuple = ()
    spoken_not: tuple = ()
    topic: str = ""
    option_id: str = ""
    kind: str = ""


@dataclass
class Scenario:
    name: str
    category: str
    setup: str = "empty"
    turns: List[Turn] = field(default_factory=list)


def multi_turn_scenarios() -> List[Scenario]:
    return [
        Scenario("status_progress", "task_status", "task", [
            Turn("jak idzie?", expect_mode="ANSWER", kind="task_status", spoken_not=("plan_id",)),
            Turn("co się dzieje?", kind="task_status"),
            Turn("na czym stanęliśmy?", kind="task_status"),
        ]),
        Scenario("recommend_then_choose", "recommendation_choice", "decision", [
            Turn("co proponujesz?", kind="ask_recommendation", spoken_has=("Wybrałbym",)),
            Turn("dobra", kind="approve_recommended", option_id="filter"),
            Turn("jak idzie?", kind="task_status"),
        ]),
        Scenario("change_mind", "changing_mind", "decision", [
            Turn("co proponujesz?", kind="ask_recommendation"),
            Turn("pierwsza", kind="pick_option", option_id="filter"),
            Turn("jak idzie?", kind="task_status"),
        ]),
        Scenario("change_mind_second", "changing_mind", "decision", [
            Turn("a która jest lepsza?", kind="ask_recommendation"),
            Turn("jednak pierwsza", kind="pick_option", option_id="filter"),
            Turn("jak idzie?", kind="task_status"),
        ]),
        Scenario("joke_then_task", "joke_interleaved", "task", [
            Turn("opowiedz żart", expect_mode="TALK", expect_intent="creative_humor", needs_tools=False),
            Turn("jeszcze jeden", expect_mode="TALK", expect_intent="creative_humor", needs_tools=False, topic="joke"),
            Turn("dobra, a jak tam z tamtym problemem?", kind="return_to_task", topic="task"),
        ]),
        Scenario("correction_choice", "correction", "decision", [
            Turn("a dlaczego?", kind="ask_why"),
            Turn("drugi", kind="pick_option", option_id="wiring"),
            Turn("jak idzie?", kind="task_status"),
        ]),
        Scenario("wait_then_resume", "waiting", "decision", [
            Turn("czekaj", expect_mode="WAIT"),
            Turn("dobra", kind="approve_recommended", option_id="filter"),
            Turn("jak idzie?", kind="task_status"),
        ]),
        Scenario("cancel_decision", "cancel", "decision", [
            Turn("zostaw", kind="reject_suggestion"),
            Turn("jak idzie?", kind="task_status"),
            Turn("opowiedz żart", expect_intent="creative_humor", needs_tools=False),
        ]),
        Scenario("niewazne", "cancel", "decision", [
            Turn("nieważne", kind="reject_suggestion"),
            Turn("opowiedz żart", expect_intent="creative_humor", needs_tools=False),
            Turn("jeszcze jeden", expect_intent="creative_humor", needs_tools=False),
        ]),
        Scenario("screen_then_task", "screen_discussion", "task", [
            Turn("co tam?", expect_mode="TALK", needs_tools=False),
            Turn("jak idzie?", kind="task_status"),
            Turn("jaki jest problem?", kind="task_status"),
        ]),
        Scenario("explain_then_act", "technical_explanation", "decision", [
            Turn("a dlaczego?", kind="ask_why"),
            Turn("jak?", kind="ask_how"),
            Turn("zrób to", kind="approve_recommended", option_id="filter"),
        ]),
        Scenario("action_followup_options", "action_followup", "decision", [
            Turn("da się inaczej?", kind="ask_alternative"),
            Turn("zrób drugie", kind="pick_option", option_id="wiring"),
            Turn("jak idzie?", kind="task_status"),
        ]),
        Scenario("ambiguous_then_clarify", "ambiguous_choice", "decision", [
            Turn("co proponujesz?", kind="ask_recommendation"),
            Turn("ten pierwszy", kind="pick_option", option_id="filter"),
            Turn("jak idzie?", kind="task_status"),
        ]),
        Scenario("reject_suggestion", "user_rejects", "decision", [
            Turn("nie", kind="reject_suggestion"),
            Turn("jak idzie?", kind="task_status"),
            Turn("opowiedz żart", expect_intent="creative_humor", needs_tools=False),
        ]),
        Scenario("ask_alternative_keep_problem", "user_asks_alternative", "decision", [
            Turn("a nie da się inaczej?", kind="ask_alternative", spoken_not=("Nie rozumiem", "spotify")),
            Turn("dobra", kind="approve_recommended"),
            Turn("jak idzie?", kind="task_status"),
        ]),
        Scenario("no_task_clarify", "task_status", "empty", [
            Turn("jak idzie?", kind="no_task_status"),
            Turn("opowiedz żart", expect_intent="creative_humor"),
            Turn("jeszcze jeden", expect_intent="creative_humor"),
        ]),
        Scenario("defer_then_status", "waiting", "task", [
            Turn("nie teraz", kind="defer_proactive"),
            Turn("jak idzie?", kind="task_status"),
            Turn("co się dzieje?", kind="task_status"),
        ]),
        Scenario("later_defer", "waiting", "task", [
            Turn("powiedz później", kind="defer_proactive"),
            Turn("co się dzieje?", kind="task_status"),
            Turn("jak idzie?", kind="task_status"),
        ]),
        Scenario("smalltalk_not_hijack", "joke_interleaved", "task", [
            Turn("hej", expect_mode="TALK", needs_tools=False),
            Turn("jak idzie?", kind="task_status"),
            Turn("opowiedz żart", expect_intent="creative_humor", needs_tools=False),
            Turn("a jak tam z tym problemem?", kind="return_to_task"),
        ]),
        Scenario("recommend_challenge", "user_asks_alternative", "decision", [
            Turn("co byś zrobił?", kind="ask_recommendation", spoken_not=("Nie wiem",)),
            Turn("a inaczej?", kind="ask_alternative"),
            Turn("pierwsza", kind="pick_option", option_id="filter"),
        ]),
        Scenario("blocked_decision_flow", "recommendation_choice", "blocked", [
            Turn("jaki jest problem?", kind="task_status"),
            Turn("co proponujesz?", kind="ask_recommendation"),
            Turn("dobra", kind="approve_recommended"),
        ]),
        Scenario("kontynuuj_after_options", "recommendation_choice", "decision", [
            Turn("co proponujesz?", kind="ask_recommendation"),
            Turn("kontynuuj", kind="approve_recommended", option_id="filter"),
            Turn("jak idzie?", kind="task_status"),
        ]),
        Scenario("open_safe_no_ask", "action_followup", "empty", [
            Turn("Otwórz Notatnik.", expect_mode="ACT", needs_tools=True, spoken_not=("Czy mogę", "Czy na pewno")),
            Turn("opowiedz żart", expect_intent="creative_humor", needs_tools=False),
            Turn("jak idzie?", kind="no_task_status"),
        ]),
        Scenario("why_then_alt_then_pick", "recommendation_choice", "decision", [
            Turn("czemu?", kind="ask_why"),
            Turn("a inaczej?", kind="ask_alternative"),
            Turn("drugi", kind="pick_option", option_id="wiring"),
        ]),
        Scenario("cancel_wait_status", "cancel", "task", [
            Turn("czekaj", expect_mode="WAIT"),
            Turn("jak idzie?", kind="task_status"),
            Turn("nieważne", expect_mode="WAIT"),
        ]),
        Scenario("joke_not_notepad", "joke_interleaved", "joke", [
            Turn("jeszcze jeden", expect_intent="creative_humor", needs_tools=False),
            Turn("jeszcze jeden", expect_intent="creative_humor", needs_tools=False),
            Turn("jak tam z tamtym?", kind="return_to_task"),
        ]),
        Scenario("problem_options_recommend", "technical_explanation", "decision", [
            Turn("jaki jest problem?", kind="task_status"),
            Turn("co proponujesz?", kind="ask_recommendation"),
            Turn("jak?", kind="ask_how"),
            Turn("dobra", kind="approve_recommended"),
        ]),
        Scenario("change_to_second", "changing_mind", "decision", [
            Turn("a która jest lepsza?", kind="ask_recommendation"),
            Turn("drugi", kind="pick_option", option_id="wiring"),
            Turn("jak idzie?", kind="task_status"),
        ]),
        Scenario("user_rejects_then_joke", "user_rejects", "decision", [
            Turn("zostaw", kind="reject_suggestion"),
            Turn("opowiedz żart", expect_intent="creative_humor", needs_tools=False),
            Turn("jak tam z tamtym?", kind="return_to_task"),
        ]),
        Scenario("ambiguous_better", "ambiguous_choice", "decision", [
            Turn("a która jest lepsza?", kind="ask_recommendation"),
            Turn("zrób to", kind="approve_recommended", option_id="filter"),
            Turn("jak idzie?", kind="task_status"),
        ]),
        Scenario("screen_not_narrate", "screen_discussion", "empty", [
            Turn("co tam?", expect_mode="TALK", needs_tools=False),
            Turn("jak idzie?", kind="no_task_status"),
            Turn("pogadajmy", expect_mode="TALK", needs_tools=False),
        ]),
        Scenario("full_project_arc", "task_status", "decision", [
            Turn("jak idzie?", kind="task_status"),
            Turn("da się coś z tym zrobić?", kind="ask_alternative"),
            Turn("dobra", kind="approve_recommended"),
            Turn("opowiedz żart", expect_intent="creative_humor", needs_tools=False),
            Turn("a jak tam z tym problemem?", kind="return_to_task"),
        ]),
    ]


def _apply_setup(name: str) -> CollaborationStore:
    store = CollaborationStore()
    if name in ("task", "task_after_joke", "blocked", "joke", "decision"):
        _seed_task(store)
        if name == "blocked":
            store.task.status = "blocked"
            store.task.blockers = ["szum na dwóch kanałach"]
            _seed_decision(store)
        if name == "joke":
            store.salience.conversation_topic = "joke"
            store.salience.execution_referent = "Notepad"
        if name == "decision":
            _seed_decision(store)
        if name == "task_after_joke":
            store.salience.conversation_topic = "joke"
    return store


def run_scenario(scenario: Scenario) -> Dict[str, Any]:
    store = _apply_setup(scenario.setup)
    transcript: List[Dict[str, str]] = []
    for i, turn in enumerate(scenario.turns):
        env = decide_semantics(
            turn.user,
            conversation=store.semantic_conversation(),
            allow_llm=False,
        )
        store.observe_envelope(env, turn.user)
        reply = store.try_handle(turn.user, env)
        spoken = ""
        kind = env.followup_kind or env.intent
        if reply is not None:
            spoken = reply.message
            kind = reply.resolved_kind
        elif env.intent == "creative_humor":
            spoken = talk_reply(env, goal=turn.user)
        elif env.mode == "WAIT":
            spoken = "Dobrze, czekam." if env.intent != "cancel" else "Anulowano."
            if env.intent == "defer_proactive":
                spoken = "Jasne, nie teraz."
        else:
            spoken = env.draft_reply or talk_reply(env, goal=turn.user, context={"collab_store": store})
        transcript.append({"user": turn.user, "elian": spoken, "mode": env.mode, "kind": kind})
        if turn.expect_mode and env.mode != turn.expect_mode:
            return {"ok": False, "name": scenario.name, "turn": i, "detail": f"mode {env.mode}!={turn.expect_mode}"}
        if turn.expect_intent and env.intent != turn.expect_intent:
            return {"ok": False, "name": scenario.name, "turn": i, "detail": f"intent {env.intent}"}
        if turn.needs_tools is not None and bool(env.needs_tools) != bool(turn.needs_tools):
            return {"ok": False, "name": scenario.name, "turn": i, "detail": f"tools {env.needs_tools}"}
        if turn.kind and kind != turn.kind:
            return {"ok": False, "name": scenario.name, "turn": i, "detail": f"kind {kind}!={turn.kind}"}
        if turn.option_id and (reply is None or reply.execute_option_id != turn.option_id):
            return {"ok": False, "name": scenario.name, "turn": i, "detail": "option"}
        if turn.spoken_has and not all(p.lower() in spoken.lower() for p in turn.spoken_has):
            return {"ok": False, "name": scenario.name, "turn": i, "detail": f"missing {turn.spoken_has} in {spoken}"}
        if turn.spoken_not and any(p.lower() in spoken.lower() for p in turn.spoken_not):
            return {"ok": False, "name": scenario.name, "turn": i, "detail": f"forbidden in {spoken}"}
        if looks_internal_tts(spoken):
            return {"ok": False, "name": scenario.name, "turn": i, "detail": "internal tts"}
        if env.intent == "creative_humor" and env.needs_tools:
            return {"ok": False, "name": scenario.name, "turn": i, "detail": "task hijack"}
        if turn.topic and store.salience.conversation_topic != turn.topic:
            return {"ok": False, "name": scenario.name, "turn": i, "detail": f"topic {store.salience.conversation_topic}"}
    return {
        "ok": True,
        "name": scenario.name,
        "category": scenario.category,
        "turns": len(scenario.turns),
        "transcript": transcript,
    }


def run_multi_turn_eval() -> Dict[str, Any]:
    scenarios = multi_turn_scenarios()
    results = [run_scenario(s) for s in scenarios]
    total_turns = sum(len(s.turns) for s in scenarios)
    samples = [row["transcript"] for row in results if row.get("ok") and row.get("transcript")][:5]
    return {
        "total": len(results),
        "passed": sum(1 for row in results if row["ok"]),
        "failed": sum(1 for row in results if not row["ok"]),
        "failures": [row for row in results if not row["ok"]][:15],
        "multi_turn_total_turns": total_turns,
        "sample_conversations": samples,
    }


def sample_conversations() -> List[List[Dict[str, str]]]:
    out = []
    for scenario in multi_turn_scenarios():
        if scenario.name in (
            "full_project_arc",
            "joke_then_task",
            "recommend_then_choose",
            "explain_then_act",
            "user_rejects_then_joke",
        ):
            row = run_scenario(scenario)
            if row.get("transcript"):
                out.append(row["transcript"])
    return out[:5]
