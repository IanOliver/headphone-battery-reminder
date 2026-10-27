#!/usr/bin/env python3
"""Tray app that reminds you to charge your Bluetooth headphones.

Whenever the headphones are connected, their battery level is read from BlueZ.
If it is below the threshold, a reminder is scheduled for the configured time
that day (17:25 by default). If the low reading happens after that time, the
reminder is shown straight away. At most one reminder is shown per day.

Before that time, taking a break (audio stopping for a couple of minutes,
e.g. pausing music or taking the headphones off) gives an earlier reminder,
at most once a day. A later reading back above the threshold means they were
charged, and cancels the day's reminders.

When the battery drops into the amber or red level, a sound is played and the
level is spoken (using Piper, set up by install.sh), with any playing media
paused around them. If no music is playing then, this waits until it is.
"""

import argparse
import datetime as dt
import json
import os
import sys
from pathlib import Path

import dbus
import gi
from dbus.mainloop.glib import DBusGMainLoop

gi.require_version("Gtk", "3.0")
gi.require_version("Notify", "0.7")
from gi.repository import Gio, GLib, Gtk, Notify  # noqa: E402

try:
    gi.require_version("AyatanaAppIndicator3", "0.1")
    from gi.repository import AyatanaAppIndicator3 as AppIndicator  # noqa: E402
except (ValueError, ImportError):
    AppIndicator = None

APP_ID = "headphone-battery-reminder"
DEFAULT_THRESHOLD = 50
DEFAULT_NOTIFY_AT = "17:25"
DEFAULT_BREAK_MINUTES = 2
POLL_SECONDS = 30
ICON = "audio-headphones-symbolic"
CONFIG_FILE = Path(os.environ.get("XDG_CONFIG_HOME", Path.home() / ".config")) / APP_ID / "config"  # written by install.sh
STATE_FILE = Path(os.environ.get("XDG_STATE_HOME", Path.home() / ".local/state")) / APP_ID / "state.json"
CACHE_DIR = Path(os.environ.get("XDG_CACHE_HOME", Path.home() / ".cache")) / APP_ID
ICON_DIR = CACHE_DIR / "icons"
SPEECH_FILE = CACHE_DIR / "speech.wav"

# Piper text-to-speech, set up by install.sh.
DATA_DIR = Path(os.environ.get("XDG_DATA_HOME", Path.home() / ".local/share")) / APP_ID
PIPER_PYTHON = DATA_DIR / "piper-venv/bin/python"
VOICES_DIR = DATA_DIR / "voices"
VOICE = "en_GB-alba-medium"

# Tray icon colour by battery level: (minimum percentage, icon name, colour).
BATTERY_COLOURS = [
    (75, f"{APP_ID}-white", "#ffffff"),
    (50, f"{APP_ID}-green", "#33d17a"),
    (30, f"{APP_ID}-amber", "#ffa000"),
    (0, f"{APP_ID}-red", "#e01b24"),
]
# Sounds played when the battery drops into these levels, keyed by icon name.
ALERT_SOUNDS = {
    f"{APP_ID}-amber": "/usr/share/sounds/gnome/default/alarms/digitone.oga",
    f"{APP_ID}-red": "/usr/share/sounds/gnome/default/alarms/sonar.oga",
}
PAUSE_SETTLE_MS = 500  # gap between pausing music and the alert, and after it before resuming
DISCONNECTED_ICON = f"{APP_ID}-disconnected"
DISCONNECTED_COLOUR = "#8a8a8a"
HEADPHONES_SVG = """<svg xmlns="http://www.w3.org/2000/svg" width="16" height="16" viewBox="0 0 16 16">
  <path d="M2.75 10V8a5.25 5.25 0 0 1 10.5 0v2" fill="none" stroke="{colour}" stroke-width="1.5" stroke-linecap="round"/>
  <rect x="1.5" y="9" width="3.5" height="5.5" rx="1.2" fill="{colour}"/>
  <rect x="11" y="9" width="3.5" height="5.5" rx="1.2" fill="{colour}"/>
</svg>
"""
# The headphones with a gap cut out around a diagonal slash, so it reads on any panel colour.
DISCONNECTED_SVG = """<svg xmlns="http://www.w3.org/2000/svg" width="16" height="16" viewBox="0 0 16 16">
  <mask id="gap">
    <rect width="16" height="16" fill="white"/>
    <path d="M1.5 1.5L14.5 14.5" stroke="black" stroke-width="3.5" stroke-linecap="round"/>
  </mask>
  <g mask="url(#gap)">
    <path d="M2.75 10V8a5.25 5.25 0 0 1 10.5 0v2" fill="none" stroke="{colour}" stroke-width="1.5" stroke-linecap="round"/>
    <rect x="1.5" y="9" width="3.5" height="5.5" rx="1.2" fill="{colour}"/>
    <rect x="11" y="9" width="3.5" height="5.5" rx="1.2" fill="{colour}"/>
  </g>
  <path d="M1.5 1.5L14.5 14.5" stroke="{colour}" stroke-width="1.5" stroke-linecap="round"/>
</svg>
"""


