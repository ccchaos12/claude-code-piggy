#!/usr/bin/env python3
"""Claude Code hook -> dsh-piggy work events (protocol v1).

Reads a Claude Code hook payload on stdin, maps it to a work event, writes it to a
private on-disk outbox, then tries to deliver queued events to the game's local
HTTP endpoint. Never blocks or fails the Claude Code session: always exits 0 and
prints nothing on stdout.

Only metadata is sent (id, type, time). No prompt text, tool input or output.

Usage:
  piggy_hook.py            # as a Claude Code hook (payload on stdin)
  piggy_hook.py --flush    # deliver queued events now
  piggy_hook.py --status   # show endpoint and queue state
"""
import hashlib
import http.client
import json
import os
import stat
import sys
import time
import uuid

SOURCE = "claude-code"
EVENT_MAX_AGE_MS = 7 * 24 * 60 * 60 * 1000
EVENT_FUTURE_MS = 5 * 60 * 1000
EVENT_TYPES = {"message", "turn", "tool", "toolError", "agentError"}
JSON_LIMIT = 1024
REQUEST_TIMEOUT_S = 2.0
FLUSH_BUDGET_S = 3.0
MAX_PER_FLUSH = 200

# Claude Code hook event -> game work event type.
HOOK_TYPES = {
    "UserPromptSubmit": "message",
    "PostToolUse": "tool",
    "PostToolUseFailure": "toolError",
    "Stop": "turn",
    "StopFailure": "agentError",
}

HOME = os.path.expanduser("~")


def _support_dir(name):
    if sys.platform == "darwin":
        return os.path.join(HOME, "Library", "Application Support", name)
    if sys.platform == "win32":
        return os.path.join(os.environ.get("APPDATA") or os.path.join(HOME, "AppData", "Roaming"), name)
    return os.path.join(os.environ.get("XDG_CONFIG_HOME") or os.path.join(HOME, ".config"), name)


QUEUE_DIR = os.environ.get("CLAUDE_PIGGY_QUEUE") or _support_dir("claude-code-piggy")
EVENTS_DIR = os.path.join(QUEUE_DIR, "events")
BACKOFF_FILE = os.path.join(QUEUE_DIR, "retry-after")


def configure(source, queue_dir):
    """Reuse this outbox/delivery code for another event source (e.g. piggy_input.py)."""
    global SOURCE, QUEUE_DIR, EVENTS_DIR, BACKOFF_FILE
    SOURCE = source
    QUEUE_DIR = queue_dir
    EVENTS_DIR = os.path.join(queue_dir, "events")
    BACKOFF_FILE = os.path.join(queue_dir, "retry-after")


def game_dirs():
    if os.environ.get("PIGGY_GAME_DIR"):
        return [os.environ["PIGGY_GAME_DIR"]]
    if os.environ.get("PIGGY_USERDATA"):
        return [os.path.join(os.environ["PIGGY_USERDATA"], "work-endpoint")]
    if sys.platform.startswith("linux"):
        xdg = os.environ.get("XDG_DATA_HOME")
        root = xdg if xdg and os.path.isabs(xdg) else os.path.join(HOME, ".local", "share")
        primary = os.path.join(root, "piggy", "work-endpoint")
    else:
        primary = os.path.join(_support_dir("piggy"), "work-endpoint")
    return [primary] + [os.path.join(_support_dir(n), "work-endpoint") for n in ("dsh-piggy", "dsh-piggy-desktop")]


def log(msg):
    line = f"[{SOURCE}-piggy] {msg}\n"
    try:
        if sys.stderr is not None:  # pythonw.exe on Windows has no stderr
            sys.stderr.write(line)
            return
    except Exception:  # noqa: BLE001
        pass
    try:
        with open(os.path.join(QUEUE_DIR, "error.log"), "a", encoding="utf-8") as f:
            f.write(line)
    except OSError:
        pass


