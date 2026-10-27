import asyncio
import json
import os
import sys
import tempfile
import threading
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, Mock, call, patch


PROJECT_ROOT = Path(__file__).resolve().parents[1]

if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))


import main
import AppLauncher
import Brain
import Commands
import ComputerControl
import HomeAssistantMqttBridge as mqtt_bridge
import IntentRouter
import Memory
import HomeAssistant
import ConversationContext
import DeviceResolver
import HaActions
import Voice
from Skills import Music, System
import Skills.SmartHome as smart_home
import Skills.Weather as weather
import TextCorrector
import VizziniBluetooth as vizzini
import WebActions
from Memory import MEMORY_FILE
from SkillsLoader import SKILLS_FOLDER
from WakeWord import is_wake_word, remove_wake_word
from VizziniBluetooth import (
    INITIALIZE_COMMAND,
    POWER_STATUS_QUERY,
    build_brightness_command,
    build_color_temperature_command,
    build_power_command,
    build_rgb_color_command,
    parse_status_frame,
)


class MqttBridgeTests(unittest.TestCase):

    class FakePublication:

        def wait_for_publish(self, timeout=None):
            return timeout

    class FakeClient:

        def __init__(self, *_args, **_kwargs):
            self.published = []
            self.suppress_exceptions = False

        def username_pw_set(self, *_args, **_kwargs):
            pass

        def will_set(self, *_args, **_kwargs):
            pass

        def reconnect_delay_set(self, *_args, **_kwargs):
            pass

        def publish(self, topic, payload, **kwargs):
            self.published.append((topic, payload, kwargs))
            return MqttBridgeTests.FakePublication()

    def test_discovery_exposes_full_light_entity(self):

        payload = mqtt_bridge.build_discovery_payload()

        self.assertEqual(
            payload["default_entity_id"],
            "light.vizzini"
        )
        self.assertEqual(payload["schema"], "json")
        self.assertTrue(payload["brightness"])
        self.assertTrue(payload["color_temp_kelvin"])
        self.assertEqual(
            payload["supported_color_modes"],
            ["rgb", "color_temp"]
        )
        self.assertEqual(
            payload["device"]["identifiers"],
            ["elian_vizzini_zbek37"]
        )
        self.assertEqual(payload["origin"]["name"], "Elian")

    def test_translates_json_light_command_to_ble(self):

        commands = mqtt_bridge.build_ble_commands({
            "state": "ON",
            "brightness": 128,
            "color": {
                "r": 255,
                "g": 0,
                "b": 0,
            },
        })

        self.assertEqual(
            [command.hex() for command in commands],
            [
                "0000100103010000",
                "0000110103800000",
                "000013070400fe0000",
            ]
        )

    def test_translates_kelvin_and_off_commands(self):

        temperature = mqtt_bridge.build_ble_commands({
            "state": "ON",
            "color_temp": 2700,
        })
        turn_off = mqtt_bridge.build_ble_commands({
            "state": "OFF",
        })

        self.assertEqual(
            [command.hex() for command in temperature],
            [
                "0000100103010000",
                "000012010401720000",
            ]
        )
        self.assertEqual(
            [command.hex() for command in turn_off],
            ["0000100103000000"]
        )

    def test_successful_command_publishes_confirmed_state(self):

        controller = Mock()
        bridge = mqtt_bridge.VizziniMqttBridge(
            {
                "host": "192.168.0.31",
                "username": "elian_bridge",
                "bluetooth_address": "28:DB:A7:16:D5:39",
            },
            "test-password",
            client_factory=self.FakeClient,
            light_controller=controller,
        )
        bridge._connected.set()

        bridge._execute({
            "state": "ON",
            "brightness": 128,
            "color_temp": 4000,
        })

        controller.assert_called_once()
        state_messages = [
            json.loads(payload)
            for topic, payload, _kwargs in bridge.client.published
            if topic == mqtt_bridge.STATE_TOPIC
        ]
        self.assertEqual(len(state_messages), 1)
        self.assertEqual(
            state_messages[0],
            {
                "state": "ON",
                "brightness": 128,
                "color_mode": "color_temp",
                "color_temp": 4000,
            }
        )

    def test_reconnect_queues_only_one_state_sync(self):

        bridge = mqtt_bridge.VizziniMqttBridge(
            {
                "host": "192.168.0.31",
                "username": "elian_bridge",
            },
            "test-password",
            client_factory=self.FakeClient,
        )

        bridge._request_sync()
        bridge._request_sync()
        bridge._request_sync()

        self.assertEqual(bridge._commands.qsize(), 1)

    def test_state_sync_uses_one_short_power_query(self):

        controller = Mock(return_value=vizzini.ControlResult(
            address="28:DB:A7:16:D5:39",
            updates=(),
        ))
        bridge = mqtt_bridge.VizziniMqttBridge(
            {
                "host": "192.168.0.31",
                "username": "elian_bridge",
                "bluetooth_address": "28:DB:A7:16:D5:39",
            },
            "test-password",
            client_factory=self.FakeClient,
            light_controller=controller,
        )

        bridge._sync_state()

        controller.assert_called_once_with(
            (POWER_STATUS_QUERY,),
            address="28:DB:A7:16:D5:39",
            timeout=15,
            attempts=1,
        )


