# SceneVoice — photo description on Raspberry Pi

SceneVoice turns a camera photograph into a spoken scene description. Its logo
combines a camera lens with sound waves. The spoken wake command remains **Hey bot**.
Internal installation paths and the `heybot.service` name remain compatible.

Headless software for a Raspberry Pi 4B with 1 GB RAM, CSI camera, INMP441 GPIO
microphone and MAX98357A GPIO speaker amplifier, or USB audio/headphones for tests.
It uses offline Vosk for a fixed set of English commands,
`rpicam-still` for photographs, Groq's vision API for descriptions, and offline
eSpeak NG through ALSA for spoken answers. No desktop is required.

The password-protected local dashboard shows saved photos, commands, responses,
processing stages, failures, and session history. It also accepts typed commands
and photo requests without a microphone. It runs on the Pi with Flask/Waitress
and stores history in SQLite; no separate cloud web hosting is used.

## Local dashboard

The overview now has a live camera view, a current processing flow, scene
descriptions, and recent capture thumbnails. The Sessions page lets you search
loaded history and open a saved image. The Device page shows available memory,
temperature, storage, and the actual assistant/camera/audio child processes.

The dashboard and sign-in page use a dark theme. On phones, navigation moves to
the bottom, the camera uses the full page width, controls have larger touch areas,
and command/password inputs use readable 16-pixel text. The Device page includes
the full browser address and local-network connection guidance.

Live preview uses one shared `rpicam-vid` MJPEG producer at 1280 × 960, up to
8 frames/second. The browser refreshes the displayed frame approximately five
times per second. Preview frames stay in memory and are not saved or uploaded.
**Capture & describe** saves a fresh frame from that same camera feed, then sends
that single photo to Groq. Preview and capture therefore do not start competing
camera applications. The browser switches to the captured photo so you can inspect
it; select **Live view** to return to the live camera. Click its expand button or
the result thumbnail to open a larger photo preview and download saved photos.

**Pause live view** pauses preview on that browser. When no browser requests frames
and no capture is requested, the camera process stops after approximately 15
seconds. It starts again on demand. Closing/hiding the page also stops browser
frame requests. A stopped, starting, or disconnected camera is shown explicitly;
an old image is not labelled as a fresh live feed.

After installing and starting this version, open **`http://<Pi-IP>:8080`** from
your laptop or phone on the same local network. Check the current Pi address
with `hostname -I` on the Pi.

The first startup generates a separate private dashboard password. Read it on
the Pi using:

```bash
cat ~/.config/heybot/dashboard-password
```

Sign in with that password; no username is needed. It is separate from the SSH
password. The web page never receives the Groq API key. This is a local HTTP
service for your trusted network; do not forward port 8080 to the internet.
For an encrypted connection from a laptop, set `HEYBOT_WEB_HOST=127.0.0.1` and use
`ssh -L 8080:127.0.0.1:8080 pi@<Pi-IP>`, then open `http://127.0.0.1:8080`.

- **Take a photo**: queues one camera capture, Groq analysis, and optional playback.
- **Speak on Pi**: turn off to read the description without using the speaker.
- **The conversation**: accepted command and assistant responses for the selected session.
- **Live activity**: capture, analysis, playback, device notices, and errors.
- **Session history**: select older sessions to see their photos and conversations;
  load more to browse older records. Delete a completed session when no longer needed.
- **Mic & speaker**: configured ALSA input/output names and voice readiness in the status bar.

The page updates approximately every 1.5 seconds. Camera/API jobs are serialized
so web requests and voice commands cannot open the camera at the same time. Up
to eight web commands can wait in the queue. Only the documented command set is
accepted; the command box is not a shell or an open-ended chatbot.

## What you say

1. Say **“Hey bot”** (or “Hey boat”). Wait for **“Yes, I'm listening. Say click photo.”**
2. Within 15 seconds, say **“click photo”**, **“click picture”**, or **“take a photo”**.
3. Hold the camera steady. The bot takes a photograph, sends it to Groq, and speaks
   a short English description. It then waits for “Hey bot” again.

You can also say **“Hey bot click photo”** in one utterance. After “Hey bot”,
**“repeat”** repeats the last description without another photo/API request;
**“help”** explains the commands; **“cancel”** returns to waiting for the wake word.
This is a small offline speech recognizer with wake-phrase gating, not a general
conversation assistant. Noise and accents can affect recognition. Wait until the
bot finishes speaking before giving another command. It does not listen during
capture, API processing, or playback; spoken interruption is not supported.

## Hardware

- The attached OV5647 CSI camera is supported by Raspberry Pi's modern camera stack.
- The Pi 4's **3.5 mm jack is headphone output only**. A headset's built-in
  microphone will not work through that socket. USB microphone input remains supported.
- **INMP441 + MAX98357A:** follow [the GPIO wiring and configuration guide](GPIO_AUDIO.md).
  GPIO audio requires its I²S kernel driver and ALSA device configuration. Do not
  connect an analog microphone or a bare speaker directly to a GPIO signal pin.
- Automatic output selection prefers the ALSA `Headphones` card. Configure
  `HEYBOT_OUTPUT_DEVICE` or `HEYBOT_INPUT_DEVICE` if multiple devices are present.
- Wi-Fi must have internet access for Groq. Wake detection and speech run locally.

## Installation on the Pi

