import json
import os
import queue
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock

from heybot.app import Assistant, STOP, voice_loop
from heybot.dashboard import CommandQueue, create_app, private_secret
from heybot.history import History
from heybot.vision import AssistantError

JPEG = b"\xff\xd8\xff" + b"test image"


class HistoryTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.history = History(self.temp.name, limit=3)
        self.addCleanup(lambda: self.history.close())

    def test_persistent_photo_response_and_conversation(self):
        run = self.history.start("hey bot click photo", "web")
        source = Path(self.temp.name) / "source.jpg"
        source.write_bytes(JPEG)
        self.history.save_photo(run, source)
        self.history.event(run, "assistant", "A chair.")
        self.history.finish(run, "A chair.")
        self.assertEqual(self.history.photo_path(run).read_bytes(), JPEG)
        self.assertEqual(self.history.last_description(), "A chair.")
        self.assertEqual(len(self.history.get(run)["events"]), 2)
        other = History(self.temp.name)
        try:
            self.assertEqual(other.get(run)["response"], "A chair.")
        finally:
            other.close()

    def test_retention_removes_old_sessions_and_photos(self):
        ids = []
        source = Path(self.temp.name) / "source.jpg"; source.write_bytes(JPEG)
        for i in range(6):
            run = self.history.start(str(i)); ids.append(run)
            self.history.save_photo(run, source)
            self.history.finish(run, "Description.")
        self.assertIsNone(self.history.get(ids[0]))
        self.assertFalse(self.history.photo_path(ids[0]).exists())
        self.assertEqual(len(self.history.snapshot()["runs"]), 3)

    def test_photo_budget_keeps_text_history(self):
        self.history.photo_budget = len(JPEG)
        source = Path(self.temp.name) / "source.jpg"; source.write_bytes(JPEG)
        first = self.history.start("first"); self.history.save_photo(first, source); self.history.finish(first)
        second = self.history.start("second"); self.history.save_photo(second, source); self.history.finish(second)
        self.assertEqual(self.history.get(first)["photo"], 0)
        self.assertEqual(self.history.get(second)["photo"], 1)

    def test_delete_refuses_active_session(self):
        run = self.history.start("click photo")
        with self.assertRaises(ValueError):
            self.history.delete(run)
        self.history.finish(run)
        self.assertTrue(self.history.delete(run))
        self.assertIsNone(self.history.get(run))

    def test_restart_marks_active_work_interrupted(self):
        run = self.history.start("click photo")
        other = History(self.temp.name)
        try:
            self.assertEqual(other.get(run)["status"], "interrupted")
        finally:
            other.close()

    def test_history_pagination(self):
        self.history.limit = 100
        for i in range(55):
            run = self.history.start(str(i)); self.history.finish(run)
        first = self.history.snapshot()
        second = self.history.snapshot(first["next_cursor"])
        self.assertEqual(len(first["runs"]), 50)
        self.assertEqual(len(second["runs"]), 5)
        self.assertFalse({r["id"] for r in first["runs"]} & {r["id"] for r in second["runs"]})


class DashboardTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(); self.addCleanup(self.temp.cleanup)
        self.history = History(self.temp.name); self.addCleanup(self.history.close)
        self.commands = CommandQueue(self.history)
        self.app = create_app(self.history, self.commands, "test-password", "test-session-secret")
        self.app.config["TESTING"] = True
        self.client = self.app.test_client()

    def login(self):
        self.client.get("/login")
        with self.client.session_transaction() as session:
            token = session["csrf"]
        response = self.client.post("/login", data={"csrf": token, "password": "test-password"})
        self.assertEqual(response.status_code, 302)
        with self.client.session_transaction() as session:
            return {"X-CSRF-Token": session["csrf"]}

    def test_history_and_photos_require_login(self):
        self.assertEqual(self.client.get("/").status_code, 302)
        self.assertEqual(self.client.get("/api/state").status_code, 401)
        self.assertEqual(self.client.get("/photos/12345678-1234-1234-1234-123456789abc.jpg").status_code, 401)

    def test_authenticated_ui_and_csrf(self):
        headers = self.login()
        response = self.client.get("/")
        self.assertIn(b"Live activity", response.data)
        self.assertEqual(response.headers["Cache-Control"], "no-store")
        self.assertEqual(self.client.post("/api/commands", json={"command": "click photo"}).status_code, 403)
        sent = self.client.post("/api/commands", json={"command": "click photo", "speak": False}, headers=headers)
        self.assertEqual(sent.status_code, 202)
        run_id, text, speak = self.commands.pop()
        self.assertEqual(text, "hey bot click photo"); self.assertFalse(speak)
        self.assertEqual(self.history.get(run_id)["status"], "queued")

    def test_live_preview_is_authenticated_and_uses_shared_camera(self):
        camera = Mock()
        camera.info.return_value = {"status": "live", "sequence": 42, "fps": 8}
        camera.get_frame.return_value = (JPEG, 42)
        client = create_app(self.history, self.commands, "test", "test", camera=camera).test_client()
        self.assertEqual(client.get("/api/camera/frame").status_code, 401)
        camera.get_frame.assert_not_called()
        with client.session_transaction() as session:
            session["authenticated"] = True
        frame = client.get("/api/camera/frame")
        self.assertEqual(frame.data, JPEG)
        self.assertEqual(frame.headers["X-Frame-Sequence"], "42")
        self.assertEqual(frame.headers["Cache-Control"], "no-store")
        self.assertEqual(frame.mimetype, "image/jpeg")
        state = client.get("/api/state").get_json()
        self.assertEqual(state["camera"]["fps"], 8)
        self.assertIn("processes", state["device"])

    def test_unavailable_camera_does_not_break_dashboard(self):
        camera = Mock()
        camera.info.return_value = {"status": "unavailable"}
        camera.get_frame.side_effect = AssistantError("Camera disconnected.")
        client = create_app(self.history, self.commands, "test", "test", camera=camera).test_client()
        with client.session_transaction() as session:
            session["authenticated"] = True
        self.assertEqual(client.get("/api/camera/frame").status_code, 503)
        self.assertEqual(client.get("/").status_code, 200)
        self.assertEqual(client.get("/api/state").status_code, 200)

    def test_no_arbitrary_commands_or_paths(self):
        headers = self.login()
        for command in ("rm -rf /", "/etc/passwd", "click photo; reboot", None, "x" * 201):
            self.assertEqual(self.client.post("/api/commands", json={"command": command}, headers=headers).status_code, 400)
        self.assertEqual(self.client.get("/photos/../../etc/passwd").status_code, 404)

    def test_authentication_rate_limit(self):
        self.client.get("/login")
        with self.client.session_transaction() as session:
            token = session["csrf"]
        for i in range(5):
            self.assertEqual(self.client.post("/login", data={"csrf": token, "password": "wrong"}).status_code, 200)
        self.assertEqual(self.client.post("/login", data={"csrf": token, "password": "wrong"}).status_code, 429)

    def test_queue_is_bounded(self):
        for _ in range(8): self.commands.submit("click photo")
        with self.assertRaises(queue.Full): self.commands.submit("click photo")
        self.assertEqual(len(self.history.snapshot()["runs"]), 8)

    def test_secret_file_is_private_and_stable(self):
        path = Path(self.temp.name) / "password"
        value = private_secret(path)
        self.assertEqual(value, private_secret(path))
        self.assertEqual(path.stat().st_mode & 0o777, 0o600)

    def test_completed_photo_can_be_viewed_and_deleted(self):
        headers = self.login()
        run = self.history.start("click photo")
        source = Path(self.temp.name) / "source.jpg"; source.write_bytes(JPEG)
        self.history.save_photo(run, source); self.history.finish(run, "A chair.")
        with self.client.get("/photos/" + run + ".jpg") as response:
            self.assertEqual(response.data, JPEG)
            self.assertEqual(response.mimetype, "image/jpeg")
        self.assertEqual(self.client.post("/api/sessions/" + run + "/delete", headers=headers).status_code, 200)
        self.assertEqual(self.client.get("/photos/" + run + ".jpg").status_code, 404)

    def test_photos_work_with_a_relative_state_directory(self):
        history = History(os.path.relpath(Path(self.temp.name) / "relative-state"))
        try:
            self.assertTrue(history.photos.is_absolute())
            run = history.start("click photo")
            source = Path(self.temp.name) / "source.jpg"; source.write_bytes(JPEG)
            history.save_photo(run, source); history.finish(run)
            client = create_app(history, CommandQueue(history), "test", "test").test_client()
            with client.session_transaction() as session:
                session["authenticated"] = True
            with client.get("/photos/" + run + ".jpg") as response:
                self.assertEqual(response.data, JPEG)
        finally:
            history.close()


