# Headphone Battery Reminder

A small Linux tray app that reminds you to charge your Bluetooth headphones before they run flat. It runs in the background with no interaction needed.

## What it does

- **End-of-day reminder.** When the headphones are connected and their battery is below 50%, it shows a desktop notification at 17:25 that day (you choose the time when installing). If the battery first drops below 50% after that time, the notification is shown straight away. You get at most one a day.
- **Break reminder.** Before the end-of-day reminder, if audio has been playing and then stops for 2 minutes, it reminds you that this is a good time to charge. Audio stops when you pause your music, take the headphones off (on headphones that pause automatically) or switch them off. You get at most one break reminder a day.
- **Charging cancels reminders.** If a later reading is back at 50% or more, the rest of the day's reminders are cancelled.
- **Coloured tray icon.** The icon shows the battery level, and the tray menu shows the exact percentage and whether a reminder is pending.

  | Icon | Battery |
  |---|---|
  | <img src="docs/icons/white.svg" width="32" alt="White headphones"> | 75% and up |
  | <img src="docs/icons/green.svg" width="32" alt="Green headphones"> | 50–74% |
  | <img src="docs/icons/amber.svg" width="32" alt="Amber headphones"> | 30–49% |
  | <img src="docs/icons/red.svg" width="32" alt="Red headphones"> | below 30% |
  | <img src="docs/icons/disconnected.svg" width="32" alt="Grey headphones with a line through them"> | disconnected |

- **Spoken alerts.** When the battery drops into amber or red, it plays an alarm sound (digitone for amber, sonar for red), then says "Headphones at X percent". Any music that's playing is paused first and resumed afterwards. If no music is playing at the time, the alert waits until music next plays while the headphones are connected, and then announces the latest level. If you charge the headphones above that level first, the alert is dropped. The speech uses [Piper](https://github.com/OHF-Voice/piper1-gpl) with the `en_GB-alba-medium` voice, and is generated on your computer.

  Samples: [amber alert](docs/announcement-amber.mp3) (digitone, "Headphones at 45 percent") · [red alert](docs/announcement-red.mp3) (sonar, "Headphones at 25 percent")

## Requirements

Built for Ubuntu with GNOME, but should work on other Linux desktops that support tray icons.

- **BlueZ:** the standard Linux Bluetooth service. Your headphones need to report their battery level to it; see [Troubleshooting](#troubleshooting).
- **PipeWire:** used to play the alert sounds.
- **A tray:** on GNOME, the AppIndicator extension (`ubuntu-appindicators@ubuntu.com` is enabled by default on Ubuntu).
- **Packages:**
  ```
  sudo apt install python3-gi python3-dbus python3-venv gir1.2-notify-0.7 \
      gir1.2-ayatanaappindicator3-0.1 pipewire-bin gnome-clocks
  ```
  `gnome-clocks` provides the alarm sounds.

## Installation

1. Pair your headphones with your computer.
2. Run the install script in a terminal:
   ```
   ./install.sh
   ```
   It lists your paired headphones and asks which to watch, and what time you'd like the end-of-day reminder:
   ```
   Which headphones should be watched?
     1) My Headphones (00:11:22:33:44:55)
     2) My Earbuds (66:77:88:99:AA:BB)
   Choice: 1
   Time for the end-of-day reminder, HH:MM [17:25]: 17:00
   ```
   Your answers are saved in `~/.config/headphone-battery-reminder/config`:
   ```
   device = 00:11:22:33:44:55
   remind_at = 17:00
   ```
   It copies the app to `~/.local/bin/headphone-battery-reminder` and adds an autostart entry, so the app starts each time you log in. It also installs Piper into its own Python environment and downloads the voice (about 63 MB) into `~/.local/share/headphone-battery-reminder/`.
3. Start the app now instead of waiting for your next login:
   ```
   setsid -f ~/.local/bin/headphone-battery-reminder
   ```

Re-run `./install.sh` to change the headphones or reminder time, or after changing the code; press Enter to keep the current settings. It skips Piper and the voice if they're already installed. Then restart the app.

## Options

```
headphone-battery-reminder [--device ADDRESS] [--threshold PERCENT] [--at HH:MM] [--break-after MINUTES]
```

| Option | Default | Meaning |
|---|---|---|
| `--device` | the address chosen by `install.sh` | Bluetooth address of the headphones |
| `--threshold` | `50` | Remind when the battery is below this percentage |
| `--at` | the time chosen by `install.sh`, or `17:25` | Time of the end-of-day reminder |
| `--break-after` | `2` | Minutes without audio that count as a break |

Options given on the command line override the config file. To use other options at login, add them to the `Exec=` line in `~/.config/autostart/headphone-battery-reminder.desktop`.

The tray colour levels, alert sounds and voice are set by constants near the top of `headphone_battery_reminder.py`.

## How it works

Every 30 seconds the app reads the headphones' connection state and battery level from BlueZ over D-Bus. If you have more than one Bluetooth adapter, it uses whichever one the headphones are connected through. "Playing" means BlueZ reports the headphones' audio connection as active. This happens whenever an app has audio open, even if it's silent.

Media players are checked, paused and resumed through MPRIS, the standard media-control interface. "Music playing", for the spoken alerts, means a media player reports that it's playing. To resume, the app toggles play/pause rather than sending "Play", because some players (such as Strawberry) ignore "Play" while paused. It only resumes players that it paused and that are still paused.

The app saves its state, including today's low reading, which reminders have been shown and any alert waiting for music, in `~/.local/state/headphone-battery-reminder/state.json`, so a restart doesn't lose it.

## Troubleshooting

- **The tray always says "not connected".** Check that the headphones report a battery level while connected:
  ```
  busctl tree org.bluez
  busctl get-property org.bluez /org/bluez/hciN/dev_XX_XX_XX_XX_XX_XX org.bluez.Battery1 Percentage
  ```
  Use the adapter (`hciN`) shown under your device in the tree. If it says "No such interface", your Bluetooth setup isn't passing the headphones' battery level on to BlueZ.
- **No tray icon.** Install `gir1.2-ayatanaappindicator3-0.1` and make sure the AppIndicator GNOME extension is enabled. Without a tray icon, the app still sends notifications.
- **No voice, only the alarm sound.** Piper isn't installed. Re-run `./install.sh`.
- **Seeing errors.** Run `~/.local/bin/headphone-battery-reminder` in a terminal to see its error messages.

## Uninstalling

```
pkill -f '^python3 .*/headphone-battery-reminder$'
rm ~/.local/bin/headphone-battery-reminder ~/.config/autostart/headphone-battery-reminder.desktop
rm -r ~/.local/share/headphone-battery-reminder ~/.local/state/headphone-battery-reminder \
    ~/.cache/headphone-battery-reminder ~/.config/headphone-battery-reminder
```
