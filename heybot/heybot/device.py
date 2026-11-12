"""Small, local health snapshot without shell commands or environment exposure."""
import os
import shutil
import socket
import time
from pathlib import Path


def device_info(directory):
    memory = {}
    try:
        for line in Path("/proc/meminfo").read_text().splitlines():
            key, _, value = line.partition(":")
            if key in ("MemTotal", "MemAvailable"):
                memory[key] = int(value.split()[0]) // 1024
    except (OSError, ValueError):
        pass
    try:
        temperature = round(int(Path("/sys/class/thermal/thermal_zone0/temp").read_text()) / 1000, 1)
    except (OSError, ValueError):
        temperature = None
    model = "Raspberry Pi"
    try:
        model = Path("/proc/device-tree/model").read_text().strip("\x00\n")
    except OSError:
        pass
    processes = [{"pid": os.getpid(), "name": "SceneVoice service"}]
    children = set()
    try:
        for task in Path("/proc/self/task").iterdir():
            try:
                children.update(int(pid) for pid in (task / "children").read_text().split())
            except (OSError, ValueError):
                pass
    except OSError:
        pass
    names = {"rpicam-vid": "Camera feed", "libcamera-vid": "Camera feed", "arecord": "Microphone",
             "aplay": "Speaker playback", "espeak-ng": "Speech synthesis"}
    for pid in sorted(children):
        try:
            name = Path(f"/proc/{pid}/comm").read_text().strip()
            if name in names:
                processes.append({"pid": pid, "name": names[name]})
        except OSError:
            pass
    return {"hostname": socket.gethostname(), "model": model, "temperature_c": temperature,
            "memory_total_mb": memory.get("MemTotal"), "memory_available_mb": memory.get("MemAvailable"),
            "disk_free_gb": round(shutil.disk_usage(directory).free / 1_000_000_000, 1),
            "processes": processes}
