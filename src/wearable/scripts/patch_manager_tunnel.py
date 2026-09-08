"""Supervise the Cloudflare tunnel alongside the other services.

The gateway, llama, Whisper and TTS all come back after a laptop reboot because
`server_manager.py` owns them and its unit is enabled with lingering. The tunnel
did not: it was always started by hand, reparented to init, and simply gone
after a restart. A board sitting at home never noticed. A board somewhere else
noticed immediately -- the only way back in was the Tailscale funnel, which
works but measured 447 ms against Cloudflare's 20.6 ms.

A quick tunnel is renamed every time it starts, so this is not merely "keep it
running": `run_tunnel.sh` rewrites public_uri.txt with the new hostname, the
gateway reads that file on every device connection and hands it over in
hello_ack, and the board moves to it immediately rather than waiting for the
funnel to fail -- which it would not do, because the funnel is not failing.
Together that turns a reboot from "20x slower until someone notices" into a few
seconds on the funnel.

Health is a file rather than a port: cloudflared holds no local listener, and
the URL file is what the rest of the system actually needs. cloudflared being
alive says nothing about whether it ever got a hostname.

Idempotent -- safe to run repeatedly.
"""
from pathlib import Path

MANAGER = Path("/home/vaibhav/kiki_servers/server_manager.py")

ENTRY = '''    "tunnel": {
        "cwd": f"{HOME}/KikiESP32",
        "cmd": f"{HOME}/KikiESP32/scripts/run_tunnel.sh --supervised",
        "health": "file",
        "health_file": f"{HOME}/KikiESP32/public_uri.txt",
        "kill_pattern": "cloudflared tunnel --url",
        # Cloudflare has to register the tunnel and issue a hostname first,
        # which is a few seconds of network round trips.
        "start_timeout": 90,
    },
'''

# Ahead of the gateway so public_uri.txt exists before the first device
# connects, and still leaving the gateway last, which its own comment asks for.
OLD_PROFILE = '"esp32":  {"services": ("llama", "tts", "whisper", "gateway"),'
NEW_PROFILE = '"esp32":  {"services": ("llama", "tts", "whisper", "tunnel", "gateway"),'

# _healthy() knows "http" and falls through to a TCP port check. A tunnel has
# neither. Inserted after `svc` is bound, not before it.
OLD_HEALTH = '''def _healthy(name):
    svc = SERVICES[name]
'''
NEW_HEALTH = '''def _healthy(name):
    svc = SERVICES[name]
    if svc.get("health") == "file":
        path = svc.get("health_file", "")
        return bool(path) and os.path.exists(path)
'''

# Two more places assume every service owns a port. Both raise KeyError on the
# tunnel, and the stop_all() one is fatal: stop_all() is the first thing
# restart_all() does, so the whole profile died with "restart failed: 'port'"
# and the board sat on "Reconnecting" with no gateway to reach at all.
OLD_FUSER = '''    # anything else still holding our ports
    for svc in SERVICES.values():
        subprocess.run(["fuser", "-k", f"{svc['port']}/tcp"], check=False,
                       stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
'''
NEW_FUSER = '''    # anything else still holding our ports. Not every service has one -- the
    # tunnel is a client, it listens on nothing -- and a bare svc["port"] here
    # raised KeyError for the whole of stop_all(), which is the first thing
    # restart_all() does, so a portless service silently broke every restart.
    for svc in SERVICES.values():
        port = svc.get("port")
        if port is None:
            continue
        subprocess.run(["fuser", "-k", f"{port}/tcp"], check=False,
                       stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
'''

OLD_STATUS = '''                h = _healthy(name)
                services[name] = {
                    "running": _proc_running(name) or _tcp_alive(SERVICES[name]["port"]),
'''
NEW_STATUS = '''                h = _healthy(name)
                port = SERVICES[name].get("port")
                services[name] = {
                    # A portless service (the tunnel) can only be judged by its
                    # process; there is no socket to knock on.
                    "running": _proc_running(name) or (
                        port is not None and _tcp_alive(port)),
'''


def main() -> int:
    text = MANAGER.read_text()
    if '"tunnel": {' in text:
        print("already patched")
        return 0

    for needle in ('    "gateway": {', OLD_PROFILE, OLD_HEALTH,
                   OLD_FUSER, OLD_STATUS):
        if needle not in text:
            print(f"manager layout changed; not patching (missing: {needle[:40]!r})")
            return 1

    text = text.replace('    "gateway": {', ENTRY + '    "gateway": {', 1)
    text = text.replace(OLD_PROFILE, NEW_PROFILE, 1)
    text = text.replace(OLD_HEALTH, NEW_HEALTH, 1)
    text = text.replace(OLD_FUSER, NEW_FUSER, 1)
    text = text.replace(OLD_STATUS, NEW_STATUS, 1)
    MANAGER.write_text(text)
    print(f"patched {MANAGER}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
