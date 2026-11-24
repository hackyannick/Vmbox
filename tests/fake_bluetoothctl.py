"""Stand-in for an interactive ``bluetoothctl`` session (used by test_bluetooth).

Reads commands from stdin like bluetoothctl does with a pipe, logs them to
$FAKE_BT_LOG and answers in bluetoothctl's style, including colour codes.
$FAKE_BT_MODE: "ok" (default), "confirm" (asks for a passkey confirmation),
"authfail" (pairing fails), "invisible" (the device never shows up)."""

import os
import sys
import threading
import time

MAC = "F2:9C:31:0B:5E:10"
MODE = os.environ.get("FAKE_BT_MODE", "ok")
LOG = open(os.environ["FAKE_BT_LOG"], "a", buffering=1)
PROMPT = "\x1b[0;94m[bluetooth]\x1b[0m# "
confirmed = threading.Event()


def say(text):
    sys.stdout.write(PROMPT + text + "\n")
    sys.stdout.flush()


def advertise():
    time.sleep(0.3)
    if MODE != "invisible":
        say("[\x1b[0;92mNEW\x1b[0m] Device %s M650 L" % MAC)


for line in sys.stdin:
    cmd = line.strip()
    LOG.write(cmd + "\n")
    if cmd.startswith("agent"):
        say("Agent registered")
    elif cmd == "default-agent":
        say("Default agent request successful")
    elif cmd == "scan on":
        say("Discovery started")
        threading.Thread(target=advertise, daemon=True).start()
    elif cmd == "scan off":
        say("Discovery stopped")
    elif cmd == "yes":
        confirmed.set()
    elif cmd.startswith("pair"):
        say("Attempting to pair with %s" % MAC)
        if MODE == "authfail":
            say("Failed to pair: org.bluez.Error.AuthenticationFailed")
            continue
        if MODE == "confirm":
            say("[agent] Confirm passkey 123456 (yes/no):")

            def finish():
                if confirmed.wait(5):
                    say("[CHG] Device %s Paired: yes" % MAC)
                    say("Pairing successful")
            threading.Thread(target=finish, daemon=True).start()
            continue
        say("[CHG] Device %s Paired: yes" % MAC)
        say("Pairing successful")
    elif cmd.startswith("trust"):
        say("[CHG] Device %s Trusted: yes" % MAC)
        say("Changing %s trust succeeded" % MAC)
    elif cmd.startswith("connect"):
        say("Attempting to connect to %s" % MAC)
        say("[CHG] Device %s Connected: yes" % MAC)
        say("Connection successful")
