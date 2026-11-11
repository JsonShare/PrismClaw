"""ScheduleSpec ↔ cron expression conversion.

Pure conversion functions with zero external dependencies.
Moved from engine/scheduler/ to kernel/ so that web/persistence layers
can reference these without coupling to engine.
"""
from __future__ import annotations

_WEEKDAY_MAP: dict[str, int] = {
    "mon": 1,
    "tue": 2,
    "wed": 3,
    "thu": 4,
    "fri": 5,
    "sat": 6,
    "sun": 0,
}

_WEEKDAY_REVERSE: dict[int, str] = {v: k for k, v in _WEEKDAY_MAP.items()}


def spec_to_cron(spec: dict) -> str:
    """Convert a frontend ScheduleSpec dict to a 5-field cron expression.

    Supported types: minute, hour, day, week, month.
    Raises ValueError on unknown type or missing fields.
    """
    spec_type = spec.get("type")
    if spec_type == "minute":
        every = int(spec["everyMinutes"])
        if every < 1 or every > 59:
            raise ValueError(f"everyMinutes must be 1-59, got {every}")
        return f"*/{every} * * * *"

    if spec_type == "hour":
        minute = int(spec["minute"])
        if minute < 0 or minute > 59:
            raise ValueError(f"minute must be 0-59, got {minute}")
        return f"{minute} * * * *"

    if spec_type == "day":
        hour = int(spec["hour"])
        minute = int(spec["minute"])
        if hour < 0 or hour > 23:
            raise ValueError(f"hour must be 0-23, got {hour}")
        if minute < 0 or minute > 59:
            raise ValueError(f"minute must be 0-59, got {minute}")
        return f"{minute} {hour} * * *"

    if spec_type == "week":
        weekday_str = str(spec["weekday"]).lower()
        if weekday_str not in _WEEKDAY_MAP:
            raise ValueError(f"unknown weekday: {weekday_str}")
        hour = int(spec["hour"])
        minute = int(spec["minute"])
        dow = _WEEKDAY_MAP[weekday_str]
        return f"{minute} {hour} * * {dow}"

    if spec_type == "month":
        day = int(spec["day"])
        hour = int(spec["hour"])
        minute = int(spec["minute"])
        if day < 1 or day > 31:
            raise ValueError(f"day must be 1-31, got {day}")
        if hour < 0 or hour > 23:
            raise ValueError(f"hour must be 0-23, got {hour}")
        if minute < 0 or minute > 59:
            raise ValueError(f"minute must be 0-59, got {minute}")
        return f"{minute} {hour} {day} * *"

    raise ValueError(f"unknown schedule type: {spec_type}")


def cron_to_spec(cron: str) -> dict:
    """Best-effort reverse conversion for display purposes.

    Handles the exact patterns produced by spec_to_cron. Falls back to
    a raw cron display for unrecognized patterns.
    """
    parts = cron.strip().split()
    if len(parts) != 5:
        return {"type": "raw", "cron": cron}

    minute_f, hour_f, dom_f, month_f, dow_f = parts

    # */N * * * *
    if minute_f.startswith("*/") and hour_f == "*" and dom_f == "*" and dow_f == "*":
        return {"type": "minute", "everyMinutes": int(minute_f[2:])}

    # M * * * *
    if minute_f.isdigit() and hour_f == "*" and dom_f == "*" and dow_f == "*":
        return {"type": "hour", "minute": minute_f.zfill(2)}

    # M H * * *
    if minute_f.isdigit() and hour_f.isdigit() and dom_f == "*" and dow_f == "*":
        return {"type": "day", "hour": hour_f.zfill(2), "minute": minute_f.zfill(2)}

    # M H * * D
    if minute_f.isdigit() and hour_f.isdigit() and dom_f == "*" and dow_f.isdigit():
        weekday = _WEEKDAY_REVERSE.get(int(dow_f), "mon")
        return {"type": "week", "weekday": weekday, "hour": hour_f.zfill(2), "minute": minute_f.zfill(2)}

    # M H D * *
    if minute_f.isdigit() and hour_f.isdigit() and dom_f.isdigit() and dow_f == "*":
        return {"type": "month", "day": dom_f, "hour": hour_f.zfill(2), "minute": minute_f.zfill(2)}

    return {"type": "raw", "cron": cron}
