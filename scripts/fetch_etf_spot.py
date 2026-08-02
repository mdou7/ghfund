"""Fetch an ETF spot snapshot from East Money via akshare and save it as CSV."""
from __future__ import annotations

import sys
import time
from typing import Callable, TypeVar

T = TypeVar("T")

RETRY_ATTEMPTS = 3
RETRY_BACKOFF_SECONDS = (5, 15)


def fetch_with_retry(
    fetch_fn: Callable[[], T],
    attempts: int = RETRY_ATTEMPTS,
    backoff: tuple[int, ...] = RETRY_BACKOFF_SECONDS,
) -> T:
    last_exc: Exception | None = None
    for attempt in range(attempts):
        try:
            return fetch_fn()
        except Exception as exc:  # noqa: BLE001 - any failure should trigger a retry
            last_exc = exc
            print(f"attempt {attempt + 1}/{attempts} failed: {exc}", file=sys.stderr)
            if attempt < attempts - 1:
                time.sleep(backoff[min(attempt, len(backoff) - 1)])
    raise RuntimeError(f"all {attempts} attempts failed") from last_exc