class GpioConfigurationTests(unittest.TestCase):
    def test_managed_block_preserves_camera_configuration_and_is_idempotent(self):
        from tools.configure_gpio_audio import replace_block
        original = "camera_auto_detect=1\ndtoverlay=ov5647\n[pi4]\nmax_framebuffers=2\n"
        once = replace_block(original, "[all]\ndtoverlay=googlevoicehat-soundcard")
        self.assertTrue(once.startswith(original))
        self.assertEqual(once, replace_block(once, "[all]\ndtoverlay=googlevoicehat-soundcard"))

    def test_incomplete_block_is_not_silently_overwritten(self):
        from tools.configure_gpio_audio import BEGIN, replace_block
        with self.assertRaises(ValueError):
            replace_block("camera_auto_detect=1\n" + BEGIN, "anything")

    def test_private_configuration_replacement_keeps_key_and_permissions(self):
        from tools.configure_gpio_audio import atomic_private
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "config"
            text = "GROQ_API_KEY=dummy-secret\nHEYBOT_INPUT_DEVICE=heybot_gpio_mic\n"
            atomic_private(path, text)
            self.assertEqual(path.read_text(), text)
            self.assertEqual(path.stat().st_mode & 0o777, 0o600)


class WorkerTests(unittest.TestCase):
    def test_missing_speaker_does_not_block_photo_and_history(self):
        with tempfile.TemporaryDirectory() as directory:
            history = History(directory)
            try:
                speaker = Mock(); speaker.say.side_effect = AssistantError("Speaker unavailable.")
                camera = Mock(); camera.capture.side_effect = lambda p: Path(p).write_bytes(JPEG)
                vision = Mock(); vision.describe.return_value = "A desk."
                bot = Assistant(vision, camera, speaker, directory, clock=lambda: 100, history=history)
                bot.handle("hey bot click photo", source="web")
                run = history.snapshot()["runs"][0]
                self.assertEqual(run["status"], "done")
                self.assertEqual(run["response"], "A desk.")
                self.assertEqual(run["photo"], 1)
                self.assertTrue(any(e["kind"] == "audio_error" for e in history.get(run["id"])["events"]))
                again = Assistant(vision, camera, speaker, directory, history=history)
                self.assertEqual(again.last_description, "A desk.")
            finally: history.close()

    def test_web_queue_runs_without_microphone_or_vosk(self):
        with tempfile.TemporaryDirectory() as directory:
            history = History(directory)
            try:
                commands = CommandQueue(history); commands.submit("help", False)
                bot = Assistant(Mock(), Mock(), Mock(), directory, history=history)
                original = bot.handle
                def handle(*args, **kwargs):
                    original(*args, **kwargs); STOP.set()
                bot.handle = handle
                STOP.clear()
                voice_loop(bot, Path(directory) / "missing-model", "auto", commands)
                self.assertEqual(history.snapshot()["runs"][0]["status"], "done")
            finally:
                STOP.clear(); history.close()


if __name__ == "__main__": unittest.main()
