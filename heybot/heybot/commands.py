"""Wake-word gating independent of hardware, speech recognition, and APIs."""
import re

WAKE_PHRASES = ("hey bot", "hey boat", "heybot")
PHOTO_PHRASES = ("click photo", "click a photo", "click picture", "click a picture",
                 "take photo", "take a photo", "take picture", "take a picture",
                 "capture photo", "describe scene", "what do you see")
OTHER_PHRASES = ("repeat", "repeat that", "help", "cancel", "go to sleep")
# "heybot" is accepted by the typed parser; Vosk's dictionary has separate words.
GRAMMAR = list(WAKE_PHRASES[:2] + PHOTO_PHRASES + OTHER_PHRASES)
GRAMMAR += [wake + " " + command for wake in WAKE_PHRASES[:2]
            for command in PHOTO_PHRASES + OTHER_PHRASES]
GRAMMAR += ["[unk]"]


class CommandGate:
    def __init__(self, window=15.0):
        self.window = window
        self.armed_until = 0.0

    def arm(self, now):
        self.armed_until = now + self.window

    def accept(self, text, now):
        text = " ".join(re.sub(r"[^a-z ]", " ", text.lower()).split())
        woke = False
        for wake in WAKE_PHRASES:
            if text == wake or text.startswith(wake + " "):
                text = text[len(wake):].strip()
                woke = True
                self.arm(now)
                break
        if not woke and now >= self.armed_until:
            return None
        if not text:
            return "wake" if woke else None
        action = None
        if text in PHOTO_PHRASES:
            action = "photo"
        elif text in ("repeat", "repeat that"):
            action = "repeat"
        elif text == "help":
            action = "help"
        elif text in ("cancel", "go to sleep"):
            action = "cancel"
        if action is not None:
            self.armed_until = 0.0
        return action