def try_lock(path):
    """Non-blocking exclusive lock on a file (Unix flock / Windows msvcrt). Returns the file or None."""
    f = open(path, "a+")
    try:
        if os.name == "nt":
            import msvcrt
            f.seek(0)
            msvcrt.locking(f.fileno(), msvcrt.LK_NBLCK, 1)
        else:
            import fcntl
            fcntl.flock(f, fcntl.LOCK_EX | fcntl.LOCK_NB)
        return f
    except OSError:
        f.close()
        return None


def unlock(f):
    try:
        if os.name == "nt":
            import msvcrt
            f.seek(0)
            msvcrt.locking(f.fileno(), msvcrt.LK_UNLCK, 1)
        else:
            import fcntl
            fcntl.flock(f, fcntl.LOCK_UN)
    finally:
        f.close()


# ---------- outbox ----------

def ensure_private_dir(path):
    os.makedirs(path, mode=0o700, exist_ok=True)
    st = os.lstat(path)
    if not stat.S_ISDIR(st.st_mode) or stat.S_ISLNK(st.st_mode):
        raise RuntimeError("queue path is not a real directory")
    if hasattr(os, "getuid") and st.st_uid != os.getuid():
        raise RuntimeError("queue directory owner mismatch")
    if sys.platform != "win32":
        os.chmod(path, 0o700)


def read_small_json(path):
    st = os.lstat(path)
    if not stat.S_ISREG(st.st_mode) or st.st_size > JSON_LIMIT:
        raise ValueError("invalid file")
    fd = os.open(path, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0))
    with os.fdopen(fd, "rb") as f:
        data = f.read(JSON_LIMIT + 1)
    if len(data) > JSON_LIMIT:
        raise ValueError("file too large")
    return json.loads(data.decode("utf-8"))


def enqueue(event):
    ensure_private_dir(QUEUE_DIR)
    ensure_private_dir(EVENTS_DIR)
    path = os.path.join(EVENTS_DIR, f"{event['id']}.json")
    tmp = f"{path}.{uuid.uuid4().hex[:16]}.tmp"
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    try:
        with os.fdopen(fd, "w") as f:
            json.dump(event, f)
            f.flush()
            os.fsync(f.fileno())
        try:
            os.link(tmp, path)  # create-only: never overwrite an existing record
        except FileExistsError:
            pass
    finally:
        try:
            os.unlink(tmp)
        except FileNotFoundError:
            pass


def map_hook(payload):
    kind = HOOK_TYPES.get(payload.get("hook_event_name"))
    session = payload.get("session_id")
    if not kind or not isinstance(session, str) or not session:
        return None
    # Subagent tool calls still count as tools; only top-level prompts reach UserPromptSubmit.
    tool_use = payload.get("tool_use_id")
    # Tool calls have a real stable id; prompts and turns get one per observation.
    unique = tool_use if isinstance(tool_use, str) and tool_use else uuid.uuid4().hex
    key = json.dumps(["claude-code-piggy:v1", session, kind, unique])
    return {"id": hashlib.sha256(key.encode()).hexdigest(), "type": kind, "at": int(time.time() * 1000)}


# ---------- delivery ----------

def discover():
    dirs = game_dirs()
    for i, d in enumerate(dirs):
        path = os.path.join(d, "endpoint.json")
        try:
            st = os.lstat(path)
        except FileNotFoundError:
            if i == len(dirs) - 1:
                return None
            continue
        if not stat.S_ISREG(st.st_mode) or st.st_nlink != 1:
            raise RuntimeError("endpoint.json is not a real file")
        if hasattr(os, "getuid") and (st.st_uid != os.getuid() or st.st_mode & 0o077):
            raise RuntimeError("endpoint.json is not private to this user")
        v = read_small_json(path)
        tok = v.get("token") if isinstance(v, dict) else None
        port = v.get("port") if isinstance(v, dict) else None
        if (v.get("version") != 1 or v.get("role") != "piggy-work-events" or not isinstance(port, int)
                or not 1 <= port <= 65535 or not isinstance(tok, str) or len(tok) != 64
                or any(c not in "0123456789abcdef" for c in tok)):
            raise RuntimeError("invalid endpoint.json")
        return v
    return None


