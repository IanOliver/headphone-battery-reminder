#!/bin/sh
# Installs the reminder to ~/.local/bin and starts it automatically at login.
set -e
cd "$(dirname "$0")"

install -Dm755 headphone_battery_reminder.py "$HOME/.local/bin/headphone-battery-reminder"

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