class WakeWordTests(unittest.TestCase):

    def test_wake_word_is_removed_from_command(self):

        text = "Hej Elian, otwórz kalkulator"

        self.assertTrue(is_wake_word(text))
        self.assertEqual(
            remove_wake_word(text),
            "otwórz kalkulator"
        )

    def test_hi_elian_english_wake(self):
        """Product wake for en-US: Hi Elian (not only Polish hej)."""
        from WakeWord import extract_wake

        self.assertTrue(is_wake_word("Hi Elian"))
        self.assertTrue(is_wake_word("hi elian volume up"))
        self.assertTrue(is_wake_word("Hey Elian, turn up the volume"))
        extracted = extract_wake("Hi Elian, turn up the volume")
        self.assertTrue(extracted.wake_detected)
        self.assertEqual(extracted.raw_command.lower(), "turn up the volume")

    def test_whisper_higher_lane_is_hi_elian(self):
        """Live sim: Whisper often hears 'Hi Elian' as higher lane/lean/High LN."""
        from WakeWord import extract_wake

        for heard, expect_cmd in (
            ("higher lane, volume up.", "volume up"),
            ("Higher lean, volume up.", "volume up"),
            ("High LN, volume up.", "volume up"),
            ("high ln volume up", "volume up"),
            ("Hey, Alien, co tam?", "co tam"),
            ("Hey, Alien, volume up", "volume up"),
        ):
            self.assertTrue(is_wake_word(heard), msg=heard)
            extracted = extract_wake(heard)
            self.assertTrue(extracted.wake_detected, msg=heard)
            self.assertEqual(
                extracted.raw_command.lower().rstrip(".?!"),
                expect_cmd,
                msg=heard,
            )

    def test_legacy_stt_wake_accepts_high_ln(self):
        """Production legacy wake listener must accept Whisper 'High LN' slip."""
        from voice.settings import VoiceSettings
        from voice.wake import LegacySttWakeListener

        heard = {"n": 0}

        def listen_fn(wait_timeout=None, llm_correct=False):
            heard["n"] += 1
            if heard["n"] == 1:
                return "High LN, volume up."
            return ""

        listener = LegacySttWakeListener(
            listen_fn,
            VoiceSettings(wake_legacy_stt_fallback=True),
        )
        result = listener.wait_for_wake(timeout=0.5)
        self.assertTrue(result.detected)
        self.assertEqual(str(listener.last_remainder or "").lower(), "volume up")

    def test_wake_word_requires_word_boundary(self):

        self.assertFalse(
            is_wake_word("kamelian")
        )

    def test_in_content_elian_is_not_stripped(self):
        from WakeWord import extract_command_from_transcript

        command = extract_command_from_transcript(
            "Hej Elian, otwórz Notatnik i wpisz Elian działa jak agent i zaznacz cały tekst."
        )
        self.assertEqual(
            command,
            "otwórz Notatnik i wpisz Elian działa jak agent i zaznacz cały tekst",
        )
        self.assertFalse(
            is_wake_word("otwórz notatnik i wpisz elian działa jak agent")
        )


class ProjectPathTests(unittest.TestCase):

    def test_data_and_skills_paths_are_absolute(self):

        self.assertTrue(
            Path(MEMORY_FILE).is_absolute()
        )

        self.assertTrue(
            Path(SKILLS_FOLDER).is_absolute()
        )

        self.assertEqual(
            Path(MEMORY_FILE).parent,
            PROJECT_ROOT / "Data"
        )

        self.assertEqual(
            Path(SKILLS_FOLDER),
            PROJECT_ROOT / "Skills"
        )


class PersistentMemoryTests(unittest.TestCase):

    def test_migrates_legacy_list_and_deduplicates_facts(self):

        with tempfile.TemporaryDirectory() as directory:
            memory_file = Path(directory) / "memory.json"
            memory_file.write_text(
                json.dumps(["Lubię krótkie odpowiedzi"]),
                encoding="utf-8"
            )

            with patch.object(
                Memory,
                "MEMORY_FILE",
                str(memory_file)
            ):
                self.assertEqual(
                    Memory.load_memory(),
                    ["Lubię krótkie odpowiedzi"]
                )
                self.assertFalse(Memory.add_memory(
                    "lubię krótkie odpowiedzi"
                ))
                self.assertTrue(Memory.add_memory(
                    "Moje miasto to Łódź"
                ))

                stored = json.loads(
                    memory_file.read_text(encoding="utf-8")
                )

        self.assertEqual(stored["version"], 2)
        self.assertEqual(len(stored["facts"]), 2)

    def test_persists_actions_conversation_and_context(self):

        with tempfile.TemporaryDirectory() as directory:
            memory_file = Path(directory) / "memory.json"

            with patch.object(
                Memory,
                "MEMORY_FILE",
                str(memory_file)
            ):
                Memory.record_action(
                    "web_search",
                    "pogoda Łódź",
                    "Wyszukuję.",
                    value=2
                )
                Memory.record_conversation(
                    "user",
                    "Lubię muzykę filmową"
                )
                Memory.set_context(
                    "last_file_results",
                    ["C:/test.txt"]
                )

                action = Memory.get_last_action()
                conversation = Memory.load_recent_conversation()
                context = Memory.get_context("last_file_results")

        self.assertEqual(action["action"], "web_search")
        self.assertEqual(action["value"], 2)
        self.assertEqual(conversation[0]["role"], "user")
        self.assertEqual(context, ["C:/test.txt"])


class BrainMemoryTests(unittest.TestCase):

    def test_chat_uses_relevant_memory_and_records_turns(self):

        response = {
            "message": {
                "content": "Zapamiętałem ten styl."
            }
        }

        with (
            patch.object(
                Brain,
                "search_memory",
                return_value=["Lubię krótkie odpowiedzi"]
            ),
            patch.object(Brain, "record_conversation") as record_mock,
            patch.object(
                Brain.CLIENT,
                "chat",
                return_value=response
            ) as chat_mock
        ):
            original = list(Brain.conversation)
            try:
                result = Brain.ask_ai("Jaki styl odpowiedzi lubię?")
            finally:
                Brain.conversation = original

        self.assertEqual(result, "Zapamiętałem ten styl.")
        user_message = chat_mock.call_args.kwargs["messages"][-2]["content"]
        self.assertIn("Lubię krótkie odpowiedzi", user_message)
        self.assertEqual(record_mock.call_count, 2)


class VizziniBluetoothTests(unittest.TestCase):

    def test_resolves_address_to_ble_device_before_connecting(self):

        device = Mock()
        device.address = "28:DB:A7:16:D5:39"

        async def run_test():
            with patch.object(
                vizzini.BleakScanner,
                "find_device_by_address",
                new=AsyncMock(return_value=device),
            ) as find_mock:
                result = await vizzini._find_device(
                    "28:DB:A7:16:D5:39",
                    8,
                )

            find_mock.assert_awaited_once_with(
                "28:DB:A7:16:D5:39",
                timeout=5.0,
            )
            self.assertIs(result, device)

        asyncio.run(run_test())

    def test_sync_control_uses_dedicated_ble_thread(self):

        async def current_thread_name(*_args):

            return threading.current_thread().name

        with patch.object(
            vizzini,
            "control_light",
            new=current_thread_name
        ):

            thread_name = vizzini.control_light_sync([])

        self.assertEqual(thread_name, "VizziniBLE")

    def test_parses_power_report(self):

        update = parse_status_frame(
            bytes.fromhex("00001003020101")
        )

        self.assertEqual(update.attribute, "power")
        self.assertTrue(update.current)
        self.assertTrue(update.target)

    def test_parses_color_temperature_report(self):

        update = parse_status_frame(
            bytes.fromhex("000012030401720172")
        )

        self.assertEqual(
            update.attribute,
            "color_temperature_mired"
        )
        self.assertEqual(update.current, 370)
        self.assertEqual(update.target, 370)

    def test_parses_brightness_report(self):

        update = parse_status_frame(
            bytes.fromhex("00001103027f7f")
        )

        self.assertEqual(update.attribute, "brightness_percent")
        self.assertEqual(update.current, 50)
        self.assertEqual(update.target, 50)

    def test_parses_hue_and_saturation_reports(self):

        hue = parse_status_frame(
            bytes.fromhex("00001303027f7f")
        )
        saturation = parse_status_frame(
            bytes.fromhex("0000130602fefe")
        )

        self.assertEqual(hue.attribute, "hue_degrees")
        self.assertEqual(hue.current, 179)
        self.assertEqual(
            saturation.attribute,
            "saturation_percent"
        )
        self.assertEqual(saturation.current, 100)

    def test_builds_commands_used_by_enki_app(self):

        self.assertEqual(
            INITIALIZE_COMMAND.hex(),
            "00002001020002"
        )
        self.assertEqual(
            POWER_STATUS_QUERY.hex(),
            "00001002"
        )
        self.assertEqual(
            build_power_command(True).hex(),
            "0000100103010000"
        )
        self.assertEqual(
            build_power_command(False).hex(),
            "0000100103000000"
        )
        self.assertEqual(
            build_brightness_command(50).hex(),
            "0000110103800000"
        )
        self.assertEqual(
            build_color_temperature_command(2700).hex(),
            "000012010401720000"
        )
        self.assertEqual(
            build_rgb_color_command(255, 0, 0).hex(),
            "000013070400fe0000"
        )

    def test_rejects_truncated_report(self):

        with self.assertRaises(ValueError):
            parse_status_frame(
                bytes.fromhex("00001303027f")
            )


