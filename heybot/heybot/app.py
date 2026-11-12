import argparse
import fcntl
import json
import logging
import os
import shutil
import signal
import subprocess
import tempfile
import threading
import time
from pathlib import Path

from .commands import CommandGate, GRAMMAR
from .hardware import Camera, Microphone, Speaker, choose_audio, recognized_text
from .vision import AssistantError, DEFAULT_MODEL, GroqVision
from .history import History
from .live_camera import LiveCamera

LOG = logging.getLogger("heybot")
STOP = threading.Event()
HELP = "Say Hey bot, then click photo. You can also say repeat, help, or cancel after Hey bot."


def load_env(path):
    """Read simple NAME=value files as data, never execute a shell script."""
    if not path:
        return
    try:
        for line in Path(path).read_text().splitlines():
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            name, sep, value = line.partition("=")
            if sep and name in {"GROQ_API_KEY", "GROQ_MODEL", "HEYBOT_INPUT_DEVICE",
                                "HEYBOT_OUTPUT_DEVICE", "HEYBOT_MODEL_PATH", "HEYBOT_STATE_DIR",
                                "HEYBOT_WEB_HOST", "HEYBOT_WEB_PORT", "HEYBOT_WEB_PASSWORD_FILE",
                                "HEYBOT_HISTORY_LIMIT", "HEYBOT_PHOTO_BUDGET_MB"}:
                os.environ[name] = value.strip()
    except OSError:
        raise AssistantError("The private configuration file could not be read.") from None


class Assistant:
    def __init__(self, vision, camera, speaker, state_dir, clock=time.monotonic, history=None):
        self.vision, self.camera, self.speaker = vision, camera, speaker
        self.state_dir, self.clock = Path(state_dir), clock
        self.gate = CommandGate()
        self.history = history
        self.last_description = history.last_description() if history else None

    def say(self, text, run_id=None, enabled=True):
        if self.history:
            self.history.event(run_id, "assistant", text)
        if enabled:
            try:
                audible = not getattr(self.speaker, "silent", False)
                if self.history and audible:
                    self.history.event(run_id, "audio_started", "Playing audio on the Pi")
                self.speaker.say(text)
                if self.history and audible:
                    self.history.event(run_id, "audio_finished", "Audio playback completed")
            except AssistantError as error:
                # A missing speaker must not prevent camera/web use.
                LOG.warning("%s", error)
                if self.history:
                    self.history.event(run_id, "audio_error", str(error))

    def stage(self, run_id, stage, text):
        if self.history:
            self.history.stage(run_id, stage, text)

    def handle(self, text, source="voice", run_id=None, speak=True):
        action = self.gate.accept(text, self.clock())
        if not action:
            if run_id and self.history:
                self.history.finish(run_id, error="Command not recognized or wake window expired.")
            return False
        # Log action names, never the microphone transcript or photograph content.
        LOG.info("Command: %s", action)
        if self.history and run_id is None:
            run_id = self.history.start(text, source)
        answer = None
        try:
            if self.history:
                self.history.event(run_id, "delivery", "Audio on Pi" if speak and not getattr(self.speaker, "silent", False) else "Text only")
            self.stage(run_id, "running", "Command received from " + source)
            if action == "wake":
                self.say("Yes, I'm listening. Say click photo.", run_id, speak)
                self.gate.arm(self.clock())  # full 15 seconds after the acknowledgement
            elif action == "photo":
                self.stage(run_id, "capturing", "Taking a photograph")
                self.say("Taking a photo. Please hold the camera steady.", run_id, speak)
                with tempfile.TemporaryDirectory(prefix="photo-", dir=self.state_dir) as folder:
                    photo = Path(folder) / "capture.jpg"
                    self.camera.capture(photo)
                    if self.history:
                        self.history.save_photo(run_id, photo)
                    self.stage(run_id, "analyzing", "Photo captured; asking Groq to describe it")
                    self.say("Photo taken. Analysing.", run_id, speak)
                    description = self.vision.describe(photo)
                # Temporary copy deleted; a private history copy is retained when enabled.
                self.last_description = description
                answer = description
                self.stage(run_id, "speaking", "Description ready; delivering the answer")
                self.say(description, run_id, speak)
            elif action == "repeat":
                answer = self.last_description or "There is no photo description to repeat yet."
                self.say(answer, run_id, speak)
            elif action == "help":
                self.say(HELP, run_id, speak)
                self.gate.arm(self.clock())
            elif action == "cancel":
                self.say("Cancelled. Say Hey bot when you need me.", run_id, speak)
            if self.history:
                self.history.finish(run_id, response=answer)
                self.history.state("idle", "Command finished")
        except AssistantError as error:
            LOG.warning("%s%s", error, f" (HTTP {error.status})" if error.status else "")
            self.say(str(error), run_id, speak)
            if self.history:
                self.history.event(run_id, "error", str(error))
                self.history.finish(run_id, error=str(error))
                self.history.state("error", str(error))
        except (OSError, ValueError):
            message = "Local storage failed. Please check free space on the SD card."
            LOG.error(message)
            self.say(message, run_id, speak)
            if self.history:
                self.history.finish(run_id, error=message)
        return True


