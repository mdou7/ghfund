"""Fetch an ETF spot snapshot from East Money via akshare and save it as CSV."""
from __future__ import annotations

import sys
import time
from datetime import datetime
from pathlib import Path
from typing import Callable, TypeVar
from zoneinfo import ZoneInfo

import akshare as ak
import pandas as pd

T = TypeVar("T")

RETRY_ATTEMPTS = 3
RETRY_BACKOFF_SECONDS = (5, 15)
BEIJING_TZ = ZoneInfo("Asia/Shanghai")
DATA_DIR = Path("data")


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


def snapshot_path(base_dir: Path, now: datetime) -> Path:
    date_part = now.strftime("%Y-%m-%d")
    time_part = now.strftime("%H%M%S")
    return base_dir / date_part / f"{time_part}.csv"


def save_snapshot(df: pd.DataFrame, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    df.to_csv(path, index=False, encoding="utf-8-sig")


def main() -> None:
    now = datetime.now(BEIJING_TZ)
    df = fetch_with_retry(ak.fund_etf_spot_em)
    path = snapshot_path(DATA_DIR, now)
    save_snapshot(df, path)
    print(f"saved {len(df)} rows to {path}")


if __name__ == "__main__":
    main()
