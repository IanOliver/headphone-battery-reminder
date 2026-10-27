#!/bin/sh
# Installs the reminder to ~/.local/bin and starts it automatically at login.
set -e
cd "$(dirname "$0")"

install -Dm755 headphone_battery_reminder.py "$HOME/.local/bin/headphone-battery-reminder"

# Piper text-to-speech and its voice, for the spoken low-battery alerts.
DATA="${XDG_DATA_HOME:-$HOME/.local/share}/headphone-battery-reminder"
VOICE=en_GB-alba-medium
if [ ! -x "$DATA/piper-venv/bin/python" ]; then
    echo "Installing Piper..."
    python3 -m venv "$DATA/piper-venv"
    "$DATA/piper-venv/bin/pip" install -q piper-tts==1.8.0
fi
if [ ! -f "$DATA/voices/$VOICE.onnx" ]; then
    echo "Downloading the $VOICE voice..."
    mkdir -p "$DATA/voices"
    "$DATA/piper-venv/bin/python" -I -m piper.download_voices --data-dir "$DATA/voices" "$VOICE"
fi

mkdir -p "$HOME/.config/autostart"
cat > "$HOME/.config/autostart/headphone-battery-reminder.desktop" <<EOF
[Desktop Entry]
Type=Application
Name=Headphone Battery Reminder
Comment=Reminds you to charge your Bluetooth headphones
Exec=$HOME/.local/bin/headphone-battery-reminder
Icon=audio-headphones-symbolic
X-GNOME-Autostart-enabled=true
NoDisplay=true
EOF

echo "Installed. It will start at next login; to start it now run:"
echo "  setsid -f $HOME/.local/bin/headphone-battery-reminder"
