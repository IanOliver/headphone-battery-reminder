#!/bin/sh
# Installs the reminder to ~/.local/bin and starts it automatically at login.
set -e
cd "$(dirname "$0")"

# Settings are asked for when run in a terminal; pressing Enter keeps the current value.
CONFIG="${XDG_CONFIG_HOME:-$HOME/.config}/headphone-battery-reminder"
config_value() {
    sed -n "s/^[[:space:]]*$1[[:space:]]*=[[:space:]]*//p" "$CONFIG/config" 2>/dev/null | tail -n 1
}
current=$(config_value device)
remind_at=$(config_value remind_at)
remind_at=${remind_at:-17:25}

# Which headphones to watch.
# Paired audio devices as "ADDRESS NAME" lines, falling back to all paired devices.
devices=$(bluetoothctl devices Paired 2>/dev/null | while read -r _ address name; do
    if bluetoothctl info "$address" 2>/dev/null | grep -q "Icon: audio-"; then
        echo "$address $name"
    fi
done)
[ -n "$devices" ] || devices=$(bluetoothctl devices Paired 2>/dev/null | cut -d' ' -f2-)

if [ -t 0 ] && [ -n "$devices" ]; then
    echo "Which headphones should be watched?"
    default=
    i=0
    while read -r address name; do
        i=$((i + 1))
        if [ "$address" = "$current" ]; then
            default=$i
            echo "  $i) $name ($address) [current]"
        else
            echo "  $i) $name ($address)"
        fi
    done <<EOF
$devices
EOF
    while :; do
        printf "Choice%s: " "${default:+ [$default]}"
        read -r choice
        choice=${choice:-$default}
        case $choice in
            '' | *[!0-9]*) ;;
            *) [ "$choice" -ge 1 ] && [ "$choice" -le "$i" ] && break ;;
        esac
        echo "Please enter a number from 1 to $i."
    done
    current=$(echo "$devices" | sed -n "${choice}p" | cut -d' ' -f1)
elif [ -z "$current" ]; then
    echo "No headphones chosen. Pair your headphones, then run this script in a terminal." >&2
    exit 1
fi

# Time of the end-of-day reminder.
if [ -t 0 ]; then
    while :; do
        printf "Time for the end-of-day reminder, HH:MM [%s]: " "$remind_at"
        read -r answer
        answer=${answer:-$remind_at}
        if echo "$answer" | grep -Eq '^([01]?[0-9]|2[0-3]):[0-5][0-9]$'; then
            remind_at=$answer
            break
        fi
        echo "Please enter a 24-hour time, such as 17:25."
    done
fi

mkdir -p "$CONFIG"
cat > "$CONFIG/config" <<EOF
# Written by install.sh; re-run it to change these.
device = $current
remind_at = $remind_at
EOF

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

echo "Installed, watching $current with the reminder at $remind_at."
echo "It will start at next login; to start it now run:"
echo "  setsid -f $HOME/.local/bin/headphone-battery-reminder"
