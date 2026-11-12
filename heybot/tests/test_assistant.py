import io
import json
import tempfile
import unittest
import urllib.error
from pathlib import Path
from unittest.mock import Mock

from heybot.app import Assistant
from heybot.commands import CommandGate
from heybot.hardware import audio_devices, recognized_text
from heybot.vision import AssistantError, GroqVision


class WakeCommandTests(unittest.TestCase):
    def test_capture_requires_wake(self):
        gate = CommandGate()
        for text in ("click photo", "take picture", "repeat", "not hey bot click photo", ""):
            self.assertIsNone(gate.accept(text, 100))

    def test_wake_then_command_once(self):
        gate = CommandGate()
        self.assertEqual(gate.accept("Hey bot!", 100), "wake")
        self.assertEqual(gate.accept("click photo", 102), "photo")
        self.assertIsNone(gate.accept("click photo", 103))

    def test_combined_command_and_alias(self):
        for wake in ("hey bot", "hey boat", "heybot"):
            self.assertEqual(CommandGate().accept(wake + " take a picture", 100), "photo")

    def test_timeout_and_cancel(self):
        gate = CommandGate()
        gate.accept("hey bot", 100)
        self.assertIsNone(gate.accept("click photo", 115))
        gate.accept("hey bot", 120)
        self.assertEqual(gate.accept("cancel", 121), "cancel")
        self.assertIsNone(gate.accept("click photo", 122))

    def test_unrecognized_speech_never_captures(self):
        gate = CommandGate()
        gate.accept("hey bot", 100)
        for text in ("do not click photo", "[unk] click photo", "click photo please maybe", "noise"):
            self.assertIsNone(gate.accept(text, 102))


class IntegrationWithFakesTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.camera, self.vision, self.speaker = Mock(), Mock(), Mock()
        self.camera.capture.side_effect = lambda path: Path(path).write_bytes(b"photo")
        self.vision.describe.return_value = "A chair is on the left."
        self.bot = Assistant(self.vision, self.camera, self.speaker, self.temp.name, clock=lambda: 100)

    def test_no_photo_at_boot_or_without_wake(self):
        self.bot.handle("click photo")
        self.camera.capture.assert_not_called()
        self.vision.describe.assert_not_called()

    def test_capture_description_repeat_and_photo_cleanup(self):
        self.bot.handle("hey bot click photo")
        self.camera.capture.assert_called_once()
        path = self.camera.capture.call_args.args[0]
        self.assertFalse(path.exists())
        self.speaker.say.assert_any_call("A chair is on the left.")
        self.bot.handle("hey bot repeat")
        self.assertEqual(self.vision.describe.call_count, 1)
        self.assertEqual(self.camera.capture.call_count, 1)

    def test_api_failure_also_removes_photo_and_does_not_repeat_capture(self):
        self.vision.describe.side_effect = AssistantError("Please check the internet connection.")
        self.bot.handle("hey bot click photo")
        self.assertFalse(self.camera.capture.call_args.args[0].exists())
        self.assertIsNone(self.bot.last_description)
        self.bot.handle("click photo")
        self.assertEqual(self.camera.capture.call_count, 1)

    def test_camera_failure_does_not_upload(self):
        self.camera.capture.side_effect = AssistantError("Camera disconnected.")
        self.bot.handle("hey bot click photo")
        self.vision.describe.assert_not_called()
        self.assertEqual(list(Path(self.temp.name).iterdir()), [])

    def test_listening_window_starts_after_acknowledgement(self):
        now = [100]
        self.bot.clock = lambda: now[0]
        self.speaker.say.side_effect = lambda _: now.__setitem__(0, now[0] + 7)
        self.bot.handle("hey bot")
        self.assertEqual(self.bot.gate.armed_until, 122)


class ClientTests(unittest.TestCase):
    def test_real_image_payload_and_response(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "picture.png"
            path.write_bytes(b"\x89PNG\r\n\x1a\nfixture")
            calls = []
            def open_request(request, timeout):
                calls.append(request)
                return io.BytesIO(json.dumps({"choices": [{"message": {"content": "A red square."}}]}).encode())
            client = GroqVision("dummy-secret", opener=open_request)
            self.assertEqual(client.describe(path), "A red square.")
            request = calls[0]
            self.assertEqual(request.full_url, "https://api.groq.com/openai/v1/chat/completions")
            data = json.loads(request.data)
            self.assertTrue(data["messages"][0]["content"][1]["image_url"]["url"].startswith("data:image/png;base64,"))
            self.assertNotIn("dummy-secret", request.data.decode())

    def test_api_errors_do_not_expose_upstream_body_or_key(self):
        for status in (400, 401, 403, 404, 429, 500):
            def failing(request, timeout):
                raise urllib.error.HTTPError(request.full_url, status, "dummy-secret", {}, io.BytesIO(b"dummy-secret"))
            with self.assertRaises(AssistantError) as caught:
                GroqVision("dummy-secret", opener=failing).check_model()
            self.assertNotIn("dummy-secret", str(caught.exception))

    def test_invalid_api_response(self):
        client = GroqVision("dummy", opener=lambda *_args, **_kwargs: io.BytesIO(b"not-json"))
        with self.assertRaises(AssistantError):
            client.check_model()

    def test_missing_key_and_invalid_photo(self):
        with self.assertRaises(AssistantError):
            GroqVision("")
        with self.assertRaises(AssistantError):
            GroqVision("dummy").describe("does-not-exist.jpg")


class AudioTests(unittest.TestCase):
    def test_alsa_uses_stable_names_and_correct_direction(self):
        with tempfile.TemporaryDirectory() as folder:
            proc = Path(folder)
            (proc / "pcm").write_text("00-00: Headphones : playback 1\n02-00: USB Audio : playback 1 : capture 1\n")
            for index, name in ((0, "Headphones"), (2, "Device")):
                (proc / f"card{index}").mkdir()
                (proc / f"card{index}/id").write_text(name)
            self.assertEqual(audio_devices("capture", proc), [("Device", "plughw:CARD=Device,DEV=0")])
            self.assertEqual(len(audio_devices("playback", proc)), 2)

    def test_low_confidence_speech_is_ignored(self):
        for result in ({"text": "hey bot", "result": [{"conf": .2}]}, {"text": "hey bot"}, {}):
            self.assertEqual(recognized_text(json.dumps(result)), "")
        self.assertEqual(recognized_text(json.dumps({"text": "hey bot", "result": [{"conf": .9}]})), "hey bot")


if __name__ == "__main__":
    unittest.main()
