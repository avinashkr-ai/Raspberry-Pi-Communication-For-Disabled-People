#!/usr/bin/env bash
# Run on Raspberry Pi OS: sudo bash install.sh pi /path/to/private/groq.env
set -euo pipefail
umask 077
if [[ "$(uname -s)" != Linux || ${EUID} -ne 0 ]]; then
  echo 'Run this on the Raspberry Pi using sudo bash install.sh pi /path/to/groq.env' >&2
  exit 1
fi
target_user=${1:-pi}
secret_source=${2:-}
if [[ ! "$target_user" =~ ^[a-z_][a-z0-9_-]*$ ]] || ! id "$target_user" >/dev/null 2>&1; then
  echo 'The target Linux account does not exist.' >&2; exit 1
fi
target_home=$(getent passwd "$target_user" | cut -d: -f6)
target_group=$(id -gn "$target_user")
if [[ ! "$target_home" =~ ^/[a-zA-Z0-9/_-]+$ ]]; then
  echo 'Unsupported home directory path.' >&2; exit 1
fi
if [[ -z "$secret_source" || ! -f "$secret_source" ]]; then
  echo 'Provide the private Groq environment file as the second argument.' >&2; exit 1
fi
# Validate before changing the running service. Never source a credentials file.
python3 - "$secret_source" <<'PY'
import sys
from pathlib import Path
allowed = {'GROQ_API_KEY','GROQ_MODEL','HEYBOT_INPUT_DEVICE','HEYBOT_OUTPUT_DEVICE',
           'HEYBOT_MODEL_PATH','HEYBOT_STATE_DIR','HEYBOT_WEB_HOST','HEYBOT_WEB_PORT',
           'HEYBOT_WEB_PASSWORD_FILE','HEYBOT_HISTORY_LIMIT','HEYBOT_PHOTO_BUDGET_MB'}
values = {}
for line in Path(sys.argv[1]).read_text().splitlines():
    if not line.strip() or line.lstrip().startswith('#'): continue
    name, sep, value = line.partition('=')
    if not sep or name not in allowed or not value.strip():
        sys.exit('Invalid private configuration. Use simple NAME=value lines.')
    values[name] = value.strip()
if not values.get('GROQ_API_KEY','').startswith('gsk_'):
    sys.exit('A Groq API key is required in the private configuration.')
PY
source_dir=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)
export DEBIAN_FRONTEND=noninteractive
apt-get update
apt-get install -y ca-certificates alsa-utils espeak-ng rpicam-apps-lite patchelf \
  python3-venv python3-pip python3-cffi
for group in audio video render; do
  if getent group "$group" >/dev/null; then usermod -aG "$group" "$target_user"; fi
done
systemctl stop heybot.service 2>/dev/null || true
install -d -m 755 /opt/heybot /opt/heybot/heybot /opt/heybot/tools /opt/heybot/models /opt/heybot/heybot/web/static
install -m 644 "$source_dir"/heybot/*.py /opt/heybot/heybot/
install -m 644 "$source_dir"/heybot/web/*.html /opt/heybot/heybot/web/
install -m 644 "$source_dir"/heybot/web/static/* /opt/heybot/heybot/web/static/
install -m 644 "$source_dir/requirements.txt" /opt/heybot/requirements.txt
install -m 644 "$source_dir"/tools/*.py /opt/heybot/tools/
install -m 644 "$source_dir/README.md" /opt/heybot/README.md
install -m 644 "$source_dir/GPIO_AUDIO.md" /opt/heybot/GPIO_AUDIO.md
python3 -m venv --system-site-packages /opt/heybot/.venv
/opt/heybot/.venv/bin/python -m pip install --prefer-binary -r /opt/heybot/requirements.txt
# The older armv7 Vosk wheel marks its stack executable. New glibc rejects
# loading it. Clear that flag on this library; do not relax system protections.
vosk_library=$(/opt/heybot/.venv/bin/python -c 'import importlib.util; from pathlib import Path; print(Path(importlib.util.find_spec("vosk").origin).parent / "libvosk.so")')
patchelf --clear-execstack "$vosk_library"
/opt/heybot/.venv/bin/python -c 'import vosk; print("Vosk native library verified.")'
/opt/heybot/.venv/bin/python /opt/heybot/tools/download_model.py /opt/heybot/models
# umask protects credentials, but installed code/models must be readable by pi.
chmod -R a+rX /opt/heybot
config_dir="$target_home/.config/heybot"
install -d -m 700 -o "$target_user" -g "$target_group" "$config_dir" "$target_home/.local/state/heybot"
if [[ "$(readlink -f "$secret_source")" != "$config_dir/heybot.env" ]]; then
  install -m 600 -o "$target_user" -g "$target_group" "$secret_source" "$config_dir/heybot.env"
else
  chown "$target_user:$target_group" "$config_dir/heybot.env"
  chmod 600 "$config_dir/heybot.env"
fi
python3 - "$source_dir/systemd/heybot.service" "$target_user" "$target_group" "$target_home" <<'PY'
from pathlib import Path
import grp, sys
template, user, group, home = sys.argv[1:]
text = Path(template).read_text().replace('User=pi\n','User='+user+'\n').replace('Group=pi\n','Group='+group+'\n').replace('/home/pi/',home+'/')
available = {item.gr_name for item in grp.getgrall()}
text = text.replace('SupplementaryGroups=audio video render', 'SupplementaryGroups='+' '.join(g for g in ('audio','video','render') if g in available))
Path('/etc/systemd/system/heybot.service').write_text(text)
PY
chmod 644 /etc/systemd/system/heybot.service
systemd-analyze verify /etc/systemd/system/heybot.service
systemctl daemon-reload
systemctl enable heybot.service
systemctl restart heybot.service
echo 'Hey Bot is installed and enabled for boot. Check hardware and API:'
echo "sudo -u $target_user /opt/heybot/.venv/bin/python -m heybot --env $config_dir/heybot.env --doctor --check-api"
echo 'Run that check from /opt/heybot. View logs: journalctl -u heybot -n 40 --no-pager'
echo 'Dashboard: http://<Pi-IP>:8080 (local network only).'
echo "Dashboard password file: $config_dir/dashboard-password"
