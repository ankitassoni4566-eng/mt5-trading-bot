"""Watchdog: keeps the bot running 24/7.

Starts `python run.py`, and restarts it if it crashes, exits, or freezes
(heartbeat file older than --stale seconds). Sends a Telegram alert on every restart.

    python watchdog/watchdog.py            (run from the project folder)

Install it as a Windows service with NSSM so it also starts after a reboot —
see watchdog/WINDOWS_SERVICE.md.
"""
from __future__ import annotations

import argparse
import os
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from bot.notifier import Notifier  # noqa: E402

try:
    from dotenv import load_dotenv
    load_dotenv(ROOT / ".env")
except ImportError:
    pass


def heartbeat_age(path: Path) -> float:
    try:
        return time.time() - float(path.read_text().strip())
    except (OSError, ValueError):
        return float("inf")


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--stale", type=int, default=180, help="restart if no heartbeat for this many seconds")
    ap.add_argument("--heartbeat", default=str(ROOT / "data" / "heartbeat.txt"))
    args = ap.parse_args()

    hb = Path(args.heartbeat)
    notifier = Notifier(enabled=bool(os.getenv("TELEGRAM_TOKEN")))
    restarts, backoff, proc, started = 0, 0, None, 0.0
    while True:
        if proc is None or proc.poll() is not None:
            if proc is not None:
                if proc.returncode == 3:                   # safety refusal (demo/live mismatch)
                    notifier.send("⛔ Watchdog: bot refused to trade (check mode in config.yaml). Not restarting.")
                    return
                restarts += 1
                backoff = 0 if time.time() - started > 3600 else backoff + 1   # healthy for 1h → reset
                notifier.send(f"🔁 Watchdog: bot exited (code {proc.returncode}). Restart #{restarts}.")
                time.sleep(min(300, 10 * backoff))         # wait longer if it keeps crashing
            hb.parent.mkdir(parents=True, exist_ok=True)
            hb.write_text(str(time.time()))                # give the new process time to start
            started = time.time()
            proc = subprocess.Popen([sys.executable, str(ROOT / "run.py")], cwd=str(ROOT))
        elif heartbeat_age(hb) > args.stale:
            restarts += 1
            notifier.send(f"🔁 Watchdog: bot frozen (no heartbeat for {args.stale}s). Restart #{restarts}.")
            proc.kill()
            proc.wait(timeout=30)
            proc = None
            continue
        time.sleep(15)


if __name__ == "__main__":
    main()
