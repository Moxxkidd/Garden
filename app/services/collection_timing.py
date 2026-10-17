"""Persisted wall-time samples; read-only projection never advances historical time."""

TIMING_FIELDS = ("queued_seconds", "active_seconds", "wait_seconds", "cooldown_seconds")


def initial_telemetry(now):
    return {
        "duplicate_work_submissions": 0,
        "timing": {
            **dict.fromkeys(TIMING_FIELDS, 0.0),
            "queued_at": now,
            "sample_at": None,
            "waiting": None,
            "timing_complete": False,
        },
    }


def sample_time(queue, now, *, start=False, waiting=False, complete=False):
    if not queue.telemetry or "timing" not in queue.telemetry:
        return
    timing = dict(queue.telemetry["timing"])
    if start:
        timing["queued_seconds"] = max(0, now - timing["queued_at"])
    elif timing["sample_at"] is not None:
        elapsed = max(0, now - timing["sample_at"])
        key = "wait_seconds" if timing["waiting"] else "active_seconds"
        timing[key] += elapsed
        if timing["waiting"] == "cooldown":
            timing["cooldown_seconds"] += elapsed
    timing["sample_at"] = max(now, timing["sample_at"] or now)
    if waiting is not False:
        timing["waiting"] = waiting
    timing["timing_complete"] = complete
    queue.telemetry = {**queue.telemetry, "timing": timing}


def timing_metrics(queue):
    timing = (queue.telemetry or {}).get("timing", {})
    return {key: timing.get(key) for key in (*TIMING_FIELDS, "timing_complete")}
