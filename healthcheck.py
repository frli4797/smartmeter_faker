#!/usr/bin/env python3
import json
import os
import sys
import time
from pathlib import Path

HEALTH_PATH = Path(os.getenv("HEALTH_STATUS_PATH", "/tmp/smartmeter-faker-health.json"))
MAX_AGE_SECONDS = float(os.getenv("HEALTHCHECK_MAX_AGE_SECONDS", "30"))


def main() -> int:
    try:
        payload = json.loads(HEALTH_PATH.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return 1
    except (OSError, json.JSONDecodeError, ValueError):
        return 1

    last_success_at = payload.get("last_success_at")
    if not isinstance(last_success_at, (int, float)):
        return 1

    age_seconds = time.time() - float(last_success_at)
    if age_seconds > MAX_AGE_SECONDS:
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
