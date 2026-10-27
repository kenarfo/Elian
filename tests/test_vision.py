# ==========================
# ELIAN — Vision / screen awareness tests (Phase 5)
# Offline — no monitor, Ollama vision, OCR, or real mouse
# ==========================

from __future__ import annotations

import unittest
from unittest.mock import patch

from core.builtin_tools import build_default_registry
from core.conversation_manager import ConversationManager
from core.model_client import FakeModelClient
from core.planner import Planner
from core.tool_result import ToolStatus
from core.tools import ToolRegistry, reset_registry_for_tests
from vision.actions import click_element
from vision.capture import FakeScreenCapture
from vision.coordinates import scale_bounds, validate_point_in_screen
from vision.models import Bounds, UIElement
from vision.ocr import FakeOCR, NullOCR, OCRBox
from vision.provider import FakeVisionProvider, NullVisionProvider
from vision.session import VisionSession, reset_vision_session_for_tests
from vision.tools import register_vision_tools
from vision.uia import FakeUIBackend


def _el(eid, role, text, x=10, y=10, w=80, h=30, **kwargs):
    return UIElement(
        id=eid,
        role=role,
        text=text,
        bounds=Bounds(x, y, w, h),
        source=kwargs.pop("source", "uia"),
        confidence=kwargs.pop("confidence", 0.95),
        **kwargs,
    )


def _session(elements, *, summary="Test screen.", vision_fail=False, ocr_boxes=None):
    ui = FakeUIBackend(list(elements))
    vision = FakeVisionProvider(summary=summary, elements=[])
    vision.fail = bool(vision_fail)
    ocr = FakeOCR(ocr_boxes or []) if ocr_boxes is not None else NullOCR()
    session = VisionSession(
        capture=FakeScreenCapture(
            width=1920,
            height=1080,
            active_window="Google Chrome",
            active_process="chrome.exe",
        ),
        ui=ui,
        ocr=ocr,
        vision=vision,
        cache_ttl_ms=0,
    )
    reset_vision_session_for_tests(session)
    return session, ui


class CoordinateTests(unittest.TestCase):
    def test_scale_2x(self):
        src = Bounds(100, 50, 40, 20)
        out = scale_bounds(
            src,
            from_width=1280,
            from_height=720,
            to_width=2560,
            to_height=1440,
        )
        self.assertEqual(out.x, 200)
        self.assertEqual(out.y, 100)
        self.assertEqual(out.width, 80)
        self.assertEqual(out.height, 40)

    def test_out_of_bounds_rejected(self):
        screen = Bounds(0, 0, 1920, 1080)
        self.assertIsNotNone(validate_point_in_screen(-300, 10, screen))
        self.assertIsNone(validate_point_in_screen(100, 100, screen))


class DescribeScreenTests(unittest.TestCase):
    def test_describe_screen_observation(self):
        elements = [
            _el("button_1", "button", "Zapisz", 100, 200),
            _el("input_1", "textbox", "Imię", 100, 100, w=200),
        ]
        session, _ = _session(elements, summary="Chrome z formularzem.")
        registry = ToolRegistry(confirm_medium=False)
        register_vision_tools(registry)
        result = registry.invoke("describe_screen", {})
        self.assertTrue(result.success)
        self.assertIn("Zapisz", result.data.get("observation", ""))
        self.assertIn("button_1", result.message + str(result.data))
        self.assertEqual(result.data.get("active_window"), "Google Chrome")


class FindElementTests(unittest.TestCase):
    def test_find_zapisz(self):
        session, _ = _session([_el("button_1", "button", "Zapisz")])
        session.refresh(force=True)
        registry = ToolRegistry(confirm_medium=False)
        register_vision_tools(registry)
        result = registry.invoke("find_ui_element", {"query": "Zapisz"})
        self.assertTrue(result.success)
        self.assertEqual(result.data.get("element_id"), "button_1")
        self.assertFalse(result.data.get("ambiguous"))

    def test_ambiguity_two_save_buttons(self):
        session, _ = _session(
            [
                _el("button_1", "button", "Zapisz", 10, 10),
                _el("button_2", "button", "Zapisz", 10, 80),
            ]
        )
        session.refresh(force=True)
        registry = ToolRegistry(confirm_medium=False)
        register_vision_tools(registry)
        result = registry.invoke("find_ui_element", {"query": "Zapisz"})
        self.assertTrue(result.success)
        self.assertTrue(result.data.get("ambiguous"))
        self.assertGreaterEqual(len(result.data.get("candidates") or []), 2)


