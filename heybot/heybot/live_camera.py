"""One camera owner for authenticated live preview and explicit photo captures."""
import os
import selectors
import shutil
import subprocess
import threading
import time
from collections import deque
from pathlib import Path

from .vision import AssistantError

MAX_FRAME = 4_000_000


class JpegFrames:
    def __init__(self):
        self.buffer = bytearray()

    def feed(self, data):
        self.buffer.extend(data)
        frames = []
        while True:
            start = self.buffer.find(b"\xff\xd8")
            if start < 0:
                self.buffer[:] = self.buffer[-1:]
                break
            if start:
                del self.buffer[:start]
            end = self.buffer.find(b"\xff\xd9", 2)
            if end < 0:
                if len(self.buffer) > MAX_FRAME:
                    self.buffer.clear()
                break
            end += 2
            if end <= MAX_FRAME:
                frames.append(bytes(self.buffer[:end]))
            del self.buffer[:end]
        return frames


class LiveCamera:
    width, height, target_fps = 1280, 960, 8

    def __init__(self, idle_timeout=15, clock=time.monotonic):
        self.clock = clock
        self.idle_timeout = idle_timeout
        self.condition = threading.Condition()
        self.closed = False
        self.last_demand = float("-inf")
        self.last_frame_at = 0
        self.frame = None
        self.sequence = 0
        self.times = deque(maxlen=30)
        self.process = None
        self.status = "standby"
        self.error = None
        self.thread = threading.Thread(target=self._run, name="camera-feed", daemon=True)
        self.thread.start()

    def info(self):
        with self.condition:
            age = self.clock() - self.last_frame_at if self.frame else None
            fps = ((len(self.times) - 1) / (self.times[-1] - self.times[0])
                   if len(self.times) > 1 and self.times[-1] > self.times[0] else 0)
            return {"status": self.status, "error": self.error, "sequence": self.sequence,
                    "frame_age_seconds": round(age, 2) if age is not None else None,
                    "fps": round(fps, 1), "width": self.width, "height": self.height,
                    "pid": self.process.pid if self.process and self.process.poll() is None else None}

    def get_frame(self, timeout=.15, after=None):
        deadline = self.clock() + timeout
        with self.condition:
            if self.closed:
                raise AssistantError("The camera is stopped.")
            self.last_demand = self.clock()
            self.condition.notify_all()
            while True:
                if (self.frame is not None and self.clock() - self.last_frame_at < 1.5
                        and (after is None or self.sequence > after)):
                    return self.frame, self.sequence
                remaining = deadline - self.clock()
                if remaining <= 0 or self.closed:
                    raise AssistantError(self.error or "Camera is starting. Please wait a moment.")
                self.condition.wait(min(remaining, .3))

    def capture(self, destination):
        with self.condition:
            previous = self.sequence
        image, _ = self.get_frame(timeout=10, after=previous)
        Path(destination).write_bytes(image)

    def _publish(self, image):
        with self.condition:
            self.frame = image
            self.sequence += 1
            self.last_frame_at = self.clock()
            self.times.append(self.last_frame_at)
            self.status, self.error = "live", None
            self.condition.notify_all()

    def _run(self):
        while True:
            with self.condition:
                while not self.closed and self.clock() - self.last_demand >= self.idle_timeout:
                    self.condition.wait()
                if self.closed:
                    return
                self.status = "starting"
            process = None
            try:
                binary = shutil.which("rpicam-vid") or shutil.which("libcamera-vid")
                if not binary:
                    raise OSError("missing camera software")
                process = subprocess.Popen(
                    [binary, "--nopreview", "--timeout", "0", "--codec", "mjpeg",
                     "--width", str(self.width), "--height", str(self.height),
                     "--framerate", str(self.target_fps), "--quality", "80", "--output", "-"],
                    stdout=subprocess.PIPE, stderr=subprocess.DEVNULL)
                with self.condition:
                    self.process = process
                parser = JpegFrames()
                started = self.clock()
                last_data = started
                with selectors.DefaultSelector() as selector:
                    selector.register(process.stdout, selectors.EVENT_READ)
                    while not self.closed and self.clock() - self.last_demand < self.idle_timeout:
                        if not selector.select(timeout=.3):
                            if process.poll() is not None or self.clock() - last_data > 6:
                                raise OSError("camera not producing frames")
                            continue
                        data = os.read(process.stdout.fileno(), 65536)
                        if not data:
                            raise OSError("camera disconnected")
                        last_data = self.clock()
                        for image in parser.feed(data):
                            # Let exposure settle on a cold start.
                            if self.clock() - started >= .8:
                                self._publish(image)
            except (OSError, subprocess.SubprocessError):
                with self.condition:
                    self.status = "unavailable"
                    self.frame = None
                    self.error = "Camera unavailable. Check its connection and close other camera applications."
                    self.condition.notify_all()
                # A failed camera must not spin or start multiple competing processes.
                with self.condition:
                    retry_at = self.clock() + 3
                    while not self.closed and self.clock() < retry_at:
                        self.condition.wait(timeout=max(.01, retry_at - self.clock()))
            finally:
                if process:
                    if process.poll() is None:
                        process.terminate()
                        try:
                            process.wait(timeout=3)
                        except subprocess.TimeoutExpired:
                            process.kill(); process.wait()
                    if process.stdout:
                        process.stdout.close()
                with self.condition:
                    self.process = None
                    self.frame = None
                    self.times.clear()
                    if not self.error:
                        self.status = "standby"

    def close(self):
        with self.condition:
            self.closed = True
            self.condition.notify_all()
        self.thread.join(timeout=5)