def voice_loop(assistant, model_path, input_device, commands=None):
    model = None
    greeted = False
    last_error = None
    retry_after = 0
    announced_missing = False
    while not STOP.is_set():
        job = commands.pop() if commands else None
        if job:
            run_id, text, speak = job
            assistant.handle(text, source="web", run_id=run_id, speak=speak)
            continue
        if time.monotonic() < retry_after:
            STOP.wait(.2)
            continue
        try:
            device = choose_audio("capture", input_device)
            if model is None:
                if assistant.history:
                    assistant.history.state("loading_model", "Loading offline voice recognition")
                try:
                    from vosk import Model, KaldiRecognizer, SetLogLevel
                    SetLogLevel(-1)
                    if not (model_path / "am/final.mdl").is_file():
                        raise ValueError
                    model = Model(str(model_path))
                except Exception:
                    raise AssistantError("Offline speech recognition could not load. Run the installer to repair it.") from None
            if not greeted:
                assistant.say("Hey bot is ready. Say Hey bot, then click photo.")
                greeted = True
            recognizer = KaldiRecognizer(model, 16000, json.dumps(GRAMMAR))
            recognizer.SetWords(True)
            # Close recording BEFORE speaking or calling the API. This discards
            # buffered sound and prevents the bot from hearing its own response.
            command = None
            with Microphone(device) as mic:
                LOG.info("Listening for Hey bot.")
                if assistant.history:
                    assistant.history.state("listening", "Listening for Hey bot")
                while not STOP.is_set():
                    job = commands.pop() if commands else None
                    if job:
                        break
                    data = mic.read(timeout=.25)
                    if data and recognizer.AcceptWaveform(data):
                        candidate = recognized_text(recognizer.Result())
                        # An unknown utterance must not close/reopen the microphone.
                        # Probe a copy so handle() remains the sole state mutation.
                        probe = CommandGate(assistant.gate.window)
                        probe.armed_until = assistant.gate.armed_until
                        if probe.accept(candidate, assistant.clock()):
                            command = candidate
                            break
            if job:
                run_id, text, speak = job
                assistant.handle(text, source="web", run_id=run_id, speak=speak)
            elif command:
                assistant.handle(command)
            last_error = None
        except AssistantError as error:
            if str(error) != last_error:
                LOG.warning("%s", error)
                last_error = str(error)
                if assistant.history:
                    assistant.history.event(None, "system", str(error))
            if assistant.history:
                assistant.history.state("audio_unavailable", str(error) + " Web commands are available.")
            if not announced_missing:
                assistant.say("Hey bot has started. Voice input is unavailable. You can use the local dashboard.")
                announced_missing = True
            # Keep handling web commands while waiting for a microphone.
            retry_after = time.monotonic() + 5


def doctor(model_path, api=False):
    checks = []
    for executable in ("rpicam-still", "rpicam-hello", "arecord", "aplay", "espeak-ng"):
        present = bool(shutil.which(executable))
        checks.append(present)
        print(f"{executable}: {'found' if present else 'MISSING'}")
    for kind in ("capture", "playback"):
        try:
            env = "HEYBOT_INPUT_DEVICE" if kind == "capture" else "HEYBOT_OUTPUT_DEVICE"
            print(f"Audio {kind}: {choose_audio(kind, os.getenv(env, 'auto'))}")
            checks.append(True)
        except AssistantError as error:
            print(str(error)); checks.append(False)
    print("Offline speech model:", "found" if (model_path / "am" / "final.mdl").is_file() else "MISSING")
    checks.append((model_path / "am" / "final.mdl").is_file())
    try:
        import vosk  # noqa: F401
        print("Vosk Python package: found")
    except (ImportError, OSError):
        print("Vosk Python package: MISSING or incompatible; rerun the installer."); checks.append(False)
    key_present = bool(os.getenv("GROQ_API_KEY"))
    print("Groq API key:", "configured (hidden)" if key_present else "MISSING")
    checks.append(key_present)
    if shutil.which("rpicam-hello"):
        result = subprocess.run(["rpicam-hello", "--list-cameras"], capture_output=True, text=True, timeout=20)
        listing = result.stdout + result.stderr
        print("Camera detection:\n" + listing.strip())
        checks.append(result.returncode == 0 and "Available cameras" in listing
                      and "No cameras available" not in listing)
    if api:
        try:
            print("Groq model verified:", GroqVision(os.getenv("GROQ_API_KEY"),
                   os.getenv("GROQ_MODEL", DEFAULT_MODEL)).check_model())
        except AssistantError as error:
            print(str(error)); checks.append(False)
    return 0 if all(checks) else 1


