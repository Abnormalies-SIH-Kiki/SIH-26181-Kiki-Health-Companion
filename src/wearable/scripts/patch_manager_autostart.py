"""Auto-start the esp32 profile when the manager launches.

The manager has always come up idle and waited for a /start request -- which the
Pi sent, because the Pi was the thing that booted Kiki. On the ESP32 route there
is no Pi: the board can only connect to a gateway that is already running, so
after a laptop reboot it sits on "Reconnecting" until someone curls the manager
by hand. That happened overnight.

Only the esp32 profile auto-starts. The laptop profile keeps its on-demand
behaviour, so selecting it does not load a 26B model at every boot for nobody.
"""
from pathlib import Path

p = Path("/home/vaibhav/kiki_servers/server_manager.py")
t = p.read_text()

if "_autostart" in t:
    print("already patched")
    raise SystemExit(0)

old = '''def main():
    os.makedirs(LOGDIR, exist_ok=True)
    srv = http.server.ThreadingHTTPServer(("0.0.0.0", PORT), Handler)
    log(f"kiki server manager listening on :{PORT}")
    srv.serve_forever()'''

new = '''def _autostart():
    """Bring the saved profile up on launch, for profiles that need it.

    The ESP32 board cannot ask for this: it can only connect to a gateway that
    already exists, so without an auto-start a laptop reboot leaves it showing
    "Reconnecting" forever. The laptop profile is left on demand, because there
    something else does the asking and a 26B model should not load for nobody.
    """
    if _profile != "esp32":
        log(f"profile '{_profile}' starts on request; not auto-starting")
        return
    log(f"auto-starting profile '{_profile}' (nothing else will ask for it)")
    restart_all(_profile)


def main():
    os.makedirs(LOGDIR, exist_ok=True)
    srv = http.server.ThreadingHTTPServer(("0.0.0.0", PORT), Handler)
    log(f"kiki server manager listening on :{PORT}")
    # After the listener exists, so /status answers while the models load.
    threading.Thread(target=_autostart, daemon=True).start()
    srv.serve_forever()'''

assert old in t, "main() not found in the expected shape"
t = t.replace(old, new, 1)
p.write_text(t)
print("patched autostart")
