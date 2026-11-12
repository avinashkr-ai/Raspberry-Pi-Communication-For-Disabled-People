"""Download the small English Vosk model from its publisher, over verified TLS."""
import shutil
import sys
import tempfile
import urllib.request
import zipfile
from pathlib import Path

NAME = "vosk-model-small-en-us-0.15"
URL = "https://alphacephei.com/vosk/models/" + NAME + ".zip"


def download(destination):
    destination = Path(destination).resolve()
    destination.mkdir(parents=True, exist_ok=True)
    if (destination / NAME / "am/final.mdl").is_file():
        print("Offline speech model already installed.")
        return
    with tempfile.TemporaryDirectory(dir=destination) as temp:
        archive = Path(temp) / "model.zip"
        print("Downloading the 40 MB offline English model...", flush=True)
        with urllib.request.urlopen(URL, timeout=60) as response, archive.open("wb") as output:
            count = 0
            while chunk := response.read(1024 * 1024):
                count += len(chunk)
                if count > 60_000_000:
                    raise RuntimeError("Model download exceeds the expected size.")
                output.write(chunk)
        with zipfile.ZipFile(archive) as model:
            total = 0
            root = Path(temp).resolve()
            for member in model.infolist():
                target = (root / member.filename).resolve()
                total += member.file_size
                if not target.is_relative_to(root / NAME) or total > 200_000_000:
                    raise RuntimeError("Unexpected contents in model archive.")
                if (member.external_attr >> 16) & 0o170000 == 0o120000:
                    raise RuntimeError("Model archive contains a symbolic link.")
            model.extractall(root)
        extracted = Path(temp) / NAME
        if not (extracted / "am/final.mdl").is_file():
            raise RuntimeError("Downloaded model is incomplete.")
        target = destination / NAME
        if target.exists():
            shutil.rmtree(target)
        shutil.move(str(extracted), str(target))
    print("Offline speech model installed.")


if __name__ == "__main__":
    download(sys.argv[1] if len(sys.argv) > 1 else Path(__file__).resolve().parents[1] / "models")
