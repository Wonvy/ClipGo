#!/usr/bin/env python3
"""
Native Messaging test client: talks to clipgo_host.py exactly like Edge/Chrome do.

Usage:
    python3 tools/nm_client.py <host_executable> '[delay_s, message]' ...

Each argument is a JSON array: seconds to wait before sending, then the message.
The pipe stays open until every download id has a done / error / cancelled reply
(or 30 s pass), then it's closed, which makes the host exit.

Examples:
    python3 tools/nm_client.py host/clipgo_host.py '[0, {"action": "ping", "rid": 1}]'

    python3 tools/nm_client.py "$HOME/Library/Application Support/ClipGo/clipgo_host.py" \
        '[0, {"action": "download", "id": "t1", "url": "https://www.youtube.com/watch?v=jNQXAC9IVRw", "quality": "1080"}]'

    # Start a download and cancel it after 2 s
    python3 tools/nm_client.py host/clipgo_host.py \
        '[0, {"action": "download", "id": "t1", "url": "https://youtu.be/aqz-KE-bpKQ"}]' \
        '[2, {"action": "cancel", "id": "t1"}]'

On Windows pass the .bat launcher, e.g. "%LOCALAPPDATA%\\ClipGo\\clipgo_host.bat".
"""
import json
import struct
import subprocess
import sys
import threading
import time

if len(sys.argv) < 3:
    sys.exit(__doc__)

cmd = [sys.argv[1]]
if sys.argv[1].endswith(".py"):
    cmd = [sys.executable, sys.argv[1]]
p = subprocess.Popen(cmd, stdin=subprocess.PIPE, stdout=subprocess.PIPE, shell=sys.argv[1].endswith(".bat"))
items = [json.loads(a) for a in sys.argv[2:]]
pending = {m["id"] for _, m in items if m.get("action") == "download"}
done = threading.Event()


def reader():
    while True:
        head = p.stdout.read(4)
        if len(head) < 4:
            break
        msg = json.loads(p.stdout.read(struct.unpack("@I", head)[0]))
        print(json.dumps(msg, ensure_ascii=False), flush=True)
        if msg.get("type") in ("done", "error", "cancelled"):
            pending.discard(msg.get("id"))
            if not pending:
                done.set()
    done.set()


threading.Thread(target=reader, daemon=True).start()
for delay, msg in items:
    time.sleep(delay)
    data = json.dumps(msg).encode("utf-8")
    p.stdin.write(struct.pack("@I", len(data)) + data)
    p.stdin.flush()
if pending:
    done.wait(30)
else:
    time.sleep(0.5)
p.stdin.close()
print("host exited with", p.wait())