def battery_level(percentage):
    """Index into BATTERY_COLOURS for a percentage; higher is lower battery."""
    return next(i for i, (minimum, _, _) in enumerate(BATTERY_COLOURS) if percentage >= minimum)


def run_command(argv, callback):
    """Run a command without blocking, then call callback(succeeded)."""
    try:
        process = Gio.Subprocess.new([str(arg) for arg in argv], Gio.SubprocessFlags.NONE)
    except GLib.Error as e:
        print(f"Could not run {argv[0]}: {e}", file=sys.stderr)
        callback(False)
        return

    def finished(process, result):
        try:
            succeeded = process.wait_check_finish(result)
        except GLib.Error as e:
            print(f"{argv[0]} failed: {e}", file=sys.stderr)
            succeeded = False
        callback(succeeded)

    process.wait_check_async(None, finished)


def read_device(bus, address):
    """Return (name, connected, percentage-or-None, playing) for the device, or None if unknown to BlueZ.

    With several Bluetooth adapters the device is listed once per adapter, so
    the entry that is actually connected wins. "Playing" means an audio
    transport to the device is active, i.e. some app has a stream open.
    """
    manager = dbus.Interface(bus.get_object("org.bluez", "/"), "org.freedesktop.DBus.ObjectManager")
    objects = manager.GetManagedObjects()
    result = None
    for path, interfaces in objects.items():
        device = interfaces.get("org.bluez.Device1")
        if not device or str(device.get("Address", "")).upper() != address:
            continue
        battery = interfaces.get("org.bluez.Battery1")
        percentage = int(battery["Percentage"]) if battery and "Percentage" in battery else None
        name = str(device.get("Alias") or device.get("Name") or address)
        playing = any(
            transport.get("Device") == path and transport.get("State") == "active"
            for transport in (i.get("org.bluez.MediaTransport1") for i in objects.values())
            if transport
        )
        result = name, bool(device.get("Connected", False)), percentage, playing
        if result[1]:
            break
    return result


