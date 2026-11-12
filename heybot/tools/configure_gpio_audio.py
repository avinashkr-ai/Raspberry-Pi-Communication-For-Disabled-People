"""Two-stage setup for INMP441 + MAX98357A on a Raspberry Pi 4B.

sudo python3 tools/configure_gpio_audio.py --enable-driver
# Reboot, then run as pi:
python3 tools/configure_gpio_audio.py --select-devices
"""
import argparse
import os
import re
import shutil
import sys
import tempfile
from datetime import datetime
from pathlib import Path

BEGIN = "# BEGIN HEYBOT GPIO AUDIO"
END = "# END HEYBOT GPIO AUDIO"


def replace_block(text, body):
    if BEGIN in text:
        before, _, rest = text.partition(BEGIN)
        _, separator, after = rest.partition(END)
        if not separator:
            raise ValueError("The existing Hey Bot block is incomplete.")
        text = before + after.lstrip("\n")
    return text.rstrip() + "\n\n" + BEGIN + "\n" + body.rstrip() + "\n" + END + "\n"


def enable_driver(config=Path("/boot/firmware/config.txt")):
    if os.geteuid() != 0 or not Path("/proc/device-tree/model").is_file():
        raise SystemExit("Run --enable-driver on the Pi using sudo.")
    if not (config.parent / "overlays/googlevoicehat-soundcard.dtbo").is_file():
        raise SystemExit("The standard googlevoicehat-soundcard overlay is missing.")
    text = config.read_text()
    # Do not stack incompatible I2S card overlays on the same PCM peripheral.
    outside = re.sub(re.escape(BEGIN) + r".*?" + re.escape(END), "", text, flags=re.S)
    conflicts = [line for line in outside.splitlines()
                 if re.match(r"\s*dtoverlay=(hifiberry|i2s-|adau7002|googlevoicehat|max98357|iqaudio|audioinjector|seeed|respeaker)", line)]
    if conflicts:
        raise SystemExit("An existing audio-card overlay needs to be reviewed before enabling GPIO audio.")
    backup = config.with_name(config.name + ".heybot-" + datetime.now().strftime("%Y%m%d-%H%M%S") + ".bak")
    shutil.copy2(config, backup)
    updated = replace_block(text, "[all]\n# INMP441 + MAX98357A; GPIO16 controls amplifier SD/MODE.\ndtparam=audio=off\ndtparam=i2s=on\ndtoverlay=googlevoicehat-soundcard")
    config.write_text(updated)
    print("GPIO I2S driver enabled. Backup:", backup)
    print("Reboot the Pi, then run --select-devices as the pi user.")


def atomic_private(path, text):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    descriptor, temporary = tempfile.mkstemp(dir=path.parent, prefix=".heybot-")
    try:
        with os.fdopen(descriptor, "w") as output:
            output.write(text)
        os.chmod(temporary, 0o600)
        os.replace(temporary, path)
    finally:
        Path(temporary).unlink(missing_ok=True)


def select_devices(env_path):
    if os.geteuid() == 0:
        raise SystemExit("Run --select-devices as the pi user, without sudo.")
    candidates = []
    proc = Path("/proc/asound")
    for card in proc.glob("card[0-9]*"):
        if not card.is_dir() or not (card / "id").is_file():
            continue
        name = (card / "id").read_text().strip()
        if ("google" in name.lower() or "voicehat" in name.lower()) and re.fullmatch(r"[A-Za-z0-9_]+", name):
            candidates.append(name)
    if len(candidates) != 1:
        raise SystemExit("Expected one Google voiceHAT ALSA card after reboot. Check arecord -l and aplay -l.")
    card = candidates[0]
    asound = Path.home() / ".asoundrc"
    existing = asound.read_text() if asound.exists() else ""
    # Driver exposes stereo S32_LE/48 kHz. ALSA plug converts the recognizer's
    # S16_LE/16 kHz stream; copy policy selects the left INMP441 channel (L/R=GND).
    # Softvol keeps a useful speaker volume control on the I2S amplifier.
    body = '''pcm.heybot_gpio_mic {
    type plug
    slave {
        pcm "hw:CARD=CARD_ID,DEV=0"
        format S32_LE
        rate 48000
        channels 2
    }
    route_policy copy
}
pcm.heybot_gpio_speaker {
    type softvol
    slave.pcm {
        type plug
        slave {
            pcm "hw:CARD=CARD_ID,DEV=0"
            format S32_LE
            rate 48000
            channels 2
        }
    }
    control {
        name "HeyBot Speaker"
        card "CARD_ID"
    }
    min_dB -40.0
    max_dB -6.0
    resolution 100
}
'''.replace("CARD_ID", card)
    env_path = Path(env_path)
    lines = env_path.read_text().splitlines()
    names = {"HEYBOT_INPUT_DEVICE": "heybot_gpio_mic", "HEYBOT_OUTPUT_DEVICE": "heybot_gpio_speaker"}
    output = [line for line in lines if line.partition("=")[0] not in names]
    output += [key + "=" + value for key, value in names.items()]
    atomic_private(asound, replace_block(existing, body))
    atomic_private(env_path, "\n".join(output) + "\n")
    print("Selected GPIO audio card:", card)
    print("Input: heybot_gpio_mic; output: heybot_gpio_speaker.")
    print("Restart heybot.service. This configures drivers; a recording/playback test is still required.")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--enable-driver", action="store_true")
    mode.add_argument("--select-devices", action="store_true")
    parser.add_argument("--env", type=Path, default=Path.home() / ".config/heybot/heybot.env")
    args = parser.parse_args()
    if args.enable_driver:
        enable_driver()
    else:
        select_devices(args.env)
