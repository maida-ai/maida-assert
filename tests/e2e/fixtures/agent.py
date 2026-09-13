"""Recorded tool behavior; no provider, network, sleep, or API credentials."""

import json
import os
from pathlib import Path

from maida import record_tool_call, traced_run

with traced_run(name="fixture-agent"):
    record_tool_call(name="lookup", args={}, result={"found": True})
    if Path("state.txt").read_text().strip() == "regressed":
        record_tool_call(name="retry_lookup", args={}, result={"found": True})

# Treat elapsed time as recorded fixture data so snapshots cannot vary with
# runner load. Keep every structural field emitted by the real recorder.
start, end = "2026-01-01T00:00:00.000000Z", "2026-01-01T00:00:00.100000Z"
for run_dir in (Path(os.environ["MAIDA_DATA_DIR"]) / "runs").iterdir():
    meta_path = run_dir / "meta.json"
    meta = json.loads(meta_path.read_text())
    meta.update(started_at=start, ended_at=end, duration_ms=100)
    meta_path.write_text(json.dumps(meta))
    spans_path = run_dir / "spans.jsonl"
    spans = [json.loads(line) for line in spans_path.read_text().splitlines()]
    for span in spans:
        span.update(start_time=start, end_time=end, duration_ms=100)
        for event in span["events"]:
            event["timestamp"] = start
    spans_path.write_text("".join(json.dumps(span) + "\n" for span in spans))