class Reminder:
    def __init__(self, address, threshold, notify_at, break_after):
        self.address = address
        self.threshold = threshold
        self.notify_at = notify_at
        self.break_after = break_after
        self.bus = dbus.SystemBus()
        self.session_bus = dbus.SessionBus()
        self.name = address
        self.connected = False
        self.percentage = None
        self.was_playing = False  # audio has played since the last break
        self.quiet_since = None  # when audio stopped, if it has
        self.state = self._load_state()

        Notify.init(APP_ID)
        self.indicator = self._build_indicator() if AppIndicator else None
        if not self.indicator:
            print("AyatanaAppIndicator3 not available; running without a tray icon.", file=sys.stderr)

        self.tick()
        GLib.timeout_add_seconds(POLL_SECONDS, self.tick)

    # --- state ---------------------------------------------------------------

    def _load_state(self):
        try:
            return json.loads(STATE_FILE.read_text())
        except (OSError, ValueError):
            return {}

    def _save_state(self):
        STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
        STATE_FILE.write_text(json.dumps(self.state))

    def _pending_today(self):
        today = dt.date.today().isoformat()
        return self.state.get("low_date") == today and self.state.get("notified_date") != today

    # --- main loop -----------------------------------------------------------

    def tick(self):
        try:
            info = read_device(self.bus, self.address)
        except dbus.DBusException as e:
            print(f"Could not query BlueZ: {e}", file=sys.stderr)
            info = None

        now = dt.datetime.now()
        today = now.date().isoformat()
        playing = False
        if info:
            self.name, self.connected, percentage, playing = info
            if self.connected and percentage is not None:
                self.percentage = percentage
                level = battery_level(percentage)
                previous = self.state.get("last_percentage")
                if previous != percentage:
                    if previous is not None and level > battery_level(previous) and BATTERY_COLOURS[level][1] in ALERT_SOUNDS:
                        self.state["pending_alert"] = level
                    self.state["last_percentage"] = percentage
                    self._save_state()

                # Alerts wait until music is playing, so they're heard, and use the latest level.
                pending = self.state.get("pending_alert")
                if pending is not None and (level < pending or self._media_playing()):
                    if level >= pending:
                        self._alert(ALERT_SOUNDS[BATTERY_COLOURS[level][1]], f"Headphones at {percentage} percent.")
                    del self.state["pending_alert"]  # played, or charged since
                    self._save_state()
                if percentage < self.threshold:
                    if self.state.get("low_date") != today or percentage < self.state.get("low_percentage", 101):
                        self.state.update(low_date=today, low_percentage=percentage)
                        self._save_state()
                elif self._pending_today():
                    # Back above the threshold, so it has been charged: no reminder needed.
                    self.state.pop("low_date", None)
                    self.state.pop("low_percentage", None)
                    self._save_state()

        # A break is audio stopping (paused, headphones taken off or switched off) for a while.
        if playing:
            self.was_playing = True
            self.quiet_since = None
        elif self.was_playing and self.quiet_since is None:
            self.quiet_since = now
        on_break = self.quiet_since is not None and now - self.quiet_since >= self.break_after

        if self._pending_today():
            if now.time() >= self.notify_at:
                percentage = self.state.get("low_percentage")
                if self._notify(f"Charge your {self.name}", f"Battery was at {percentage}% today (below {self.threshold}%)."):
                    self.state["notified_date"] = today
                    self._save_state()
            elif on_break and self.state.get("break_notified_date") != today:
                percentage = self.state.get("low_percentage")
                if self._notify(
                    f"Good time to charge your {self.name}",
                    f"Battery is at {percentage}%. You're taking a break, so charge them now to skip the {self.notify_at:%H:%M} reminder.",
                ):
                    self.state["break_notified_date"] = today
                    self._save_state()
        if on_break:
            self.was_playing = False
            self.quiet_since = None

        self._update_indicator()
        return True  # keep the GLib timeout running

    def _notify(self, title, body):
        """Show a notification, returning whether it was shown."""
        notification = Notify.Notification.new(title, body, ICON)
        notification.set_urgency(Notify.Urgency.NORMAL)
        try:
            notification.show()
        except GLib.Error as e:
            print(f"Could not show notification: {e}", file=sys.stderr)
            return False
        return True

    # --- alert sound ---------------------------------------------------------

    def _alert(self, sound, text):
        """Play a sound then speak the text, pausing any playing media around them."""
        commands = [["pw-play", sound]]

        def start():
            paused = self._pause_players()
            settle = PAUSE_SETTLE_MS if paused else 0

            def resume():
                # Some players (e.g. Strawberry) ignore Play while paused, so toggle
                # instead, but only if still paused, in case you've changed it since.
                for player in paused:
                    if self._player_status(player) == "Paused":
                        self._call_player(player, "PlayPause")
                return False

            def play(remaining):
                if remaining:
                    run_command(remaining[0], lambda _: play(remaining[1:]))
                else:
                    GLib.timeout_add(settle, resume)
                return False

            GLib.timeout_add(settle, play, commands)

        def spoken(succeeded):
            if succeeded:
                commands.append(["pw-play", SPEECH_FILE])
            start()

        # Generate the speech first (it takes a few seconds), so the music isn't paused while waiting.
        if PIPER_PYTHON.exists():
            CACHE_DIR.mkdir(parents=True, exist_ok=True)
            run_command(
                [PIPER_PYTHON, "-I", "-m", "piper", "--data-dir", VOICES_DIR, "-m", VOICE, "-f", SPEECH_FILE, "--", text],
                spoken,
            )
        else:
            print(f"Piper not found at {PIPER_PYTHON}; run install.sh. Playing the sound only.", file=sys.stderr)
            start()

    def _playing_players(self):
        """Bus names of the MPRIS media players that are playing."""
        try:
            names = self.session_bus.list_names()
        except dbus.DBusException:
            return []
        return [n for n in names if n.startswith("org.mpris.MediaPlayer2.") and self._player_status(n) == "Playing"]

    def _media_playing(self):
        return bool(self._playing_players())

    def _pause_players(self):
        """Pause every MPRIS media player that is playing, returning their bus names."""
        return [name for name in self._playing_players() if self._call_player(name, "Pause")]

    def _player_status(self, name):
        try:
            player = self.session_bus.get_object(name, "/org/mpris/MediaPlayer2")
            return str(player.Get("org.mpris.MediaPlayer2.Player", "PlaybackStatus", dbus_interface=dbus.PROPERTIES_IFACE))
        except dbus.DBusException:
            return None

    def _call_player(self, name, method):
        try:
            player = self.session_bus.get_object(name, "/org/mpris/MediaPlayer2")
            getattr(player, method)(dbus_interface="org.mpris.MediaPlayer2.Player")
            return True
        except dbus.DBusException as e:
            print(f"Could not {method.lower()} {name}: {e}", file=sys.stderr)
            return False

    # --- tray ----------------------------------------------------------------

    def _build_indicator(self):
        ICON_DIR.mkdir(parents=True, exist_ok=True)
        for _, name, colour in BATTERY_COLOURS:
            (ICON_DIR / f"{name}.svg").write_text(HEADPHONES_SVG.format(colour=colour))
        (ICON_DIR / f"{DISCONNECTED_ICON}.svg").write_text(DISCONNECTED_SVG.format(colour=DISCONNECTED_COLOUR))

        indicator = AppIndicator.Indicator.new(APP_ID, DISCONNECTED_ICON, AppIndicator.IndicatorCategory.HARDWARE)
        indicator.set_icon_theme_path(str(ICON_DIR))
        self.icon_name = DISCONNECTED_ICON
        indicator.set_status(AppIndicator.IndicatorStatus.ACTIVE)
        indicator.set_title("Headphone Battery Reminder")

        menu = Gtk.Menu()
        self.status_item = Gtk.MenuItem(label="")
        self.status_item.set_sensitive(False)
        self.reminder_item = Gtk.MenuItem(label="")
        self.reminder_item.set_sensitive(False)
        check_item = Gtk.MenuItem(label="Check now")
        check_item.connect("activate", lambda _: self.tick())
        quit_item = Gtk.MenuItem(label="Quit")
        quit_item.connect("activate", lambda _: Gtk.main_quit())
        for item in (self.status_item, self.reminder_item, Gtk.SeparatorMenuItem(), check_item, quit_item):
            menu.append(item)
        menu.show_all()
        indicator.set_menu(menu)
        return indicator

    def _update_indicator(self):
        if not self.indicator:
            return
        if self.connected and self.percentage is not None:
            status = f"{self.name}: {self.percentage}%"
        elif self.percentage is not None:
            status = f"{self.name}: not connected (last {self.percentage}%)"
        else:
            status = f"{self.name}: not connected"
        self.status_item.set_label(status)

        # Coloured by level while connected; grey and struck through otherwise.
        icon_name = DISCONNECTED_ICON
        if self.connected and self.percentage is not None:
            icon_name = BATTERY_COLOURS[battery_level(self.percentage)][1]
        if icon_name != self.icon_name:
            self.icon_name = icon_name
            self.indicator.set_icon_full(icon_name, status)

        if self._pending_today():
            reminder = f"Reminder pending for {self.notify_at:%H:%M}"
            if self.state.get("break_notified_date") != dt.date.today().isoformat():
                reminder += " (or your next break)"
        elif self.state.get("notified_date") == dt.date.today().isoformat():
            reminder = "Reminded today"
        else:
            reminder = f"No reminder needed (threshold {self.threshold}%)"
        self.reminder_item.set_label(reminder)


