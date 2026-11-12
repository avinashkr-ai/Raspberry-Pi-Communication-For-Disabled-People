"""Explicit camera -> Groq -> headphones test. Uploads one camera photograph."""
import os
import tempfile
from pathlib import Path

from heybot.app import load_env
from heybot.hardware import Camera, Speaker
from heybot.vision import AssistantError, DEFAULT_MODEL, GroqVision

load_env(Path.home() / ".config/heybot/heybot.env")
speaker = Speaker(os.getenv("HEYBOT_OUTPUT_DEVICE", "auto"))
client = GroqVision(os.getenv("GROQ_API_KEY"), os.getenv("GROQ_MODEL", DEFAULT_MODEL))
speaker.say("Testing the camera. Please hold it steady.")
with tempfile.TemporaryDirectory(prefix="heybot-test-") as folder:
    image = Path(folder) / "photo.jpg"
    Camera().capture(image)
    print("Camera capture succeeded; uploading one photo to Groq.", flush=True)
    try:
        description = client.describe(image)
    except AssistantError as error:
        print("Groq request failed:", str(error), "HTTP status:", error.status, flush=True)
        raise SystemExit(1) from None
print("Groq photo description:", description, flush=True)
speaker.say(description)
print("Headphone playback completed. Temporary photograph deleted.", flush=True)