def post(endpoint, event):
    body = json.dumps({"version": 1, "source": SOURCE, "id": event["id"], "type": event["type"], "at": event["at"]})
    conn = http.client.HTTPConnection("127.0.0.1", endpoint["port"], timeout=REQUEST_TIMEOUT_S)
    try:
        conn.request("POST", "/events", body=body, headers={
            "Authorization": f"Bearer {endpoint['token']}", "Content-Type": "application/json"})
        resp = conn.getresponse()
        data = resp.read(JSON_LIMIT + 1)
        return resp.status, resp.getheader("Retry-After"), data
    finally:
        conn.close()


def valid_event(e, name, now):
    return (isinstance(e, dict) and sorted(e) == ["at", "id", "type"] and isinstance(e["id"], str)
            and f"{e['id']}.json" == name and e["type"] in EVENT_TYPES
            and isinstance(e["at"], int) and 0 <= e["at"] <= now + EVENT_FUTURE_MS)


def flush():
    if not os.path.isdir(EVENTS_DIR):
        return
    try:
        if time.time() < float(open(BACKOFF_FILE).read().strip()):
            return
    except (OSError, ValueError):
        pass
    lock = try_lock(os.path.join(QUEUE_DIR, "deliver.lock"))  # one deliverer at a time
    if not lock:
        return
    try:
        endpoint = discover()
        if not endpoint:
            return
        deadline = time.monotonic() + FLUSH_BUDGET_S
        names = sorted(n for n in os.listdir(EVENTS_DIR) if n.endswith(".json") and len(n) == 69)
        for name in names[:MAX_PER_FLUSH]:
            if time.monotonic() > deadline:
                return
            path = os.path.join(EVENTS_DIR, name)
            now = int(time.time() * 1000)
            try:
                event = read_small_json(path)
            except FileNotFoundError:
                continue
            except (ValueError, OSError):
                continue  # keep corrupted records, don't delete evidence
            if not valid_event(event, name, now) or event["at"] < now - EVENT_MAX_AGE_MS:
                continue  # expired / invalid: kept on disk, no longer auto-sent
            status, retry_after, data = post(endpoint, event)
            if status == 200:
                try:
                    v = json.loads(data)
                except ValueError:
                    return
                if v.get("ok") is True and v.get("status") in ("applied", "duplicate", "ignored"):
                    try:
                        os.unlink(path)
                    except FileNotFoundError:
                        pass
                    continue
                return
            if status == 429:
                try:
                    wait = max(1, int(retry_after or 60))
                except ValueError:
                    wait = 60
                with open(BACKOFF_FILE, "w") as f:
                    f.write(str(time.time() + wait))
                return
            if status in (409, 422, 400):
                log(f"game rejected {name} with HTTP {status}; kept on disk")
                continue
            return  # 401/5xx etc.: retry next time
    finally:
        unlock(lock)


def status():
    try:
        ep = discover()
        print("game endpoint:", f"127.0.0.1:{ep['port']}" if ep else "not found (start dsh-piggy with --work-events)")
    except Exception as e:  # noqa: BLE001
        print("game endpoint: error:", e)
    n = len([x for x in os.listdir(EVENTS_DIR) if x.endswith(".json")]) if os.path.isdir(EVENTS_DIR) else 0
    print("queue:", EVENTS_DIR)
    print("pending events:", n)


def main():
    if "--status" in sys.argv:
        status()
        return
    if "--flush" not in sys.argv:
        try:
            raw = sys.stdin.buffer.read() if sys.stdin is not None else b""
            payload = json.loads(raw.decode("utf-8") or "{}")
        except ValueError:
            return
        event = map_hook(payload) if isinstance(payload, dict) else None
        if event:
            enqueue(event)
    flush()


if __name__ == "__main__":
    try:
        main()
    except Exception as e:  # never break the Claude Code session
        log(f"error: {type(e).__name__}: {e}")
    sys.exit(0)
