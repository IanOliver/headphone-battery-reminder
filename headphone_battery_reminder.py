#!/usr/bin/env python3
"""Tray app that reminds you to charge your Bluetooth headphones.

Whenever the headphones are connected, their battery level is read from BlueZ.
If it is below the threshold, a reminder is scheduled for the configured time
that day (17:25 by default). If the low reading happens after that time, the
reminder is shown straight away. At most one reminder is shown per day.
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
from gi.repository import GLib, Gtk, Notify  # noqa: E402

try:
    gi.require_version("AyatanaAppIndicator3", "0.1")
    from gi.repository import AyatanaAppIndicator3 as AppIndicator  # noqa: E402
except (ValueError, ImportError):
    AppIndicator = None

APP_ID = "headphone-battery-reminder"
DEFAULT_DEVICE = "AC:80:0A:6D:F3:4E"  # WH-1000XM4
DEFAULT_THRESHOLD = 50
DEFAULT_NOTIFY_AT = "17:25"
POLL_SECONDS = 30
ICON = "audio-headphones-symbolic"
STATE_FILE = Path(os.environ.get("XDG_STATE_HOME", Path.home() / ".local/state")) / APP_ID / "state.json"


def read_battery(bus, address):
    """Return (name, connected, percentage-or-None) for the device, or None if unknown to BlueZ.

    With several Bluetooth adapters the device is listed once per adapter, so
    the entry that is actually connected wins.
    """
    manager = dbus.Interface(bus.get_object("org.bluez", "/"), "org.freedesktop.DBus.ObjectManager")
    result = None
    for interfaces in manager.GetManagedObjects().values():
        device = interfaces.get("org.bluez.Device1")
        if not device or str(device.get("Address", "")).upper() != address:
            continue
        battery = interfaces.get("org.bluez.Battery1")
        percentage = int(battery["Percentage"]) if battery and "Percentage" in battery else None
        name = str(device.get("Alias") or device.get("Name") or address)
        result = name, bool(device.get("Connected", False)), percentage
        if result[1]:
            break
    return result


class Reminder:
    def __init__(self, address, threshold, notify_at):
        self.address = address
        self.threshold = threshold
        self.notify_at = notify_at
        self.bus = dbus.SystemBus()
        self.name = address
        self.connected = False
        self.percentage = None
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
            info = read_battery(self.bus, self.address)
        except dbus.DBusException as e:
            print(f"Could not query BlueZ: {e}", file=sys.stderr)
            info = None

        if info:
            self.name, self.connected, percentage = info
            if self.connected and percentage is not None:
                self.percentage = percentage
                if percentage < self.threshold:
                    today = dt.date.today().isoformat()
                    if self.state.get("low_date") != today or percentage < self.state.get("low_percentage", 101):
                        self.state.update(low_date=today, low_percentage=percentage)
                        self._save_state()

        now = dt.datetime.now()
        if self._pending_today() and now.time() >= self.notify_at:
            self._notify()

        self._update_indicator()
        return True  # keep the GLib timeout running

    def _notify(self):
        percentage = self.state.get("low_percentage")
        notification = Notify.Notification.new(
            f"Charge your {self.name}",
            f"Battery was at {percentage}% today (below {self.threshold}%).",
            ICON,
        )
        notification.set_urgency(Notify.Urgency.NORMAL)
        try:
            notification.show()
        except GLib.Error as e:
            print(f"Could not show notification: {e}", file=sys.stderr)
            return
        self.state["notified_date"] = dt.date.today().isoformat()
        self._save_state()

    # --- tray ----------------------------------------------------------------

    def _build_indicator(self):
        indicator = AppIndicator.Indicator.new(APP_ID, ICON, AppIndicator.IndicatorCategory.HARDWARE)
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

        if self._pending_today():
            reminder = f"Reminder pending for {self.notify_at:%H:%M}"
        elif self.state.get("notified_date") == dt.date.today().isoformat():
            reminder = "Reminded today"
        else:
            reminder = f"No reminder needed (threshold {self.threshold}%)"
        self.reminder_item.set_label(reminder)


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--device", default=DEFAULT_DEVICE, help="Bluetooth MAC address (default: %(default)s)")
    parser.add_argument("--threshold", type=int, default=DEFAULT_THRESHOLD, help="remind below this %% (default: %(default)s)")
    parser.add_argument("--at", default=DEFAULT_NOTIFY_AT, help="time of day to remind, HH:MM (default: %(default)s)")
    args = parser.parse_args()

    notify_at = dt.datetime.strptime(args.at, "%H:%M").time()
    DBusGMainLoop(set_as_default=True)
    Reminder(args.device.upper(), args.threshold, notify_at)
    Gtk.main()


if __name__ == "__main__":
    main()