class AppLauncherTests(unittest.TestCase):

    def test_fallback_does_not_use_shell(self):

        with (
            patch.object(
                AppLauncher,
                "find_start_menu_app",
                return_value=None
            ),
            patch.object(
                AppLauncher.subprocess,
                "Popen"
            ) as popen_mock
        ):

            success, _ = (
                AppLauncher.launch_application(
                    "przykładowa aplikacja"
                )
            )

        self.assertTrue(success)

        popen_mock.assert_called_once_with(
            ["przykładowa aplikacja"],
            stdout=AppLauncher.subprocess.DEVNULL,
            stderr=AppLauncher.subprocess.DEVNULL
        )

    def test_rejects_shell_characters_in_app_name(self):

        with patch.object(
            AppLauncher.subprocess,
            "Popen"
        ) as popen_mock:

            success, _ = AppLauncher.launch_application(
                "notatnik & shutdown"
            )

        self.assertFalse(success)
        popen_mock.assert_not_called()


class ComputerControlTests(unittest.TestCase):

    def test_finds_files_only_in_configured_roots(self):

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            expected = root / "Faktura sierpień.pdf"
            expected.write_text("test", encoding="utf-8")
            (root / "inne.txt").write_text("test", encoding="utf-8")

            with (
                patch.dict(
                    ComputerControl.CONTROL_CONFIG,
                    {"search_roots": [directory]},
                    clear=False
                ),
                patch.object(ComputerControl, "set_context")
            ):
                results = ComputerControl.find_files("faktura")

        self.assertEqual(results, [str(expected)])

    def test_does_not_open_executable_as_document(self):

        with tempfile.TemporaryDirectory() as directory:
            executable = Path(directory) / "program.exe"
            executable.write_bytes(b"MZ")

            with patch.object(
                ComputerControl.os,
                "startfile"
            ) as start_mock:
                success, message = ComputerControl._open_path(
                    executable
                )

        self.assertFalse(success)
        self.assertIn("wykonywalnych", message)
        start_mock.assert_not_called()

    def test_close_application_refuses_system_process(self):

        result = ComputerControl.close_application("explorer")

        self.assertIn("krytycznego", result)


class WebCommandTests(unittest.TestCase):

    def test_youtube_request_is_search_not_application(self):

        with patch.object(
            WebActions.webbrowser,
            "open",
            return_value=True
        ) as open_mock:

            result = Commands.run_command(
                "włącz film czajnika na YouTubie"
            )

        self.assertEqual(
            result,
            "Wyszukuję na YouTube: film czajnika."
        )
        self.assertIn(
            "search_query=film+czajnika",
            open_mock.call_args.args[0]
        )

    def test_internet_search_opens_google_results(self):

        with patch.object(
            WebActions.webbrowser,
            "open",
            return_value=True
        ) as open_mock:

            result = Commands.run_command(
                "wyszukaj dobry przepis na pizzę w internecie"
            )

        self.assertEqual(
            result,
            "Wyszukuję w internecie: dobry przepis na pizzę."
        )
        self.assertIn(
            "q=dobry+przepis+na+pizz%C4%99",
            open_mock.call_args.args[0]
        )

    def test_natural_date_question_is_handled_directly(self):

        result = Commands.run_command(
            "jaki dziś mamy dzień"
        )

        self.assertIn("Dzisiaj jest", result)
        self.assertRegex(result, r"\d{1,2}\.\d{1,2}\.\d{4}")