class ClickAndStaleTests(unittest.TestCase):
    def test_click_element(self):
        session, ui = _session([_el("button_1", "button", "Zapisz")])
        session.refresh(force=True)
        registry = ToolRegistry(confirm_medium=False)
        register_vision_tools(registry)
        result = registry.invoke("click_ui_element", {"element_id": "button_1"})
        self.assertTrue(result.success)
        self.assertIn("button_1", ui.clicks)
        self.assertTrue(result.data.get("click_dispatched"))

    def test_stale_snapshot_rejected(self):
        session, ui = _session([_el("button_1", "button", "Zapisz")])
        snap = session.refresh(force=True)
        element_id = f"{snap.id}:button_1"
        session.invalidate()
        # Keep outdated current with invalid flag for resolve
        session.current = snap
        result = click_element(session, element_id, confirmed=True, reobserve=False)
        self.assertFalse(result.success)
        self.assertEqual(result.error, "stale_snapshot")
        self.assertEqual(ui.clicks, [])


class RiskAndPasswordTests(unittest.TestCase):
    def test_delete_account_needs_confirmation(self):
        session, ui = _session(
            [_el("button_1", "button", "Usuń konto", 50, 50)]
        )
        session.refresh(force=True)
        registry = ToolRegistry(confirm_medium=False)
        register_vision_tools(registry)
        result = registry.invoke("click_ui_element", {"element_id": "button_1"})
        self.assertEqual(result.status, ToolStatus.NEEDS_CONFIRMATION)
        self.assertEqual(ui.clicks, [])

        confirmed = registry.invoke(
            "click_ui_element",
            {"element_id": "button_1", "confirmed": True},
        )
        self.assertTrue(confirmed.success)
        self.assertIn("button_1", ui.clicks)

    def test_password_not_in_observation_or_logs_payload(self):
        session, _ = _session(
            [
                _el(
                    "input_1",
                    "textbox",
                    "secret-password-value",
                    is_password=True,
                )
            ]
        )
        snap = session.refresh(force=True)
        obs = snap.observation_for_planner()
        self.assertNotIn("secret-password-value", obs)
        data = snap.to_result_data()
        blob = str(data)
        self.assertNotIn("secret-password-value", blob)


class AfterActionTests(unittest.TestCase):
    def test_click_then_new_observation(self):
        start = [_el("button_1", "button", "Settings", 20, 20)]
        after = [_el("button_1", "button", "Back", 20, 20)]
        session, ui = _session(start, summary="Main")

        def on_click(_el):
            ui.set_elements(after)
            session.vision.summary = "Settings page"

        ui.on_click = on_click
        session.refresh(force=True)
        registry = ToolRegistry(confirm_medium=False)
        register_vision_tools(registry)
        result = registry.invoke("click_ui_element", {"element_id": "button_1"})
        self.assertTrue(result.success)
        new_obs = result.data.get("new_observation") or ""
        self.assertIn("Settings page", new_obs + (session.current.summary if session.current else ""))


class FallbackTests(unittest.TestCase):
    def test_vision_fail_uia_still_works(self):
        elements = [_el("button_1", "button", "OK")]
        ui = FakeUIBackend(elements)
        vision = FakeVisionProvider(summary="")
        vision.fail = True
        session = VisionSession(
            capture=FakeScreenCapture(active_window="App"),
            ui=ui,
            ocr=NullOCR(),
            vision=vision,
            cache_ttl_ms=0,
        )
        reset_vision_session_for_tests(session)
        snap = session.refresh(force=True)
        self.assertTrue(any(e.text == "OK" for e in snap.elements))
        self.assertIn("uia", snap.sources)

    def test_uia_miss_ocr_finds_text(self):
        ui = FakeUIBackend([])
        ocr = FakeOCR(
            [OCRBox("Zapisz", Bounds(10, 10, 60, 20), confidence=0.9)]
        )
        session = VisionSession(
            capture=FakeScreenCapture(active_window="App"),
            ui=ui,
            ocr=ocr,
            vision=NullVisionProvider(),
            cache_ttl_ms=0,
        )
        reset_vision_session_for_tests(session)
        snap = session.refresh(force=True)
        self.assertTrue(any("Zapisz" in (e.text or "") for e in snap.elements))
        self.assertIn("ocr", snap.sources)