def main(argv=None):
    parser = argparse.ArgumentParser(description="SceneVoice photo assistant")
    parser.add_argument("--env", help="Private NAME=value config; never printed")
    modes = parser.add_mutually_exclusive_group()
    modes.add_argument("--text", action="store_true", help="Type commands instead of using a microphone")
    modes.add_argument("--doctor", action="store_true", help="Check camera, audio, model, and configuration")
    modes.add_argument("--describe-file", type=Path, help="Upload and describe this image once (no wake word)")
    parser.add_argument("--check-api", action="store_true", help="Also check Groq model access during --doctor")
    parser.add_argument("--silent", action="store_true", help="Print responses instead of playing audio")
    parser.add_argument("--no-web", action="store_true", help="Disable the local dashboard")
    args = parser.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    os.umask(0o077)
    load_env(args.env)
    base = Path(__file__).resolve().parent.parent
    model_path = Path(os.getenv("HEYBOT_MODEL_PATH", str(base / "models/vosk-model-small-en-us-0.15")))
    if args.doctor:
        return doctor(model_path, args.check_api)
    state = Path(os.getenv("HEYBOT_STATE_DIR", str(Path.home() / ".local/state/heybot")))
    state.mkdir(parents=True, mode=0o700, exist_ok=True)
    state.chmod(0o700)
    # Prevent service and manual mode from competing for the camera/microphone.
    with (state / "assistant.lock").open("a") as lock:
        try:
            fcntl.flock(lock.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            raise AssistantError("Hey bot is already running. Stop heybot.service before a manual test.") from None
        vision = GroqVision(os.getenv("GROQ_API_KEY"), os.getenv("GROQ_MODEL", DEFAULT_MODEL))
        speaker = Speaker(os.getenv("HEYBOT_OUTPUT_DEVICE", "auto"), args.silent)
        if args.describe_file:
            speaker.say(vision.describe(args.describe_file))
            return 0
        history = History(state, int(os.getenv("HEYBOT_HISTORY_LIMIT", "500")),
                          int(os.getenv("HEYBOT_PHOTO_BUDGET_MB", "200")) * 1_000_000)
        camera = Camera() if args.text or args.no_web else LiveCamera()
        assistant = Assistant(vision, camera, speaker, state, history=history)
        for sig in (signal.SIGTERM, signal.SIGINT):
            signal.signal(sig, lambda *_: STOP.set())
        server = None
        try:
            if args.text:
                print("Type: hey bot, then click photo. Type quit to exit. No microphone required.", flush=True)
                while not STOP.is_set():
                    try:
                        command = input("> ")
                    except EOFError:
                        break
                    if command.strip().lower() in ("exit", "quit"):
                        break
                    if not assistant.handle(command, source="terminal"):
                        print("Say/type hey bot first; then a command within 15 seconds.", flush=True)
            else:
                from .dashboard import CommandQueue, create_app, private_secret
                commands = CommandQueue(history)
                if not args.no_web:
                    from waitress import create_server
                    password_file = Path(os.getenv("HEYBOT_WEB_PASSWORD_FILE",
                                         str(Path.home() / ".config/heybot/dashboard-password")))
                    password = private_secret(password_file, 15)
                    secret = private_secret(state / "web-session-secret")
                    web = create_app(history, commands, password, secret, camera=camera)
                    host = os.getenv("HEYBOT_WEB_HOST", "0.0.0.0")
                    port = int(os.getenv("HEYBOT_WEB_PORT", "8080"))
                    server = create_server(web, host=host, port=port, threads=4,
                                           max_request_body_size=4096, channel_timeout=20)
                    threading.Thread(target=server.run, daemon=True, name="dashboard").start()
                    LOG.info("Local dashboard started on port %s. Password file: %s", port, password_file)
                    history.event(None, "system", "Assistant and local dashboard started")
                voice_loop(assistant, model_path, os.getenv("HEYBOT_INPUT_DEVICE", "auto"), commands)
        finally:
            STOP.set()
            if server:
                server.close()
            if isinstance(camera, LiveCamera):
                camera.close()
            # The process exits immediately afterwards; keep the DB connection
            # alive until any in-flight dashboard request finishes.
            history.db.commit()
    return 0