class IntentRouterTests(unittest.TestCase):

    def tearDown(self):

        IntentRouter.clear_pending_action()

    def response(self, **payload):

        return {
            "message": {
                "content": json.dumps(payload)
            }
        }

    def test_understands_natural_application_request(self):

        response = self.response(
            action="open_app",
            argument="Discord",
            value=0,
            confidence=0.96
        )

        with patch.object(
            IntentRouter.CLIENT,
            "chat",
            return_value=response
        ):

            intent = IntentRouter.classify_intent(
                "czy możesz odpalić mi Discorda"
            )

        self.assertEqual(intent.action, "open_app")
        self.assertEqual(intent.argument, "Discord")

    def test_rejects_low_confidence_action(self):

        response = self.response(
            action="open_app",
            argument="Discord",
            value=0,
            confidence=0.3
        )

        with patch.object(
            IntentRouter.CLIENT,
            "chat",
            return_value=response
        ):

            intent = IntentRouter.classify_intent(
                "może coś otwórz"
            )

        self.assertIsNone(intent)

    def test_normalizes_color_returned_by_model(self):

        response = self.response(
            action="light_color",
            argument="czerwieżny",
            value=0,
            confidence=0.95
        )

        with patch.object(
            IntentRouter.CLIENT,
            "chat",
            return_value=response
        ):

            intent = IntentRouter.classify_intent(
                "ustaw czerwone światło"
            )

        self.assertEqual(intent.argument, "czerwony")

    def test_brightness_uses_number_from_user_text(self):

        response = self.response(
            action="light_brightness",
            argument="",
            value=80,
            confidence=0.95
        )

        with patch.object(
            IntentRouter.CLIENT,
            "chat",
            return_value=response
        ):

            intent = IntentRouter.classify_intent(
                "ustaw jasność lampy na 35 procent"
            )

        self.assertEqual(intent.value, 35)

    def test_preserves_exact_file_query_from_user(self):

        response = self.response(
            action="find_file",
            argument="faktura sierpiełć",
            value=0,
            confidence=0.95
        )

        with (
            patch.object(
                IntentRouter.CLIENT,
                "chat",
                return_value=response
            ),
            patch.object(
                IntentRouter,
                "get_last_action",
                return_value=None
            )
        ):
            intent = IntentRouter.classify_intent(
                "znajdź plik faktura sierpień"
            )

        self.assertEqual(intent.argument, "faktura sierpień")

    def test_executes_only_validated_application_name(self):

        intent = IntentRouter.Intent(
            action="open_app",
            argument="Discord",
            confidence=0.95
        )

        with patch.object(
            IntentRouter,
            "launch_application",
            return_value=(True, "Uruchomiłem Discord.")
        ) as launch_mock:

            result = IntentRouter.execute_intent(intent)

        launch_mock.assert_called_once_with("Discord")
        self.assertEqual(result, "Uruchomiłem Discord.")

    def test_risky_action_waits_for_confirmation(self):

        intent = IntentRouter.Intent(
            action="close_app",
            argument="Discord",
            confidence=0.99
        )

        with (
            patch.object(
                IntentRouter,
                "classify_intent",
                return_value=intent
            ),
            patch.object(
                IntentRouter,
                "execute_intent",
                return_value="Zamykam aplikację Discord."
            ) as execute_mock,
            patch.object(IntentRouter, "record_action")
        ):
            question = IntentRouter.run_intent(
                "zamknij Discord"
            )
            result = IntentRouter.run_intent("tak")

        self.assertIn("Czy na pewno", question)
        self.assertEqual(result, "Zamykam aplikację Discord.")
        execute_mock.assert_called_once_with(intent)

    def test_risky_action_can_be_cancelled(self):

        intent = IntentRouter.Intent(
            action="shutdown",
            confidence=0.99
        )

        with (
            patch.object(
                IntentRouter,
                "classify_intent",
                return_value=intent
            ),
            patch.object(
                IntentRouter,
                "execute_intent"
            ) as execute_mock
        ):
            IntentRouter.run_intent("wyłącz komputer")
            result = IntentRouter.run_intent("nie")

        self.assertEqual(result, "Anulowałem.")
        execute_mock.assert_not_called()


class TextCorrectorSafetyTests(unittest.TestCase):

    def test_allows_known_phonetic_corrections(self):

        self.assertTrue(TextCorrector.is_safe_correction(
            "hej eljan otworz krom",
            "hej elian otwórz chrome"
        ))

    def test_rejects_shortened_or_paraphrased_transcript(self):

        self.assertFalse(TextCorrector.is_safe_correction(
            "wyszukaj coś w internecie i wyszukaj w YouTubie",
            "otwórz stronę"
        ))

    def test_rejects_changed_action_or_number(self):

        self.assertFalse(TextCorrector.is_safe_correction(
            "włącz lampę na 30 procent",
            "wyłącz lampę na 50 procent"
        ))


class WeatherTests(unittest.TestCase):

    def test_city_and_future_questions_are_left_for_ai_router(self):

        self.assertIsNone(weather.run(
            "jaka jest pogoda w Warszawie"
        ))
        self.assertIsNone(weather.run(
            "jaka będzie pogoda jutro"
        ))


class ConversationFlowTests(unittest.TestCase):

    def run_start(
        self,
        listen_results,
        command_result=None,
        intent_result=None
    ):
        from contextlib import ExitStack
        from voice.settings import VoiceSettings
        from Config import get_config_value as _real_gcv

        test_settings = VoiceSettings(
            wake_enabled=False,  # legacy STT wake for headless main-loop tests
            wake_legacy_stt_fallback=True,
            session_idle_timeout=30,
            listening_ack_on_bare_wake=True,
            barge_in_enabled=False,
            tts_enabled=True,
        )

        def _test_config(section, key=None, default=None, **kwargs):
            if section == "screen_perception" and key == "enabled":
                return False
            return _real_gcv(section, key, default=default, **kwargs)

        with ExitStack() as stack:
            stack.enter_context(patch.object(main, "initialize_voice"))
            stack.enter_context(patch.object(main, "load_memory", return_value=[]))
            stack.enter_context(patch.object(main, "load_skills"))
            stack.enter_context(patch.object(main, "start_mqtt_bridge"))
            stack.enter_context(patch.object(main, "stop_mqtt_bridge"))
            stack.enter_context(patch.object(main, "start_registry"))
            stack.enter_context(
                patch.object(main, "get_voice_settings", return_value=test_settings)
            )
            stack.enter_context(patch.object(main, "get_config_value", side_effect=_test_config))
            runtime_cls = stack.enter_context(patch.object(main, "ElianRuntime"))
            stack.enter_context(patch("ui.hud_host.try_start_hud", return_value=None))
            stack.enter_context(
                patch(
                    "core.perception.screen_runtime.ScreenPerceptionRuntime.start",
                    return_value=None,
                )
            )
            stack.enter_context(patch("core.conversation_manager.clear_context"))
            listen_mock = stack.enter_context(
                patch.object(main, "listen", side_effect=listen_results)
            )
            speak_mock = stack.enter_context(patch.object(main, "speak"))
            stack.enter_context(
                patch("core.conversation_manager.run_skills", return_value=None)
            )
            command_mock = stack.enter_context(
                patch("core.conversation_manager.run_command")
            )
            intent_mock = stack.enter_context(
                patch("core.conversation_manager.run_intent")
            )
            stack.enter_context(patch("core.conversation_manager.record_action"))
            ask_ai_mock = stack.enter_context(
                patch("core.conversation_manager.ask_ai")
            )
            stack.enter_context(
                patch("core.conversation_manager.run_fast_path", return_value=None)
            )
            stack.enter_context(
                patch("core.conversation_manager.run_agent", return_value=None)
            )
            stack.enter_context(patch.object(main, "shutdown_voice"))

            runtime = MagicMock()
            runtime.proactive.try_deliver.return_value = None
            runtime_cls.return_value = runtime
            command_mock.return_value = command_result
            intent_mock.return_value = intent_result
            main.start()

        return (
            listen_mock,
            speak_mock,
            command_mock,
            intent_mock,
            ask_ai_mock
        )

    def test_silence_returns_to_standby_after_idle_timeout(self):

        listen_mock, speak_mock, _, _, _ = self.run_start([
            "hej elian",
            "",
            KeyboardInterrupt()
        ])

        self.assertEqual(
            listen_mock.call_args_list,
            [
                call(wait_timeout=None, llm_correct=False),
                call(wait_timeout=30, llm_correct=False),
                call(wait_timeout=None, llm_correct=False),
            ]
        )

        speak_mock.assert_called_once_with(
            "Słucham."
        )

    def test_command_after_wake_word_runs_immediately(self):

        (
            _,
            speak_mock,
            command_mock,
            intent_mock,
            ask_ai_mock
        ) = self.run_start(
            [
                "hej elian, otwórz kalkulator",
                KeyboardInterrupt()
            ],
            command_result="Otwieram kalkulator."
        )

        command_mock.assert_called_once_with(
            "otwórz kalkulator"
        )

        intent_mock.assert_not_called()
        ask_ai_mock.assert_not_called()
        speak_mock.assert_called_once_with(
            "Otwieram kalkulator."
        )

    def test_natural_request_uses_ai_intent_before_chat(self):

        (
            _,
            speak_mock,
            command_mock,
            intent_mock,
            ask_ai_mock
        ) = self.run_start(
            [
                "hej elian, potrzebuję Discorda",
                KeyboardInterrupt()
            ],
            intent_result="Uruchomiłem Discord."
        )

        command_mock.assert_called_once_with(
            "potrzebuję discorda"
        )
        intent_mock.assert_called_once_with(
            "potrzebuję discorda"
        )
        ask_ai_mock.assert_not_called()
        speak_mock.assert_called_once_with(
            "Uruchomiłem Discord."
        )


