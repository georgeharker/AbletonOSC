#!/usr/bin/env python3
"""Live check that two OSC clients can hold listeners at once.

Neither a request/response client nor a listener-free one exercises the
per-client listener path, so this stands in for the second subscriber.

Two clients each bind their own port, both subscribe to the same property,
then one of them changes it. Against patched AbletonOSC both clients receive
the update; against stock, the second subscription evicts the first and only
one (or neither) does.

    python3 probe_multiclient.py            # track 0 volume
    python3 probe_multiclient.py --track 1

Requires Ableton running with AbletonOSC enabled. Uses ports 11003/11004 so it
does not disturb anything already on 11001.
"""
import argparse
import socket
import sys
import threading
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from pythonosc.osc_message import OscMessage
from pythonosc.osc_message_builder import OscMessageBuilder

LIVE = ("127.0.0.1", 11000)


class Probe:
    """One OSC client: sends from, and listens on, a single bound socket."""

    def __init__(self, name, port):
        self.name = name
        self.received = []
        self.sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self.sock.bind(("127.0.0.1", port))
        self.port = self.sock.getsockname()[1]
        self.running = True
        threading.Thread(target=self._listen, daemon=True).start()

    def _listen(self):
        while self.running:
            try:
                data, _ = self.sock.recvfrom(65536)
            except OSError:
                return
            try:
                msg = OscMessage(data)
            except Exception:
                continue
            self.received.append((msg.address, tuple(msg.params)))
            print("  [%-2s :%d]  %s %s" % (self.name, self.port, msg.address, tuple(msg.params)))

    def send(self, address, *args):
        builder = OscMessageBuilder(address=address)
        for arg in args:
            builder.add_arg(arg)
        self.sock.sendto(builder.build().dgram, LIVE)

    def close(self):
        self.running = False
        self.sock.close()


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--track", type=int, default=0)
    args = parser.parse_args()
    track = args.track

    a = Probe("A", 11003)
    b = Probe("B", 11004)

    try:
        #--------------------------------------------------------------------------------
        # Use a query that RETURNS a value: those are routed to the requesting client.
        # /live/test instead calls send() with no address, so its reply always goes to
        # the default response port and would never reach this probe.
        #--------------------------------------------------------------------------------
        print("Checking Live is reachable...")
        a.send("/live/song/get/tempo")
        time.sleep(0.5)
        if not a.received:
            print("FAIL  no reply from AbletonOSC on 11000 -- is Live running with it")
            print("      enabled, and is it running the reply-to-sender patch?")
            return 1
        print("  ok (tempo=%s)\n" % (a.received[-1][1],))

        print("Both clients subscribe to /live/track/get/volume (track %d)..." % track)
        a.received.clear(); b.received.clear()
        a.send("/live/track/start_listen/volume", track)
        time.sleep(0.3)
        b.send("/live/track/start_listen/volume", track)
        time.sleep(0.5)

        got_initial = [p.name for p in (a, b) if p.received]
        print("  initial value received by: %s\n" % (got_initial or "neither"))

        #--------------------------------------------------------------------------------
        # Pick a target that differs from the current value -- Live only fires the
        # listener on an actual change, so re-setting the existing value looks
        # identical to a broken listener.
        #--------------------------------------------------------------------------------
        current = None
        for probe in (a, b):
            for address, params in probe.received:
                if address == "/live/track/get/volume" and len(params) >= 2:
                    current = float(params[1])
        target = 0.8 if (current is None or current < 0.5) else 0.2

        a.received.clear(); b.received.clear()

        print("Client A sets volume %s -> %s; both should see it..." % (
            "?" if current is None else round(current, 3), target))
        a.send("/live/track/set/volume", track, target)
        time.sleep(0.8)

        a_saw = [r for r in a.received if r[0] == "/live/track/get/volume"]
        b_saw = [r for r in b.received if r[0] == "/live/track/get/volume"]

        print()
        print("  A received update: %s" % bool(a_saw))
        print("  B received update: %s" % bool(b_saw))
        print()

        if a_saw and b_saw:
            print("PASS  both clients hold listeners simultaneously.")
            rv = 0
        elif b_saw and not a_saw:
            print("FAIL  only B -- B's subscription evicted A's (stock behaviour).")
            rv = 1
        elif a_saw and not b_saw:
            print("FAIL  only A -- B never received its updates.")
            rv = 1
        else:
            print("FAIL  neither client received the change.")
            rv = 1

        print("\nCleaning up (/live/api/stop_all_listeners)...")
        for probe in (a, b):
            probe.received.clear()
            probe.send("/live/api/stop_all_listeners")
        time.sleep(0.4)
        removed = [r for p in (a, b) for r in p.received
                   if r[0] == "/live/api/stop_all_listeners"]
        if removed:
            print("  removed: %s" % [r[1] for r in removed])
        else:
            print("  no reply -- endpoint absent (running stock AbletonOSC?)")
        return rv
    finally:
        a.close()
        b.close()


if __name__ == "__main__":
    raise SystemExit(main())
