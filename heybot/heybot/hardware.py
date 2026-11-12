"""ALSA audio and rpicam adapters, with no desktop or Python camera SDK needed."""
import json
import os
import re
import selectors
import shutil
import subprocess
from pathlib import Path

from .vision import AssistantError


def audio_devices(kind, proc=Path("/proc/asound")):
    """Return stable ALSA card names, not changeable numeric card indices."""
    try:
        lines = (proc / "pcm").read_text().splitlines()
    except OSError:
        return []
    found = []
    for line in lines:
        match = re.match(r"(\d+)-(\d+):", line)
        if match and re.search(r"\b" + kind + r"\s+[1-9]", line):
            card, device = map(int, match.groups())
            try:
                name = (proc / ("card" + str(card)) / "id").read_text().strip()
            except OSError:
                continue
            if re.fullmatch(r"[A-Za-z0-9_]+", name):
                found.append((name, f"plughw:CARD={name},DEV={device}"))
    return found


def choose_audio(kind, configured="auto"):
    if configured and configured != "auto":
        return configured
    devices = audio_devices(kind)
    if kind == "playback":
        devices.sort(key=lambda item: ("headphone" not in item[0].lower(), item[0]))
    if not devices:
        if kind == "capture":
            raise AssistantError("No microphone was found. Check the GPIO I2S or USB microphone and its ALSA driver.")
        raise AssistantError("No audio output was found. Check the ALSA audio configuration.")
    return devices[0][1]


class Speaker:
    def __init__(self, device="auto", silent=False):
        self.device = device
        self.silent = silent

    def say(self, text):
        if self.silent:
            print(text, flush=True)
            return
        device = choose_audio("playback", self.device)
        try:
            # Text through stdin avoids shell expansion and option injection.
            wav = subprocess.run(["espeak-ng", "-v", "en", "-s", "155", "--stdout", "--stdin"],
                                 input=text.encode(), stdout=subprocess.PIPE,
                                 stderr=subprocess.DEVNULL, timeout=60, check=True)
            subprocess.run(["aplay", "-q", "-D", device], input=wav.stdout,
                           stderr=subprocess.DEVNULL, timeout=65, check=True)
        except (OSError, subprocess.SubprocessError):
            raise AssistantError("Speech output failed. Check the speaker or headphones and the ALSA output device.") from None


class Camera:
    def capture(self, destination):
        command = shutil.which("rpicam-still") or shutil.which("libcamera-still")
        if not command:
            raise AssistantError("The camera software is missing. Install rpicam-apps-lite.")
        try:
            subprocess.run([command, "--nopreview", "--timeout", "1500", "--width", "1280",
                            "--height", "960", "--quality", "85", "--output", str(destination)],
                           stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                           timeout=20, check=True)
            if not Path(destination).is_file() or Path(destination).stat().st_size == 0:
                raise OSError
        except (OSError, subprocess.SubprocessError):
            raise AssistantError("I could not take a photo. Please check the camera connection.") from None


class Microphone:
    def __init__(self, device):
        self.device = device
        self.process = None
        self.selector = None

    def __enter__(self):
        try:
            self.process = subprocess.Popen(
                ["arecord", "-q", "-D", self.device, "-t", "raw", "-f", "S16_LE", "-r", "16000", "-c", "1"],
                stdout=subprocess.PIPE, stderr=subprocess.DEVNULL)
            self.selector = selectors.DefaultSelector()
            self.selector.register(self.process.stdout, selectors.EVENT_READ)
        except OSError:
            self.__exit__(None, None, None)
            raise AssistantError("The microphone could not be opened.") from None
        return self

    def read(self, timeout=2):
        if not self.selector.select(timeout=timeout):
            if self.process.poll() is not None:
                raise AssistantError("The microphone disconnected or is busy.")
            return b""
        data = os.read(self.process.stdout.fileno(), 8000)
        if not data:
            raise AssistantError("The microphone disconnected or is busy.")
        return data

    def __exit__(self, *_):
        if self.selector:
            self.selector.close()
        if self.process:
            if self.process.poll() is None:
                self.process.terminate()
                try:
                    self.process.wait(timeout=2)
                except subprocess.TimeoutExpired:
                    self.process.kill()
                    self.process.wait()
            if self.process.stdout:
                self.process.stdout.close()


def recognized_text(result, minimum_confidence=.65):
    try:
        obj = json.loads(result)
        words = obj.get("result", [])
        if not words or sum(w.get("conf", 0) for w in words) / len(words) < minimum_confidence:
            return ""
        return obj.get("text", "")
    except (ValueError, TypeError, AttributeError):
        return ""
