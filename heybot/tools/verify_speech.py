"""Check native Vosk with synthesized speech; does not test a physical mic."""
import io
import json
import subprocess
import sys
import wave
from pathlib import Path

from vosk import Model, KaldiRecognizer, SetLogLevel
from heybot.commands import GRAMMAR, CommandGate
from heybot.hardware import recognized_text

SetLogLevel(-1)
model = Model(str(Path(sys.argv[1])))
for phrase, expected in (("hey bot", "wake"), ("hey bot click photo", "photo")):
    audio = subprocess.run(["espeak-ng", "-v", "en-us", "-s", "130", "--stdout", phrase],
                           check=True, capture_output=True).stdout
    with wave.open(io.BytesIO(audio)) as wav:
        recognizer = KaldiRecognizer(model, wav.getframerate(), json.dumps(GRAMMAR))
        recognizer.SetWords(True)
        recognizer.AcceptWaveform(wav.readframes(wav.getnframes()))
        result = recognized_text(recognizer.FinalResult())
    action = CommandGate().accept(result, 100)
    print(f"Synthesized speech {phrase!r}: recognized {result!r}; action={action}", flush=True)
    if action != expected:
        sys.exit("Speech check failed; real microphone tuning may also be required.")
print("Native speech recognition check passed. Physical microphone remains untested.")
