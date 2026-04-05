"""Stream state manager for real-time CSV ingestion monitoring.

Watches an inbox folder for new CSV files, analyzes them on arrival,
and maintains current AS-IS, TARGET, and latest stream metrics for the UI.

Zero external dependencies — uses only Python stdlib + transformer.analyzer.
"""

from __future__ import annotations

import os
import time

from transformer.analyzer import analyze_topology


class StreamState:
    """Manages streaming topology state for the Web UI."""

    def __init__(self, art_dir: str) -> None:
        self.art_dir = art_dir
        self.inbox = os.path.join(art_dir, "stream_inbox")
        os.makedirs(self.inbox, exist_ok=True)
        self.as_is: dict | None = None
        self.target: dict | None = None
        self.last_stream: dict | None = None

    def set_as_is(self, m: dict) -> None:
        self.as_is = m

    def set_target(self, m: dict) -> None:
        self.target = m

    def push_stream_csv(self, fileitem: object) -> str:
        """Save uploaded stream CSV to inbox with timestamped name."""
        p = os.path.join(self.inbox, f"stream_{int(time.time())}.csv")
        with open(p, "wb") as f:
            f.write(fileitem.file.read())  # type: ignore[union-attr]
        return p

    def watch_folder(self) -> None:
        """Background thread: watch inbox for new CSVs and analyze them."""
        seen: set[str] = set()
        while True:
            try:
                for name in sorted(os.listdir(self.inbox)):
                    p = os.path.join(self.inbox, name)
                    if p in seen or not name.lower().endswith(".csv"):
                        continue
                    try:
                        metrics, _ = analyze_topology(p)
                        self.last_stream = {
                            "dataset": os.path.basename(p),
                            "metrics": metrics,
                            "ts": int(time.time()),
                        }
                        seen.add(p)
                    except Exception:
                        pass
            except Exception:
                pass
            time.sleep(2)

    def get_stream_metrics(self) -> dict:
        """Return current AS-IS, TARGET, and latest stream metrics."""
        return {
            "as_is": self.as_is,
            "target": self.target,
            "last_stream": self.last_stream,
        }