def read_config():
    """Settings from the config file written by install.sh, as {key: value}."""
    config = {}
    try:
        lines = CONFIG_FILE.read_text().splitlines()
    except OSError:
        return config
    for line in lines:
        key, sep, value = line.partition("=")
        if sep and not key.strip().startswith("#"):
            config[key.strip()] = value.strip()
    return config


def main():
    config = read_config()
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument(
        "--device", default=config.get("device"), help=f"Bluetooth MAC address (default: from {CONFIG_FILE})"
    )
    parser.add_argument("--threshold", type=int, default=DEFAULT_THRESHOLD, help="remind below this %% (default: %(default)s)")
    parser.add_argument(
        "--at",
        default=config.get("remind_at", DEFAULT_NOTIFY_AT),
        help=f"time of day to remind, HH:MM (default: from {CONFIG_FILE}, or {DEFAULT_NOTIFY_AT})",
    )
    parser.add_argument(
        "--break-after",
        type=int,
        default=DEFAULT_BREAK_MINUTES,
        help="minutes without audio that count as a break (default: %(default)s)",
    )
    args = parser.parse_args()

    if not args.device:
        parser.error("no headphones chosen: run install.sh, or pass --device")
    try:
        notify_at = dt.datetime.strptime(args.at, "%H:%M").time()
    except ValueError:
        parser.error(f"reminder time {args.at!r} isn't in HH:MM format")
    DBusGMainLoop(set_as_default=True)
    Reminder(args.device.upper(), args.threshold, notify_at, dt.timedelta(minutes=args.break_after))
    Gtk.main()


if __name__ == "__main__":
    main()
