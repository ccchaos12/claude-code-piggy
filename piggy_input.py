#!/usr/bin/env python3
"""Keyboard/mouse activity -> dsh-piggy work events (macOS).

Reads the system-wide input event *counters* from CoreGraphics every few seconds.
It never sees which keys were pressed or where you clicked, only how many events
happened, so it needs no Input Monitoring / Accessibility permission.

Conversion (tune with env vars):
  every PIGGY_KEYS_PER_EVENT keystrokes   (default 200) -> one "tool" event
  every PIGGY_CLICKS_PER_EVENT clicks     (default 50)  -> one "tool" event
  every PIGGY_ACTIVE_MIN_PER_TURN minutes of activity (default 25) -> one "turn" event
At most PIGGY_MAX_PER_HOUR events per hour (default 40); the game's own daily caps still apply.

Usage:
  piggy_input.py           # run forever (used by the LaunchAgent)
  piggy_input.py --status  # show counters, progress and queue
"""
import ctypes
import hashlib
import json
import os
import sys
import time
import uuid

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import piggy_hook as outbox  # noqa: E402  (shared private outbox + HTTP delivery)

SOURCE = "input"
QUEUE_DIR = os.environ.get("PIGGY_INPUT_QUEUE") or os.path.join(
    os.path.expanduser("~"), "Library", "Application Support", "piggy-input")
STATE_FILE = os.path.join(QUEUE_DIR, "state.json")

KEYS_PER_EVENT = int(os.environ.get("PIGGY_KEYS_PER_EVENT", 200))
CLICKS_PER_EVENT = int(os.environ.get("PIGGY_CLICKS_PER_EVENT", 50))
ACTIVE_SECS_PER_TURN = int(float(os.environ.get("PIGGY_ACTIVE_MIN_PER_TURN", 25)) * 60)
MAX_PER_HOUR = int(os.environ.get("PIGGY_MAX_PER_HOUR", 40))
POLL_S = 5
IDLE_LIMIT_S = 60          # no input for this long = not active
FLUSH_EVERY_S = 30

HID = 1                    # kCGEventSourceStateHIDSystemState
KEY_DOWN = 10              # kCGEventKeyDown
CLICKS = (1, 3, 25)        # left / right / other mouse down
ANY_INPUT = 0xFFFFFFFF     # kCGAnyInputEventType

_cg = ctypes.CDLL("/System/Library/Frameworks/CoreGraphics.framework/CoreGraphics")
_cg.CGEventSourceCounterForEventType.restype = ctypes.c_uint32
_cg.CGEventSourceCounterForEventType.argtypes = [ctypes.c_int32, ctypes.c_uint32]
_cg.CGEventSourceSecondsSinceLastEventType.restype = ctypes.c_double
_cg.CGEventSourceSecondsSinceLastEventType.argtypes = [ctypes.c_int32, ctypes.c_uint32]


def read_counters():
    keys = _cg.CGEventSourceCounterForEventType(HID, KEY_DOWN)
    clicks = sum(_cg.CGEventSourceCounterForEventType(HID, t) for t in CLICKS)
    idle = _cg.CGEventSourceSecondsSinceLastEventType(HID, ANY_INPUT)
    return keys, clicks, idle


def delta(new, old):
    # Counters restart at 0 after reboot/logout (and are uint32): never go negative.
    return new - old if new >= old else new


def load_state():
    try:
        with open(STATE_FILE) as f:
            s = json.load(f)
        if isinstance(s, dict):
            return s
    except (OSError, ValueError):
        pass
    return {}


def save_state(s):
    tmp = f"{STATE_FILE}.tmp"
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w") as f:
        json.dump(s, f)
    os.replace(tmp, STATE_FILE)


def new_event(kind):
    key = json.dumps(["piggy-input:v1", kind, uuid.uuid4().hex])
    return {"id": hashlib.sha256(key.encode()).hexdigest(), "type": kind, "at": int(time.time() * 1000)}


def emit(state, kind):
    hour = int(time.time() // 3600)
    if state.get("hour") != hour:
        state["hour"], state["sent_this_hour"] = hour, 0
    if state["sent_this_hour"] >= MAX_PER_HOUR:
        return False
    outbox.enqueue(new_event(kind))
    state["sent_this_hour"] += 1
    state["total_sent"] = state.get("total_sent", 0) + 1
    return True


def step(state, keys, clicks, idle, elapsed):
    """Fold one sample into state; returns number of events queued."""
    dk = delta(keys, state.get("last_keys", keys))
    dc = delta(clicks, state.get("last_clicks", clicks))
    state["last_keys"], state["last_clicks"] = keys, clicks
    state["keys"] = state.get("keys", 0) + dk
    state["clicks"] = state.get("clicks", 0) + dc
    if idle < IDLE_LIMIT_S:
        state["active_s"] = state.get("active_s", 0) + min(elapsed, POLL_S * 3)
    sent = 0
    for field, size, kind in (("keys", KEYS_PER_EVENT, "tool"), ("clicks", CLICKS_PER_EVENT, "tool"),
                              ("active_s", ACTIVE_SECS_PER_TURN, "turn")):
        while state.get(field, 0) >= size:
            if not emit(state, kind):
                state[field] = min(state[field], size)  # hourly cap hit: drop the excess
                break
            state[field] -= size
            sent += 1
    return sent


def run():
    outbox.ensure_private_dir(QUEUE_DIR)
    state = load_state()
    last = time.monotonic()
    last_flush = 0.0
    while True:
        try:
            keys, clicks, idle = read_counters()
            now = time.monotonic()
            sent = step(state, keys, clicks, idle, now - last)
            last = now
            save_state(state)
            if sent or now - last_flush >= FLUSH_EVERY_S:
                last_flush = now
                outbox.flush()
        except Exception as e:  # keep running whatever happens
            outbox.log(f"error: {type(e).__name__}: {e}")
        time.sleep(POLL_S)


def status():
    keys, clicks, idle = read_counters()
    s = load_state()
    print(f"system counters: keys={keys} clicks={clicks} idle={idle:.0f}s")
    print(f"progress: keys {s.get('keys', 0)}/{KEYS_PER_EVENT}, clicks {s.get('clicks', 0)}/{CLICKS_PER_EVENT}, "
          f"active {s.get('active_s', 0) // 60}/{ACTIVE_SECS_PER_TURN // 60} min")
    print(f"events sent this hour: {s.get('sent_this_hour', 0)}/{MAX_PER_HOUR}, total queued: {s.get('total_sent', 0)}")
    outbox.status()


if __name__ == "__main__":
    outbox.configure(SOURCE, QUEUE_DIR)
    if "--status" in sys.argv:
        status()
    else:
        run()