SAMPLE_HA_STATES = [
    {
        "entity_id": "light.vizzini",
        "state": "off",
        "attributes": {
            "friendly_name": "Lampa",
            "supported_color_modes": ["rgb", "color_temp", "brightness"],
            "min_color_temp_kelvin": 2000,
            "max_color_temp_kelvin": 6500,
            "brightness": 128,
        },
    },
    {
        "entity_id": "climate.gree_living_room",
        "state": "cool",
        "attributes": {
            "friendly_name": "Klimatyzacja",
            "hvac_modes": ["off", "cool", "heat"],
            "min_temp": 16,
            "max_temp": 30,
            "temperature": 24,
        },
    },
]


def _load_ha(states=None):
    return HomeAssistant.get_registry().load_states(states or SAMPLE_HA_STATES)


class SmartHomeTests(unittest.TestCase):

    def setUp(self):
        self.original_config = smart_home.SMART_HOME_CONFIG
        smart_home.SMART_HOME_CONFIG = {"enabled": True}
        _load_ha()

    def tearDown(self):
        smart_home.SMART_HOME_CONFIG = self.original_config
        ConversationContext.clear()

    def test_rgb_uses_light_service_domain(self):
        with (
            patch.dict(os.environ, {"ELIAN_HA_TOKEN": "test-token"}),
            patch.object(HaActions, "api_request") as request_mock,
        ):
            response = Mock()
            response.status_code = 200
            request_mock.return_value = response
            result = smart_home.run("ustaw lampę na czerwony")

        self.assertIn("czerwony", (result or "").lower())
        self.assertNotIn("chcesz ustawić", (result or "").lower())
        request_mock.assert_called_once()
        self.assertEqual(
            request_mock.call_args.args[:2],
            ("POST", "/api/services/light/turn_on"),
        )
        self.assertEqual(
            request_mock.call_args.kwargs["json"],
            {
                "entity_id": "light.vizzini",
                "rgb_color": [255, 0, 0],
            },
        )

    def test_brightness_uses_percentage(self):
        with (
            patch.dict(os.environ, {"ELIAN_HA_TOKEN": "test-token"}),
            patch.object(HaActions, "api_request") as request_mock,
        ):
            response = Mock()
            response.status_code = 200
            request_mock.return_value = response
            result = smart_home.run("ustaw jasność lampy na 45 procent")

        self.assertEqual(result, "Jasność lampa: 45 procent.")
        self.assertEqual(
            request_mock.call_args.kwargs["json"],
            {
                "entity_id": "light.vizzini",
                "brightness_pct": 45,
            },
        )

    def test_warm_white_uses_color_temperature(self):
        with (
            patch.dict(os.environ, {"ELIAN_HA_TOKEN": "test-token"}),
            patch.object(HaActions, "api_request") as request_mock,
        ):
            response = Mock()
            response.status_code = 200
            request_mock.return_value = response
            result = smart_home.run("ustaw ciepłe światło")

        self.assertEqual(result, "Ustawiam ciepłe światło.")
        self.assertEqual(
            request_mock.call_args.kwargs["json"]["color_temp_kelvin"],
            2700,
        )

    def test_list_lights_includes_supported_color_modes(self):
        lights = smart_home.list_lights()
        self.assertEqual(
            lights,
            [
                {
                    "entity_id": "light.vizzini",
                    "name": "Lampa",
                    "color_modes": ["rgb", "color_temp", "brightness"],
                }
            ],
        )

    def test_disabled_integration_does_not_call_api(self):
        smart_home.SMART_HOME_CONFIG = {"enabled": False}
        with patch.object(HaActions, "api_request") as request_mock:
            result = smart_home.run("włącz lampę")
        self.assertIn("wyłączone", result)
        request_mock.assert_not_called()

    def test_bluetooth_system_controls_vizzini_directly(self):
        smart_home.SMART_HOME_CONFIG = {
            "enabled": True,
            "system": "vizzini_bluetooth",
            "bluetooth_address": "28:DB:A7:16:D5:39",
        }
        with patch.object(smart_home, "control_light_sync") as control_mock:
            result = smart_home.run("ustaw jasność lampy na 50 procent")
        self.assertEqual(result, "Ustawiam jasność lampy na 50 procent.")
        self.assertEqual(
            [command.hex() for command in control_mock.call_args.args[0]],
            ["0000100103010000", "0000110103800000"],
        )


