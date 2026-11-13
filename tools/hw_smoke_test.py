"""Hardware smoke test for a display adapter.

Does four things: shows a fixed text, clears it, times a burst of 20 show and clear calls, and
prints the numbers. With a stub adapter it exits cleanly and says which adapter is not implemented.

Usage: python tools/hw_smoke_test.py [--display console|sim|bridge|microlumin] [--bursts 20]
"""

from __future__ import annotations

import argparse
import sys
import time
from dataclasses import dataclass

from sentinel.adapters import make_display
from sentinel.config import ConfigError, load_config
from sentinel.metrics import percentile
from sentinel.runtime import start_runtime
from sentinel.types import Alert, AlertClass, DisplayAdapter

SMOKE_ALERT = Alert("e_smoke", AlertClass.REVERSING_ALARM, 1, "SMOKE TEST", "NEARBY", False, 3.0)


@dataclass
class SmokeResult:
    show_ms: list[float]
    clear_ms: list[float]
    total_s: float
    errors: list[str]


def timed(fn) -> tuple[float, str | None]:
    t0 = time.perf_counter()
    try:
        fn()
    except Exception as exc:
        return (time.perf_counter() - t0) * 1000.0, f"{type(exc).__name__}: {exc}"
    return (time.perf_counter() - t0) * 1000.0, None


def run_smoke(display: DisplayAdapter, bursts: int = 20, hold_s: float = 1.0) -> SmokeResult:
    """1. show fixed text  2. clear  3. time a burst of show and clear calls  4. return numbers."""
    errors: list[str] = []
    print(f"Capabilities: {display.capabilities()}")
    print("1. show 'SMOKE TEST | NEARBY'")
    ms, err = timed(lambda: display.show(SMOKE_ALERT))
    print(f"   show returned in {ms:.1f} ms")
    if err:
        errors.append(f"show: {err}")
    time.sleep(hold_s)
    print("2. clear")
    ms, err = timed(lambda: display.clear("e_smoke"))
    print(f"   clear returned in {ms:.1f} ms")
    if err:
        errors.append(f"clear: {err}")
    print(f"3. burst of {bursts} show and clear calls")
    show_ms: list[float] = []
    clear_ms: list[float] = []
    t0 = time.perf_counter()
    for i in range(bursts):
        alert = Alert(
            f"e_smoke_{i}", AlertClass.REVERSING_ALARM, 1, "SMOKE TEST", "NEARBY", False, 3.0
        )
        s, e1 = timed(lambda a=alert: display.show(a))
        c, e2 = timed(lambda a=alert: display.clear(a.event_id))
        show_ms.append(s)
        clear_ms.append(c)
        errors += [f"burst {i}: {e}" for e in (e1, e2) if e]
    return SmokeResult(show_ms, clear_ms, time.perf_counter() - t0, errors)


def summarize(r: SmokeResult) -> str:
    def row(name: str, v: list[float]) -> str:
        return (
            f"   {name:<6} p50 {percentile(v, 50):.2f} ms   p95 {percentile(v, 95):.2f} ms   "
            f"max {max(v):.2f} ms"
        )

    n = len(r.show_ms)
    return "\n".join(
        [
            f"4. numbers for {n} show and clear pairs, {r.total_s * 1000:.0f} ms in total "
            f"({r.total_s * 1000 / max(n, 1):.1f} ms per pair)",
            row("show", r.show_ms),
            row("clear", r.clear_ms),
            "   These are call times from this program. Time to visible photons on the glasses "
            "needs slow-motion video (tools/latency_probe.py).",
        ]
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--config", default=None)
    parser.add_argument("--display", default=None, help="adapter to test (default: config)")
    parser.add_argument("--bursts", type=int, default=20)
    parser.add_argument("--hold-s", type=float, default=1.0, help="how long to leave the text up")
    args = parser.parse_args(argv)
    runtime = None
    name = args.display or "(config)"
    try:
        cfg = load_config(args.config)
        name = args.display or cfg.display.adapter
        if "microlumin" in name or "bridge" in name or "sim" in name:
            runtime = start_runtime(cfg, "live", name)
            display = runtime.display
        else:
            display = make_display(cfg, name)
    except NotImplementedError as err:
        print(f"SKIPPED: display adapter '{name}' is not implemented.\n{err}")
        return 0
    except (ConfigError, RuntimeError) as err:
        print(err, file=sys.stderr)
        return 2
    try:
        if runtime and runtime.url:
            print(f"Dashboard and simulator: {runtime.url}")
        result = run_smoke(display, args.bursts, args.hold_s)
        print(summarize(result))
        for e in result.errors[:5]:
            print(f"ERROR {e}", file=sys.stderr)
        display.close()
        return 1 if result.errors else 0
    finally:
        if runtime:
            runtime.close()


if __name__ == "__main__":
    raise SystemExit(main())