The installer is for Raspberry Pi OS Lite Trixie (32- or 64-bit), with user `pi` by default.
Run it on the Pi, not on the Mac. It installs dependencies and a systemd boot service.

Create a private configuration using the example. Set `GROQ_API_KEY` to your key;
never paste a key into a shared terminal transcript or commit it to Git.

```bash
cp heybot.env.example groq.env
chmod 600 groq.env
nano groq.env
sudo bash install.sh pi "$PWD/groq.env"
```

Code is installed at `/opt/heybot`, the private configuration at
`/home/pi/.config/heybot/heybot.env` (mode 600), and history/runtime data at
`/home/pi/.local/state/heybot` (mode 700). After installation, remove the extra
source copy of `groq.env` if it is no longer needed. The API key is loaded from the
private configuration and is not embedded in source code or normal logs.

Groq model default: `qwen/qwen3.8-27b`, verified against the account's model list.
The model is configurable because availability can change; choose an
[image-capable Groq model](https://console.groq.com/docs/vision).
The installer downloads the publisher's roughly 40 MB
[small English Vosk model](https://alphacephei.com/vosk/models).
Its runtime memory is approximately 300 MB, suitable for the 1 GB Pi.
The installer also clears the legacy executable-stack flag on Vosk's bundled
library using `patchelf`, so it loads on the newer Raspberry Pi OS/glibc.

## Startup and checks

The installer runs `systemctl enable heybot.service`. On each boot Linux starts
the assistant automatically under user `pi`. No SSH login is needed to start it.
When a microphone and audio output are available it announces that it is ready.
With no microphone it remains running, reports the missing microphone in its
logs and startup speech, and checks again every five seconds. The dashboard
continues working without a microphone or speaker. A silent or disconnected I²S
mic still needs a real recording test: the driver may exist without the module wired.

```bash
systemctl status heybot --no-pager
journalctl -u heybot -n 40 --no-pager
cd /opt/heybot
.venv/bin/python -m heybot --env ~/.config/heybot/heybot.env --doctor --check-api
```

The doctor returns a nonzero status when required hardware is missing. With a custom
GPIO PCM configured it reports that configuration; the physical audio test in the
GPIO guide is also needed. A successful
service status alone does not prove that a microphone is connected.

To stop or restart:

```bash
sudo systemctl stop heybot
sudo systemctl restart heybot
```

## Test now over SSH, without a microphone

The service and a manual test must not run together; a lock prevents hardware
conflicts. Stop the service, start the interactive terminal, and type commands:

```bash
sudo systemctl stop heybot
cd /opt/heybot
.venv/bin/python -m heybot --env ~/.config/heybot/heybot.env --text
```

Type `hey bot`, then `click photo`. Responses play through the Pi's headphones.
Type `quit` to exit. Add `--silent` to print responses on the SSH terminal instead.
Restore automatic listening afterwards:

```bash
sudo systemctl start heybot
```

To describe an existing image once, explicitly uploading that file to Groq:

```bash
cd /opt/heybot
.venv/bin/python -m heybot --env ~/.config/heybot/heybot.env --describe-file /path/photo.jpg --silent
```

Stop the service before this test as well. To change devices or the API key:

```bash
nano ~/.config/heybot/heybot.env
sudo systemctl restart heybot
```

## Troubleshooting

```bash
rpicam-hello --list-cameras
arecord -l
aplay -l
amixer -c Headphones sget PCM
```

If the camera is not found, shut the Pi down and disconnect power before checking
the CSI ribbon cable. If playback is muted, inspect the headphone PCM control in
`alsamixer -c Headphones`. Choose a comfortable volume before wearing headphones.
An API error is spoken without revealing the key. There are no automatic API
retries or repeated captures; request another photo when ready. If a key or model
fails, update the private configuration and restart the service.

## Data and limits

Microphone audio is processed locally and is not sent to Groq. Only a requested
photo is uploaded. Requested photographs, accepted commands, assistant responses,
and processing events are retained locally for the dashboard, including captured
photos whose API analysis failed. Temporary working copies are deleted after
processing. The last saved description is restored after a restart for “repeat”.

History is private at `/home/pi/.local/state/heybot/history.sqlite3`; photographs
are stored in its `photos/` folder. Defaults retain the newest **500 completed
sessions** and up to **200 MB of photographs**. Older sessions are removed;
the photo budget can remove an older photo while preserving its conversation.
Set `HEYBOT_HISTORY_LIMIT` and `HEYBOT_PHOTO_BUDGET_MB` in the private environment
file to change these limits. A restart marks unfinished sessions as interrupted.
A forced power loss can leave a temporary working directory. Groq's own data
handling is governed by your account and its policies.

This version describes still images. It does not provide walking directions,
measure distance, detect every obstacle, or establish that a route is safe. Image
models can miss or misidentify objects. It should be used as a scene-description
prototype alongside established mobility aids.

## Development checks

From this folder:

```bash
.venv/bin/python -m unittest discover -s tests -v
bash -n install.sh
```

Tests cover wake-word gating and expiry, single captures, repeat without upload,
cleanup on failure, audio-device selection, confidence filtering, and redacted
API errors. Dashboard tests also cover login and CSRF protection, bounded queues,
retention, photo access, deletion, persistent history, and web operation without
audio hardware. Hardware and real voice recognition need additional checks on the Pi.