class DeviceRegistryTests(unittest.TestCase):

    def setUp(self):
        _load_ha()
        ConversationContext.clear()

    def tearDown(self):
        ConversationContext.clear()

    def test_klimatyzacja_resolves_to_climate(self):
        match = DeviceResolver.resolve_device("klimatyzację")
        self.assertEqual(match.status, "exact")
        self.assertEqual(match.entity.entity_id, "climate.gree_living_room")
        self.assertEqual(match.entity.domain, "climate")

    def test_lampa_resolves_to_light(self):
        match = DeviceResolver.resolve_device("lampa")
        self.assertEqual(match.entity.entity_id, "light.vizzini")
        self.assertEqual(match.entity.domain, "light")

    def test_domain_mismatch_is_rejected(self):
        light = HomeAssistant.get_registry().get("light.vizzini")
        message = DeviceResolver.reject_domain_mismatch("climate", light)
        self.assertIn("climate", message)
        self.assertIn("light", message)

    def test_unknown_device(self):
        match = DeviceResolver.resolve_device("ekspres do kawy")
        self.assertEqual(match.status, "none")

    def test_ambiguous_climate_devices(self):
        _load_ha(SAMPLE_HA_STATES + [
            {
                "entity_id": "climate.gree_bedroom",
                "state": "off",
                "attributes": {"friendly_name": "Klimatyzacja sypialnia"},
            }
        ])
        match = DeviceResolver.resolve_device("klimatyzację", domain="climate")
        self.assertEqual(match.status, "ambiguous")
        self.assertIn("czy", match.message.lower())

    def test_climate_temperature_command(self):
        with patch.object(HaActions, "api_request") as request_mock:
            request_mock.return_value = Mock(status_code=200)
            result = HaActions.execute_device_command(
                "set_temperature",
                target="klimatyzacja",
                domain="climate",
                value=22,
            )
        self.assertIn("22", result)
        self.assertEqual(
            request_mock.call_args.kwargs["json"]["entity_id"],
            "climate.gree_living_room",
        )
        self.assertEqual(
            request_mock.call_args.kwargs["json"]["temperature"],
            22,
        )

    def test_klimatyzacja_does_not_control_the_lamp(self):
        with patch.object(HaActions, "api_request") as request_mock:
            request_mock.return_value = Mock(status_code=200)
            result = HaActions.execute_device_command(
                "device_on",
                target="klimatyzacja",
                domain="climate",
            )
        self.assertIn("Klimatyzacja", result)
        self.assertEqual(
            request_mock.call_args.kwargs["json"]["entity_id"],
            "climate.gree_living_room",
        )

    def test_follow_up_temperature_uses_context(self):
        ConversationContext.update(
            last_device="climate.gree_living_room",
            last_device_name="Klimatyzacja",
            last_domain="climate",
            last_climate="climate.gree_living_room",
            last_climate_name="Klimatyzacja",
            last_action="device_on",
        )
        intent = IntentRouter._followup_intent("ustaw na 22")
        self.assertIsNotNone(intent)
        self.assertEqual(intent.action, "set_temperature")
        self.assertEqual(intent.value, 22)
        self.assertEqual(intent.domain, "climate")


class CommandUnderstandingTests(unittest.TestCase):

    def setUp(self):
        _load_ha()
        ConversationContext.clear()

    def tearDown(self):
        ConversationContext.clear()

    def _ok(self):
        return Mock(status_code=200)

    def test_followup_climate_uses_same_entity(self):
        with patch.object(HaActions, "api_request", return_value=self._ok()) as request_mock:
            first = IntentRouter.run_intent("Włącz klimatyzację")
            second = IntentRouter.run_intent("Ustaw na 22")
        self.assertIn("Klimatyzacja", first)
        self.assertIn("22", second)
        entity_ids = [
            call.kwargs["json"]["entity_id"]
            for call in request_mock.call_args_list
        ]
        self.assertTrue(all(item == "climate.gree_living_room" for item in entity_ids))
        self.assertNotIn("light.vizzini", entity_ids)

    def test_followup_light_brightness_uses_same_light(self):
        with patch.object(HaActions, "api_request", return_value=self._ok()) as request_mock:
            IntentRouter.run_intent("Włącz lampę")
            result = IntentRouter.run_intent("Jasność 35%")
        self.assertIn("35", result)
        entity_ids = [
            call.kwargs["json"]["entity_id"]
            for call in request_mock.call_args_list
        ]
        self.assertTrue(all(item == "light.vizzini" for item in entity_ids))

    def test_multi_light_temperature_and_brightness(self):
        with patch.object(HaActions, "api_request", return_value=self._ok()) as request_mock:
            result = IntentRouter.run_intent(
                "Ustaw światło na neutralne i jasność na 35%"
            )
        self.assertTrue(result)
        services = [call.args[1] for call in request_mock.call_args_list]
        self.assertEqual(len(request_mock.call_args_list), 2)
        payloads = [call.kwargs["json"] for call in request_mock.call_args_list]
        self.assertTrue(any("color_temp_kelvin" in item for item in payloads))
        self.assertTrue(any(item.get("brightness_pct") == 35 for item in payloads))
        self.assertTrue(all(item["entity_id"] == "light.vizzini" for item in payloads))

    def test_multi_climate_and_light(self):
        with patch.object(HaActions, "api_request", return_value=self._ok()) as request_mock:
            light = HomeAssistant.get_registry().get("light.vizzini")
            if light is not None:
                light.state = "on"
            result = IntentRouter.run_intent(
                "Włącz klimatyzację, ustaw na 22 i zgaś światło"
            )
        self.assertTrue(result)
        entity_ids = [
            call.kwargs["json"]["entity_id"]
            for call in request_mock.call_args_list
        ]
        self.assertIn("climate.gree_living_room", entity_ids)
        self.assertIn("light.vizzini", entity_ids)
        temps = [
            call.kwargs["json"].get("temperature")
            for call in request_mock.call_args_list
        ]
        self.assertIn(22, temps)

    def test_pronoun_ja_refers_to_climate(self):
        with patch.object(HaActions, "api_request", return_value=self._ok()) as request_mock:
            result = IntentRouter.run_intent(
                "Włącz klimatyzację i ustaw ją na 21"
            )
        self.assertIn("21", result)
        self.assertTrue(
            any(
                call.kwargs["json"].get("entity_id") == "climate.gree_living_room"
                and call.kwargs["json"].get("temperature") == 21
                for call in request_mock.call_args_list
            )
        )

    def test_relative_warmer_uses_current_temperature(self):
        ConversationContext.update(
            last_device="climate.gree_living_room",
            last_device_name="Klimatyzacja",
            last_domain="climate",
            last_climate="climate.gree_living_room",
            last_climate_name="Klimatyzacja",
        )
        with patch.object(HaActions, "api_request", return_value=self._ok()) as request_mock:
            result = IntentRouter.run_intent("Zrób o dwa stopnie cieplej")
        self.assertIn("26", result)
        self.assertEqual(
            request_mock.call_args.kwargs["json"]["temperature"],
            26,
        )
        self.assertEqual(
            request_mock.call_args.kwargs["json"]["entity_id"],
            "climate.gree_living_room",
        )

    def test_jest_mi_zimno_raises_cooling_setpoint(self):
        ConversationContext.update(
            last_device="climate.gree_living_room",
            last_device_name="Klimatyzacja",
            last_domain="climate",
            last_climate="climate.gree_living_room",
            last_climate_name="Klimatyzacja",
        )
        # Mark climate as cooling with target 24 in registry.
        _load_ha([
            {
                "entity_id": "light.vizzini",
                "state": "off",
                "attributes": {
                    "friendly_name": "Lampa",
                    "supported_color_modes": ["rgb", "color_temp", "brightness"],
                },
            },
            {
                "entity_id": "climate.gree_living_room",
                "state": "cool",
                "attributes": {
                    "friendly_name": "Klimatyzacja",
                    "hvac_modes": ["off", "cool", "heat"],
                    "min_temp": 16,
                    "max_temp": 30,
                    "temperature": 24,
                },
            },
        ])
        with patch.object(HaActions, "api_request", return_value=self._ok()) as request_mock:
            result = IntentRouter.run_intent("Jest mi zimno")
        self.assertIn("26", result)
        self.assertEqual(
            request_mock.call_args.kwargs["json"]["entity_id"],
            "climate.gree_living_room",
        )

    def test_ambiguous_device_asks_without_execution(self):
        _load_ha(SAMPLE_HA_STATES + [
            {
                "entity_id": "climate.gree_bedroom",
                "state": "off",
                "attributes": {"friendly_name": "Klimatyzacja sypialnia"},
            }
        ])
        with patch.object(HaActions, "api_request") as request_mock:
            result = IntentRouter.run_intent("Włącz klimatyzację")
        self.assertIn("czy", result.lower())
        request_mock.assert_not_called()

    def test_unknown_device_does_not_fallback_to_lamp(self):
        with patch.object(HaActions, "api_request") as request_mock:
            result = HaActions.execute_device_command(
                "device_on",
                target="ekspres do kawy",
                domain="switch",
            )
        self.assertIn("Nie znalazłem", result)
        request_mock.assert_not_called()


