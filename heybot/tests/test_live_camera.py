import tempfile
import threading
import time
import unittest
from pathlib import Path
from unittest.mock import patch

from heybot.live_camera import JpegFrames, LiveCamera
from heybot.vision import AssistantError

FIRST = b"\xff\xd8first image\xff\xd9"
SECOND = b"\xff\xd8second image\xff\xd9"


class FrameParserTests(unittest.TestCase):
    def test_jpeg_boundaries_can_cross_pipe_chunks(self):
        parser = JpegFrames()
        self.assertEqual(parser.feed(b"noise" + FIRST[:1]), [])
        self.assertEqual(parser.feed(FIRST[1:-1]), [])
        self.assertEqual(parser.feed(FIRST[-1:] + SECOND), [FIRST, SECOND])

    def test_malformed_stream_does_not_grow_memory_without_limit(self):
        parser = JpegFrames()
        with patch("heybot.live_camera.MAX_FRAME", 32):
            self.assertEqual(parser.feed(b"\xff\xd8" + b"x" * 64), [])
        self.assertLessEqual(len(parser.buffer), 1)
        self.assertEqual(parser.feed(SECOND), [SECOND])

    def test_noise_before_and_between_frames_is_discarded(self):
        parser = JpegFrames()
        self.assertEqual(parser.feed(b"junk" + FIRST + b"junk" + SECOND + b"junk"), [FIRST, SECOND])


class SharedCameraTests(unittest.TestCase):
    def setUp(self):
        # A zero lease keeps the real producer asleep; frames are supplied explicitly.
        self.camera = LiveCamera(idle_timeout=0)
        self.addCleanup(self.camera.close)

    def test_initial_camera_is_idle_until_requested(self):
        self.assertEqual(self.camera.info()["status"], "standby")
        self.assertIsNone(self.camera.info()["pid"])

    def test_multiple_readers_share_the_same_latest_frame(self):
        self.camera._publish(FIRST)
        results = []
        threads = [threading.Thread(target=lambda: results.append(self.camera.get_frame(timeout=0))) for _ in range(6)]
        for thread in threads: thread.start()
        for thread in threads: thread.join(timeout=1)
        self.assertEqual(results, [(FIRST, 1)] * 6)

    def test_capture_waits_for_a_new_frame_and_saves_it(self):
        self.camera._publish(FIRST)
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "photo.jpg"
            errors = []
            def capture():
                try: self.camera.capture(path)
                except Exception as error: errors.append(error)
            thread = threading.Thread(target=capture)
            thread.start()
            time.sleep(.03)
            self.assertFalse(path.exists())
            self.camera._publish(SECOND)
            thread.join(timeout=1)
            self.assertFalse(thread.is_alive())
            self.assertEqual(errors, [])
            self.assertEqual(path.read_bytes(), SECOND)

    def test_a_stale_frame_is_not_reused(self):
        self.camera._publish(FIRST)
        self.camera.last_frame_at = time.monotonic() - 3
        with self.assertRaises(AssistantError):
            self.camera.get_frame(timeout=0)

    def test_close_wakes_waiting_capture(self):
        self.camera.close()
        with self.assertRaises(AssistantError):
            self.camera.get_frame(timeout=.1)


if __name__ == "__main__": unittest.main()
