"""Reference subscriber for the phone bridge: prints the alerts the core publishes.

Usage: python tools/bridge_echo.py [--url ws://127.0.0.1:8765/ws] [--all] [--count N]

Start the core with the bridge display first, for example:
  python tools/run_replay.py scenarios/tonal_10db.yaml --display bridge --realtime
A real bridge does the same thing but, in forward_to_glasses(), sends the text to the glasses
through whatever the MicroLumin SDK offers. That function is a stub on purpose.
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from typing import Any

from websockets.exceptions import ConnectionClosed, InvalidURI, WebSocketException
from websockets.sync.client import connect


def forward_to_glasses(message: dict[str, Any]) -> None:
    """TODO(SDK): send message['line1'] and message['line2'] to the glasses display.

    A real bridge calls the SDK here for 'alert' and clears the display for 'clear'. Nothing about
    the SDK is known yet (docs/SDK_NOTES.md), so this only logs that it would forward.
    """
    return None


def describe(m: dict[str, Any]) -> str | None:
    kind = m.get("type")
    stamp = time.strftime("%H:%M:%S") + f".{int(time.time() * 1000) % 1000:03d}"
    if kind == "alert":
        blink = "blink" if m.get("blink") else "steady"
        line2 = f" | {m['line2']}" if m.get("line2") else ""
        return (
            f"[{stamp}] ALERT {m.get('event_id')} tier {m.get('tier')} {blink}: "
            f"{m.get('line1')}{line2} (ttl {m.get('ttl_ms')} ms)"
        )
    if kind == "clear":
        return f"[{stamp}] clear {m.get('event_id') or 'all'}"
    if kind == "event":
        return f"[{stamp}] event {json.dumps(m.get('event'), separators=(',', ':'))}"
    if kind == "stats":
        return f"[{stamp}] stats {json.dumps(m, separators=(',', ':'))}"
    return None


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--url", default="ws://127.0.0.1:8765/ws")
    parser.add_argument("--all", action="store_true", help="also print event and stats messages")
    parser.add_argument("--json", action="store_true", help="print raw JSON frames")
    parser.add_argument("--count", type=int, default=0, help="exit after N alert messages")
    parser.add_argument("--timeout", type=float, default=0.0, help="exit after N seconds")
    parser.add_argument("--retry-s", type=float, default=1.0, help="wait before reconnecting")
    args = parser.parse_args(argv)

    started = time.monotonic()
    alerts = 0
    shown_types = {"alert", "clear"} | ({"event", "stats"} if args.all else set())
    print(f"Subscribing to {args.url} (Ctrl+C to stop)", flush=True)
    while True:
        if args.timeout and time.monotonic() - started > args.timeout:
            return 0
        try:
            with connect(args.url, open_timeout=5) as ws:
                print("connected", flush=True)
                while True:
                    left = args.timeout - (time.monotonic() - started) if args.timeout else None
                    if left is not None and left <= 0:
                        return 0
                    try:
                        raw = ws.recv(timeout=left if left is not None else None)
                    except TimeoutError:
                        return 0
                    try:
                        msg = json.loads(raw)
                    except json.JSONDecodeError:
                        print(f"ignored non-JSON frame: {raw!r}", file=sys.stderr)
                        continue
                    if msg.get("type") in shown_types:
                        print(raw if args.json else describe(msg), flush=True)
                    if msg.get("type") in ("alert", "clear"):
                        forward_to_glasses(msg)
                    if msg.get("type") == "alert":
                        alerts += 1
                        if args.count and alerts >= args.count:
                            return 0
        except KeyboardInterrupt:
            return 0
        except InvalidURI as err:
            print(f"Bad WebSocket address: {err}", file=sys.stderr)
            return 2
        except (OSError, ConnectionClosed, WebSocketException) as err:
            print(
                f"connection lost ({err}); retrying in {args.retry_s:g} s",
                file=sys.stderr,
                flush=True,
            )
            time.sleep(args.retry_s)


if __name__ == "__main__":
    raise SystemExit(main())