def _ha_temperature_payload(request_mock):
    for call in request_mock.call_args_list:
        payload = (call.kwargs or {}).get("json") or {}
        if "temperature" in payload:
            return payload["temperature"]
    return None


class ClimateTemperatureUnitTests(unittest.TestCase):

    def setUp(self):
        ConversationContext.clear()
        self.fahrenheit_states = [
            {
                "entity_id": "climate.gree_living_room",
                "state": "cool",
                "attributes": {
                    "friendly_name": "Klimatyzacja",
                    "temperature_unit": "°F",
                    "hvac_modes": ["off", "cool", "heat"],
                    "min_temp": 60,
                    "max_temp": 86,
                    "temperature": 72,
                },
            }
        ]
        HomeAssistant.get_registry().load_states(self.fahrenheit_states)

    def tearDown(self):
        ConversationContext.clear()
        _load_ha()

    def test_celsius_spoken_value_converted_to_fahrenheit(self):
        with patch.object(HaActions, "api_request", return_value=Mock(status_code=200)) as request_mock:
            result = HaActions.execute_device_command(
                "set_temperature",
                target="klimatyzacja",
                domain="climate",
                value=22,
            )
        self.assertEqual(
            _ha_temperature_payload(request_mock),
            72.0,
        )
        self.assertIn("22", result)

    def test_fahrenheit_entity_temperature_spoken_in_celsius(self):
        result = HaActions.execute_on_entity(
            "device_status",
            HomeAssistant.get_registry().get("climate.gree_living_room"),
        )
        self.assertIn("22,2", result)

    def test_relative_plus_two_celsius_on_fahrenheit_entity(self):
        with patch.object(HaActions, "api_request", return_value=Mock(status_code=200)) as request_mock:
            result = HaActions.execute_device_command(
                "adjust_temperature",
                target="klimatyzacja",
                domain="climate",
                value=2,
            )
        # 72°F ≈ 22.22°C + 2°C → 24.22°C → 75.6°F → nearest 1°F = 76°F
        self.assertEqual(
            _ha_temperature_payload(request_mock),
            76.0,
        )
        self.assertIn("24", result)


class VoiceLoopTests(unittest.TestCase):

    def test_speak_waits_for_playback_then_cools_down(self):
        fake_proc = MagicMock()
        fake_proc.returncode = 0
        fake_proc.communicate = MagicMock(return_value=("", ""))
        with (
            patch.object(Voice, "PIPER", "piper.exe"),
            patch.object(Voice, "VOICE_MODEL", "voice.onnx"),
            patch.object(Voice.os.path, "isfile", return_value=True),
            patch.object(Voice.shutil, "which", return_value="piper.exe"),
            patch.object(Voice.subprocess, "Popen", return_value=fake_proc),
            patch.object(Voice.os.path, "exists", return_value=True),
            patch.object(Voice.sf, "read", return_value=([0.0], 22050)),
            patch.object(Voice.sd, "play"),
            patch.object(Voice.sd, "wait") as wait_mock,
            patch.object(Voice.os, "remove"),
            patch.object(Voice.os, "makedirs"),
            patch.object(Voice.time, "sleep") as sleep_mock,
        ):
            Voice.speak("Słucham.", cooldown=0.45)

        wait_mock.assert_called_once()
        sleep_mock.assert_called_once_with(0.45)

    def test_end_phrase_is_exact_not_substring(self):
        self.assertTrue(main.should_end_conversation("koniec"))
        self.assertFalse(
            main.should_end_conversation("na koniec dnia zrób herbatę")
        )


class SkillRoutingTests(unittest.TestCase):

    def test_cpu_and_ram_substrings_do_not_match(self):
        self.assertFalse(System.match("opowiedz mi o cpu w kosmosie"))
        self.assertFalse(System.match("program ramowy na wieczór"))
        self.assertTrue(System.match("ile mam ramu"))

    def test_wycisz_does_not_match_unrelated_speech(self):
        self.assertFalse(Music.match("nie wyciszaj tej rozmowy o pogodzie"))
        self.assertTrue(Music.match("wycisz"))


class MessagingTests(unittest.TestCase):

    def test_unconfigured_provider_does_not_pretend_success(self):
        from Integrations import send_message
        from Contacts import Contact
        result = send_message(
            Contact(name="Kuba"),
            "będę za 10 minut",
        )
        self.assertIn("Nie mam jeszcze skonfigurowanej", result)