class NativeToolFirstTests(unittest.TestCase):
    def test_glosniej_zero_vision_capture(self):
        session, _ = _session([])
        capture = session.capture_backend
        assert isinstance(capture, FakeScreenCapture)
        before = capture.calls

        model = FakeModelClient(responses=[])
        registry = build_default_registry(confirm_medium=False)
        manager = ConversationManager(registry=registry, model=model)
        manager.activate()
        with patch("core.builtin_tools.Music.run", return_value="Gotowe."):
            result = manager.handle("głośniej")

        self.assertIn(result.source, ('fast_path', 'action_loop', 'agent'))
        self.assertEqual(len(model.calls), 0)
        self.assertEqual(capture.calls, before)


class CancelVisionFlowTests(unittest.TestCase):
    def test_cancel_stops_second_step(self):
        session, ui = _session([_el("button_1", "button", "Settings")])
        session.refresh(force=True)
        registry = ToolRegistry(confirm_medium=False)
        register_vision_tools(registry)

        model = FakeModelClient(
            responses=[
                {"type": "tool_call", "tool": "describe_screen", "arguments": {}},
                {
                    "type": "tool_call",
                    "tool": "click_ui_element",
                    "arguments": {"element_id": "button_1"},
                },
                {"type": "final_answer", "message": "Gotowe."},
            ]
        )
        planner = Planner(registry, model=model, max_steps=4)

        def cancel_check():
            return len(registry.history) >= 1

        result = planner.run("Otwórz ustawienia.", cancel_check=cancel_check)
        self.assertEqual(result.source, "cancelled")
        self.assertEqual(ui.clicks, [])


class FullAgentVisionFlowTests(unittest.TestCase):
    def test_open_settings_flow(self):
        start = [_el("button_1", "button", "Settings", 30, 30)]
        after = [_el("button_1", "button", "Notifications", 30, 60)]
        session, ui = _session(start, summary="Main window with Settings.")

        def on_click(_el):
            ui.set_elements(after)
            session.vision.summary = "Settings page"

        ui.on_click = on_click
        reset_vision_session_for_tests(session)

        registry = ToolRegistry(confirm_medium=False)
        register_vision_tools(registry)
        model = FakeModelClient(
            responses=[
                {"type": "tool_call", "tool": "describe_screen", "arguments": {}},
                {
                    "type": "tool_call",
                    "tool": "find_ui_element",
                    "arguments": {"query": "Settings"},
                },
                {
                    "type": "tool_call",
                    "tool": "click_ui_element",
                    "arguments": {"element_id": "button_1"},
                },
                {"type": "final_answer", "message": "Gotowe."},
            ]
        )
        planner = Planner(registry, model=model, max_steps=6)
        result = planner.run("Otwórz ustawienia.")
        self.assertEqual(result.message, "Gotowe.")
        self.assertIn("button_1", ui.clicks)
        self.assertGreaterEqual(len(result.steps), 3)


class OutOfBoundsClickTests(unittest.TestCase):
    def test_coordinate_out_of_bounds(self):
        session, ui = _session([])
        session.refresh(force=True)
        registry = ToolRegistry(confirm_medium=False)
        register_vision_tools(registry)
        result = registry.invoke(
            "click_coordinates",
            {"x": -300, "y": 10, "confirmed": True},
            confirmed=True,
        )
        self.assertFalse(result.success)
        self.assertEqual(result.error, "out_of_bounds")
        self.assertEqual(ui.clicks, [])


class TypeIntoTests(unittest.TestCase):
    def test_type_name(self):
        session, ui = _session([_el("input_1", "textbox", "Nazwa", 10, 10, w=200)])
        session.refresh(force=True)
        registry = ToolRegistry(confirm_medium=False)
        register_vision_tools(registry)
        result = registry.invoke(
            "type_into_element",
            {"element_id": "input_1", "text": "Franek"},
        )
        self.assertTrue(result.success)
        self.assertEqual(ui.typed[0], ("input_1", "Franek"))


def tearDownModule():
    reset_vision_session_for_tests(None)
    reset_registry_for_tests(None)


if __name__ == "__main__":
    unittest.main()
