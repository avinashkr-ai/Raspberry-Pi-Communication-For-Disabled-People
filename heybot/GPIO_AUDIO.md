# INMP441 microphone + MAX98357A speaker amplifier

These modules use **I²S digital audio** on the Raspberry Pi 4B's GPIO header.
Linux supplies the clocks and audio driver; Python uses ALSA rather than manually
toggling the pins. Capture and speech playback run in turn to avoid hearing the
bot's own response.

**Shut down the Pi and disconnect power before wiring.** Use a 3.3 V supply for
the INMP441. The MAX98357A drives a passive speaker through its own output terminals;
do not connect a bare speaker to a GPIO output or a speaker terminal to Pi ground.
This assumes the usual breakouts with the pin labels listed below. Check the
labels on your actual boards before connecting power.

| Module pin | Pi physical pin | BCM GPIO / signal |
| --- | --- | --- |
| INMP441 VDD / VCC | **1** | 3.3 V |
| INMP441 GND | **6** | Ground |
| INMP441 L/R | **9** | Ground; select left audio channel |
| INMP441 SCK / BCLK | **12** | GPIO18, PCM clock |
| INMP441 WS / LRCL | **35** | GPIO19, frame clock |
| INMP441 SD / DOUT | **38** | GPIO20, Pi audio input |
| MAX98357A VIN | **2** | 5 V |
| MAX98357A GND | **14** | Ground |
| MAX98357A BCLK | **12** | GPIO18, shared clock |
| MAX98357A LRC / LRCLK | **35** | GPIO19, shared frame clock |
| MAX98357A DIN | **40** | GPIO21, Pi audio output |
| MAX98357A SD / MODE | **36** | GPIO16, driver-controlled amplifier enable |
| MAX98357A GAIN | — | Leave open for the board's default gain |
| MAX98357A speaker + / − | — | Connect only to the two terminals of a compatible passive speaker |

Use a speaker of **4 Ω or greater**, with a power rating appropriate to the amp.
Both amplifier outputs are driven: neither is a ground connection. A GPIO16
high level selects the left playback channel; the software sends mono speech to
both channels. Use a breadboard or a suitable splitter to share the clock pins
between both modules. Keep the I²S signal wires short.

The pin assignments follow the manufacturer guides for
[I²S microphone wiring](https://learn.adafruit.com/adafruit-i2s-mems-microphone-breakout/raspberry-pi-wiring-test)
and [MAX98357A wiring](https://learn.adafruit.com/adafruit-max98357-i2s-class-d-mono-amp/raspberry-pi-wiring).
Amplifier output and SD/MODE requirements are described in its
[pinout guide](https://learn.adafruit.com/adafruit-max98357-i2s-class-d-mono-amp/pinouts).

## Driver and device selection

The prepared setup uses the Raspberry Pi `googlevoicehat-soundcard` overlay for
one I²S capture/playback card. This is an integration of an I²S microphone and amp
using the driver's exposed formats; the actual connected pair must still be
tested on your Pi. Its kernel codec uses **stereo, 32-bit, 48 kHz**; the generated
ALSA configuration converts to the recognizer's mono, 16-bit, 16 kHz audio.
The configuration enables GPIO audio and disables the onboard headphone audio.
To test 3.5 mm output again, restore the saved boot configuration and set output to
`auto` in the private Hey Bot environment file.

On the Pi, after installing the new assistant version:

```bash
cd /opt/heybot
sudo .venv/bin/python tools/configure_gpio_audio.py --enable-driver
sudo reboot
```

After reconnecting, run as `pi` (without sudo):

```bash
cd /opt/heybot
.venv/bin/python tools/configure_gpio_audio.py --select-devices
sudo systemctl restart heybot
```

The helper backs up `/boot/firmware/config.txt`, adds its own labelled block,
preserves unrelated configuration, and stops if it detects a conflicting audio
overlay. Selection writes a labelled block to `~/.asoundrc` and updates only the
input/output device settings in the private environment file; it does not print
the API key. The speaker PCM includes software attenuation capped at −6 dB.

## Physical checks

Stop the assistant before manually opening its audio devices:

```bash
sudo systemctl stop heybot
arecord -l
aplay -l
arecord -D heybot_gpio_mic -c 1 -r 16000 -f S16_LE -d 5 /tmp/heybot-mic-test.wav
aplay -D heybot_gpio_speaker /tmp/heybot-mic-test.wav
sudo systemctl start heybot
```

The test WAV contains five seconds from the microphone; delete it when finished.
Adjust the software volume after the first playback creates its mixer control:

```bash
alsamixer
```

Select the GPIO card and the **HeyBot Speaker** control. Start quietly. A listed
ALSA card alone cannot prove that the physical microphone or speaker is wired:
these I²S parts do not enumerate like USB hardware.

Kernel references:
[overlay](https://github.com/raspberrypi/linux/blob/rpi-6.12.y/arch/arm/boot/dts/overlays/googlevoicehat-soundcard-overlay.dts),
[codec formats and amplifier control](https://github.com/raspberrypi/linux/blob/rpi-6.18.y/sound/soc/bcm/googlevoicehat-codec.c).