class NaturalConversationTests(unittest.TestCase):

    def setUp(self):
        ConversationContext.clear()
        from AppRegistry import reset_registry_cache
        reset_registry_cache()

    def tearDown(self):
        ConversationContext.clear()
        from AppRegistry import reset_registry_cache
        reset_registry_cache()

    def test_wait_phrase_extends_idle_timeout(self):
        from ConversationFlow import (
            get_effective_idle_timeout,
            handle_wait_phrase,
            is_wait_phrase,
        )

        self.assertTrue(is_wait_phrase("daj mi chwilę"))
        self.assertTrue(is_wait_phrase("poczekaj"))
        handle_wait_phrase()
        self.assertGreater(get_effective_idle_timeout(12), 12)

    def test_wait_phrase_keeps_conversation_active_in_main_loop(self):
        from contextlib import ExitStack
        from voice.settings import VoiceSettings
        from Config import get_config_value as _real_gcv

        test_settings = VoiceSettings(
            wake_enabled=False,
            wake_legacy_stt_fallback=True,
            session_idle_timeout=30,
            barge_in_enabled=False,
        )

        def _test_config(section, key=None, default=None, **kwargs):
            if section == "screen_perception" and key == "enabled":
                return False
            return _real_gcv(section, key, default=default, **kwargs)

        with ExitStack() as stack:
            stack.enter_context(patch.object(main, "initialize_voice"))
            stack.enter_context(patch.object(main, "load_memory", return_value=[]))
            stack.enter_context(patch.object(main, "load_skills"))
            stack.enter_context(patch.object(main, "start_mqtt_bridge"))
            stack.enter_context(patch.object(main, "stop_mqtt_bridge"))
            stack.enter_context(patch.object(main, "start_registry"))
            stack.enter_context(
                patch.object(main, "get_voice_settings", return_value=test_settings)
            )
            stack.enter_context(patch.object(main, "get_config_value", side_effect=_test_config))
            runtime_cls = stack.enter_context(patch.object(main, "ElianRuntime"))
            stack.enter_context(patch("ui.hud_host.try_start_hud", return_value=None))
            stack.enter_context(
                patch(
                    "core.perception.screen_runtime.ScreenPerceptionRuntime.start",
                    return_value=None,
                )
            )
            stack.enter_context(patch("core.conversation_manager.clear_context"))
            listen_mock = stack.enter_context(
                patch.object(
                    main,
                    "listen",
                    side_effect=[
                        "hej elian",
                        "daj mi chwilę",
                        "billie jean",
                        KeyboardInterrupt(),
                    ],
                )
            )
            stack.enter_context(patch.object(main, "speak"))
            stack.enter_context(
                patch("core.conversation_manager.run_skills", return_value=None)
            )
            stack.enter_context(
                patch("core.conversation_manager.run_command", return_value=None)
            )
            intent_mock = stack.enter_context(
                patch(
                    "core.conversation_manager.run_intent",
                    return_value="Odtwarzam Billie Jean.",
                )
            )
            stack.enter_context(patch("core.conversation_manager.record_action"))
            stack.enter_context(patch("core.conversation_manager.ask_ai"))
            stack.enter_context(
                patch("core.conversation_manager.run_fast_path", return_value=None)
            )
            stack.enter_context(
                patch("core.conversation_manager.run_agent", return_value=None)
            )
            stack.enter_context(patch.object(main, "shutdown_voice"))
            runtime = MagicMock()
            runtime.proactive.try_deliver.return_value = None
            runtime_cls.return_value = runtime
            main.start()

        timeouts = [
            call.kwargs.get("wait_timeout")
            for call in listen_mock.call_args_list
        ]
        self.assertIsNone(timeouts[0])
        self.assertGreater(timeouts[2], 12)
        intent_mock.assert_called_once_with("billie jean")

    def test_realtek_is_not_music_service(self):
        from AppRegistry import AppEntry, classify_app, registry_for_tests, find_music_apps

        registry_for_tests([
            AppEntry("spotify", "spotify.lnk", {"music"}, 100),
            AppEntry("realtek audio console", "realtek.lnk", set(), 0),
            AppEntry("nvidia audio", "nvidia.lnk", set(), 0),
        ])
        music_apps = find_music_apps()
        names = [app.name for app in music_apps]
        self.assertIn("spotify", names)
        self.assertNotIn("realtek audio console", names)
        self.assertFalse(classify_app("realtek audio console").capabilities)

    def test_music_request_uses_installed_service(self):
        from AppRegistry import AppEntry, registry_for_tests, reset_registry_cache
        from GoalRouter import handle_goal

        registry_for_tests([
            AppEntry("spotify", "spotify.lnk", {"music"}, 100),
        ])
        result = handle_goal("chcę posłuchać muzyki")
        self.assertIn("Czego chcesz posłuchać", result)
        pending = ConversationContext.get_pending_goal()
        self.assertEqual(pending["type"], "music")
        self.assertEqual(pending["stage"], "awaiting_track")

        with patch("GoalRouter.open_music_service", return_value=(True, "Szukam Billie Jean.")) as open_mock:
            follow_up = handle_goal("billie jean")
        open_mock.assert_called_once()
        self.assertIn("Billie Jean", follow_up)

    def test_gaming_clarification_preserves_context(self):
        from AppRegistry import AppEntry, registry_for_tests
        from GoalRouter import handle_goal

        registry_for_tests([
            AppEntry("fortnite", "fortnite.lnk", {"game"}, 50),
        ])
        first = handle_goal("chcę pograć")
        self.assertIn("W co chcesz zagrać", first)

        with patch("GoalRouter.launch_entry", return_value=(True, "Uruchamiam fortnite.")) as launch_mock:
            second = handle_goal("fortnite")
        launch_mock.assert_called_once()
        self.assertIn("fortnite", second.lower())

    def test_steam_and_epic_launchers_are_detected(self):
        from AppRegistry import AppEntry, find_game_launchers, registry_for_tests

        registry_for_tests([
            AppEntry("steam", "steam.lnk", {"game_launcher"}, 100),
            AppEntry("epic games launcher", "epic.lnk", {"game_launcher"}, 95),
        ])
        launchers = [entry.name for entry in find_game_launchers()]
        self.assertIn("steam", launchers)
        self.assertIn("epic games launcher", launchers)

    def test_recipe_request_uses_web_research(self):
        from WebResearch import needs_web_research, research

        self.assertTrue(needs_web_research("podaj mi przepis na drożdżówkę"))
        with patch("WebResearch._collect_snippets", return_value=[{
            "title": "Przepis",
            "url": "https://example.com/przepis",
            "text": "Składniki: mąka, drożdże. Ugniatamy ciasto i pieczemy.",
        }]):
            with patch("WebResearch._synthesize", return_value="Potrzebujesz mąki i drożdży. Ugnieć ciasto i upiecz."):
                answer = research("przepis na drożdżówkę")
        self.assertIn("mąki", answer.lower())
        self.assertNotIn("google.com/search", answer.lower())

    def test_current_information_question_triggers_web_research(self):
        from IntentRouter import run_intent
        from WebResearch import needs_web_research

        self.assertTrue(needs_web_research("co jest teraz na czasie"))
        with patch("IntentRouter.research", return_value="Dziś popularne są nowe telefony.") as research_mock:
            result = run_intent("co jest teraz na czasie")
        research_mock.assert_called_once()
        self.assertIn("popularne", result.lower())

    def test_web_clarification_preserves_research_intent(self):
        from GoalRouter import handle_goal

        first = handle_goal("sprawdź coś w internecie")
        self.assertIn("Co mam sprawdzić", first)
        with patch("WebResearch.research", return_value="Sztuczna inteligencja jest dziś bardzo popularna.") as research_mock:
            second = handle_goal("co jest teraz popularne w technologii")
        research_mock.assert_called_once_with("co jest teraz popularne w technologii")
        self.assertIn("popularna", second.lower())


if __name__ == "__main__":

    unittest.main()
