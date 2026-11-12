<img src="heybot/heybot/web/static/logo.svg" width="72" height="72" alt="SceneVoice camera and sound logo">

# SceneVoice

A voice-controlled photo description assistant for Raspberry Pi. Say **“Hey bot,
click photo”** to capture an image, describe it using Groq's vision API, and hear
the response through headphones or a speaker.

Built for a headless Raspberry Pi 4B with 1 GB RAM, with a dark, mobile-friendly
local dashboard for live camera preview, commands, photos, and conversation history.

## Features

- Photo capture and AI scene descriptions with spoken responses.
- Offline English command recognition using Vosk and speech synthesis using eSpeak NG.
- Shared live camera feed and photo capture without competing camera processes.
- Password-protected dashboard with enlarged photo previews and downloads.
- Current processing stages, activity, session history, and Pi health information.
- Automatic startup through systemd.
- Headphone output, USB audio, and optional INMP441 / MAX98357A GPIO audio setup.

## Hardware

- Raspberry Pi 4B; tested on the 1 GB model with Raspberry Pi OS Lite Trixie.
- CSI camera; tested with the OV5647 camera module.
- Headphones or speaker output and a supported microphone for voice commands.
- Wi-Fi with internet access for Groq image analysis.

The Pi's 3.5 mm jack provides headphone output only. A headset microphone cannot
record through that socket. Web commands work without a microphone.

## Install on the Raspberry Pi

```bash
git clone https://github.com/avinashkr-ai/Raspberry-Pi-Communication-For-Disabled-People.git
cd Raspberry-Pi-Communication-For-Disabled-People/heybot
cp heybot.env.example groq.env
chmod 600 groq.env
nano groq.env
```

Enter your own Groq API key in `groq.env`, then install:

```bash
sudo bash install.sh pi "$PWD/groq.env"
```

The installer enables `heybot.service` for boot. Open `http://<Pi-IP>:8080/` on
another device on the same network. Read the generated dashboard password on the Pi:

```bash
cat ~/.config/heybot/dashboard-password
```

No dashboard username is required. Credentials, model downloads, private photos,
and runtime history are excluded from this repository.

## Commands

Say **“Hey bot”**, wait for the acknowledgement, then say **“click photo”**.
You can also say **“Hey bot click photo”** in one utterance. Other supported
commands include **repeat**, **help**, and **cancel**. The dashboard accepts the
same commands through its text field.

## Documentation

- [Full setup, architecture, and troubleshooting](heybot/README.md)
- [INMP441 microphone and MAX98357A amplifier wiring](heybot/GPIO_AUDIO.md)
- [Private configuration example](heybot/heybot.env.example)
- [Automated tests](heybot/tests)

Only explicitly captured photos are sent to Groq. Live preview and microphone
processing remain local; requested photos and conversations are stored on the Pi.

This version describes still images. Autonomous walking guidance is not
implemented, and scene descriptions do not establish that a route is safe.

## Development checks

From `heybot/`, with dependencies installed in a virtual environment:

```bash
PYTHONPATH=. .venv/bin/python -m unittest discover -s tests -v
bash -n install.sh
node --check heybot/web/static/app.js
```
