# ETF Spot Snapshot Cron Pipeline Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build a GitHub Actions pipeline that calls `ak.fund_etf_spot_em()` every 5 minutes during A-share trading hours and commits each snapshot back into the repo as a CSV file.

**Architecture:** A single Python script (`scripts/fetch_etf_spot.py`) built from three small, independently testable pieces — a retry wrapper, a path/save helper, and a `main()` that wires them to the real akshare call — plus one GitHub Actions workflow (`.github/workflows/fetch_etf_spot.yml`) that runs the script on a cron schedule and pushes the result with plain git commands.

**Tech Stack:** Python 3.12 (CI) / whatever Python 3 is available locally for dev, `akshare==1.18.75`, `pandas==3.0.1`, `pytest` for tests, GitHub Actions (`actions/checkout@v4`, `actions/setup-python@v5`).

## Global Constraints

- Only call `ak.fund_etf_spot_em()` — no other akshare interface, no other data source.
- Snapshot file path: `data/<YYYY-MM-DD>/<HHMMSS>.csv`, date and time computed in `Asia/Shanghai`, independent of the runner's system timezone.
- CSV encoding: `utf-8-sig` (Chinese column names must open cleanly in Excel).
- Fetch retry: 3 attempts, backoff `5s` then `15s` between attempts; if all attempts fail the script must exit with a non-zero status and must not write any file.
- Cron covers Beijing time 09:30–11:30 and 13:00–15:00, every 5 minutes, Monday–Friday only (UTC weekday matches Beijing weekday for this window, so `1-5` is correct in UTC cron fields without extra conversion).
- Repo write-back uses plain git commands inside the workflow with the default `GITHUB_TOKEN` (`permissions: contents: write`) — no third-party commit action, no REST API client.
- Workflow must set `concurrency: { group: etf-spot-snapshot, cancel-in-progress: false }` so overlapping delayed runs queue instead of racing on push.
- No trading-calendar/holiday detection, no data archival/cleanup, no notification mechanism — explicitly out of scope per the design spec.
- Design spec: `docs/superpowers/specs/2026-08-02-etf-spot-snapshot-design.md`.

---

## Task 1: Retry helper + project scaffolding

**Files:**
- Create: `requirements.txt`
- Create: `scripts/fetch_etf_spot.py`
- Create: `scripts/test_fetch_etf_spot.py`

**Interfaces:**
- Consumes: nothing (first task).
- Produces: `fetch_with_retry(fetch_fn: Callable[[], T], attempts: int = RETRY_ATTEMPTS, backoff: tuple[int, ...] = RETRY_BACKOFF_SECONDS) -> T` — calls `fetch_fn()`, retrying on any exception up to `attempts` times with `time.sleep(backoff[...])` between attempts, and raises `RuntimeError` (chained from the last exception) if every attempt fails. Also produces module-level constants `RETRY_ATTEMPTS = 3` and `RETRY_BACKOFF_SECONDS = (5, 15)` that later tasks reuse.

- [ ] **Step 1: Create `requirements.txt`**

```
akshare==1.18.75
pandas==3.0.1
```

- [ ] **Step 2: Write the failing tests**

Create `scripts/test_fetch_etf_spot.py`:

```python
import pytest

from fetch_etf_spot import fetch_with_retry


def test_fetch_with_retry_returns_on_first_success():
    calls = []

    def fetch_fn():
        calls.append(1)
        return "ok"

    result = fetch_with_retry(fetch_fn, attempts=3, backoff=(0, 0))

    assert result == "ok"
    assert len(calls) == 1


def test_fetch_with_retry_retries_then_succeeds(monkeypatch):
    import fetch_etf_spot

    monkeypatch.setattr(fetch_etf_spot.time, "sleep", lambda seconds: None)
    calls = []

    def fetch_fn():
        calls.append(1)
        if len(calls) < 3:
            raise RuntimeError("boom")
        return "ok"

    result = fetch_with_retry(fetch_fn, attempts=3, backoff=(5, 15))

    assert result == "ok"
    assert len(calls) == 3


def test_fetch_with_retry_raises_after_exhausting_attempts(monkeypatch):
    import fetch_etf_spot

    monkeypatch.setattr(fetch_etf_spot.time, "sleep", lambda seconds: None)
    calls = []

    def fetch_fn():
        calls.append(1)
        raise RuntimeError("boom")

    with pytest.raises(RuntimeError):
        fetch_with_retry(fetch_fn, attempts=3, backoff=(5, 15))

    assert len(calls) == 3
```

- [ ] **Step 3: Run tests to verify they fail**

Run: `python3 -m pytest scripts/test_fetch_etf_spot.py -v`
Expected: FAIL (collection error) with `ModuleNotFoundError: No module named 'fetch_etf_spot'` — the module doesn't exist yet.

- [ ] **Step 4: Write minimal implementation**

Create `scripts/fetch_etf_spot.py`:

```python
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
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `python3 -m pytest scripts/test_fetch_etf_spot.py -v`
Expected: PASS (3 passed)

- [ ] **Step 6: Commit**

```bash
git add requirements.txt scripts/fetch_etf_spot.py scripts/test_fetch_etf_spot.py
git commit -m "feat: add retry helper for ETF spot fetch script"
```

---

## Task 2: Snapshot path + CSV save helpers

**Files:**
- Modify: `scripts/fetch_etf_spot.py`
- Modify: `scripts/test_fetch_etf_spot.py`

**Interfaces:**
- Consumes: nothing new from Task 1 (adds independent pure helpers to the same module).
- Produces: `BEIJING_TZ = ZoneInfo("Asia/Shanghai")`, `DATA_DIR = Path("data")`, `snapshot_path(base_dir: Path, now: datetime) -> Path` (returns `base_dir / "<YYYY-MM-DD>" / "<HHMMSS>.csv"`), `save_snapshot(df: pd.DataFrame, path: Path) -> None` (creates parent dirs, writes CSV with `encoding="utf-8-sig"`, no index column). Task 3 wires these together with `fetch_with_retry`.

- [ ] **Step 1: Write the failing tests**

Append to `scripts/test_fetch_etf_spot.py`:

```python
from datetime import datetime
from pathlib import Path

import pandas as pd

from fetch_etf_spot import BEIJING_TZ, save_snapshot, snapshot_path


def test_snapshot_path_builds_path_from_beijing_time():
    now = datetime(2026, 8, 2, 15, 30, 5, tzinfo=BEIJING_TZ)

    path = snapshot_path(Path("data"), now)

    assert path == Path("data/2026-08-02/153005.csv")


def test_save_snapshot_writes_csv_and_creates_parent_dirs(tmp_path):
    df = pd.DataFrame({"代码": ["159527"], "名称": ["云计算ETF广发"], "最新价": [0.713]})
    target = tmp_path / "2026-08-02" / "153005.csv"

    save_snapshot(df, target)

    assert target.exists()
    result = pd.read_csv(target, encoding="utf-8-sig")
    assert result.loc[0, "名称"] == "云计算ETF广发"
    assert list(result.columns) == ["代码", "名称", "最新价"]
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python3 -m pytest scripts/test_fetch_etf_spot.py -v`
Expected: FAIL with `ImportError: cannot import name 'BEIJING_TZ' from 'fetch_etf_spot'`

- [ ] **Step 3: Write minimal implementation**

Add to the top of `scripts/fetch_etf_spot.py` (alongside the existing imports) and below the existing constants in `scripts/fetch_etf_spot.py`:

```python
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import pandas as pd

BEIJING_TZ = ZoneInfo("Asia/Shanghai")
DATA_DIR = Path("data")
```

Add the two helper functions after `fetch_with_retry`:

```python
def snapshot_path(base_dir: Path, now: datetime) -> Path:
    date_part = now.strftime("%Y-%m-%d")
    time_part = now.strftime("%H%M%S")
    return base_dir / date_part / f"{time_part}.csv"


def save_snapshot(df: pd.DataFrame, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    df.to_csv(path, index=False, encoding="utf-8-sig")
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python3 -m pytest scripts/test_fetch_etf_spot.py -v`
Expected: PASS (5 passed)

- [ ] **Step 5: Commit**

```bash
git add scripts/fetch_etf_spot.py scripts/test_fetch_etf_spot.py
git commit -m "feat: add snapshot path and CSV save helpers"
```

---

## Task 3: Wire up `main()` with the real akshare call

**Files:**
- Modify: `scripts/fetch_etf_spot.py`
- Modify: `scripts/test_fetch_etf_spot.py`

**Interfaces:**
- Consumes: `fetch_with_retry` (Task 1), `BEIJING_TZ`, `DATA_DIR`, `snapshot_path`, `save_snapshot` (Task 2).
- Produces: `main() -> None` — the script's entry point, run by the GitHub Actions workflow in Task 4 as `python scripts/fetch_etf_spot.py`.

- [ ] **Step 1: Write the failing test**

Append to `scripts/test_fetch_etf_spot.py`:

```python
def test_main_fetches_and_saves_snapshot(monkeypatch, tmp_path):
    import fetch_etf_spot

    fake_df = pd.DataFrame({"代码": ["159527"], "名称": ["云计算ETF广发"]})
    monkeypatch.setattr(fetch_etf_spot.ak, "fund_etf_spot_em", lambda: fake_df)
    monkeypatch.setattr(fetch_etf_spot, "DATA_DIR", tmp_path)

    fetch_etf_spot.main()

    saved_files = list(tmp_path.rglob("*.csv"))
    assert len(saved_files) == 1
    result = pd.read_csv(saved_files[0], encoding="utf-8-sig")
    assert result.loc[0, "名称"] == "云计算ETF广发"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python3 -m pytest scripts/test_fetch_etf_spot.py -v`
Expected: FAIL with `AttributeError: module 'fetch_etf_spot' has no attribute 'ak'` (or `main`)

- [ ] **Step 3: Write minimal implementation**

Add `import akshare as ak` to the imports at the top of `scripts/fetch_etf_spot.py`, and append `main()` at the end of the file:

```python
def main() -> None:
    now = datetime.now(BEIJING_TZ)
    df = fetch_with_retry(ak.fund_etf_spot_em)
    path = snapshot_path(DATA_DIR, now)
    save_snapshot(df, path)
    print(f"saved {len(df)} rows to {path}")


if __name__ == "__main__":
    main()
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python3 -m pytest scripts/test_fetch_etf_spot.py -v`
Expected: PASS (6 passed)

- [ ] **Step 5: Commit**

```bash
git add scripts/fetch_etf_spot.py scripts/test_fetch_etf_spot.py
git commit -m "feat: wire up main() to fetch and save real ETF spot snapshots"
```

---

## Task 4: GitHub Actions workflow

**Files:**
- Create: `.github/workflows/fetch_etf_spot.yml`

**Interfaces:**
- Consumes: `scripts/fetch_etf_spot.py` (Task 3, run as `python scripts/fetch_etf_spot.py`), `requirements.txt` (Task 1).
- Produces: the scheduled workflow itself — no further tasks depend on its internals.

- [ ] **Step 1: Write the workflow file**

Create `.github/workflows/fetch_etf_spot.yml`:

```yaml
name: Fetch ETF Spot Snapshot

on:
  schedule:
    - cron: '30-59/5 1 * * 1-5'   # 09:30-09:55 Beijing time
    - cron: '*/5 2 * * 1-5'       # 10:00-10:55 Beijing time
    - cron: '0-30/5 3 * * 1-5'    # 11:00-11:30 Beijing time
    - cron: '0-59/5 5 * * 1-5'    # 13:00-13:55 Beijing time
    - cron: '*/5 6 * * 1-5'       # 14:00-14:55 Beijing time
    - cron: '0 7 * * 1-5'         # 15:00 Beijing time (close)
  workflow_dispatch: {}

permissions:
  contents: write

concurrency:
  group: etf-spot-snapshot
  cancel-in-progress: false

jobs:
  fetch:
    runs-on: ubuntu-latest
    steps:
      - name: Checkout
        uses: actions/checkout@v4

      - name: Set up Python
        uses: actions/setup-python@v5
        with:
          python-version: '3.12'
          cache: 'pip'

      - name: Install dependencies
        run: pip install -r requirements.txt

      - name: Fetch snapshot
        run: python scripts/fetch_etf_spot.py

      - name: Commit and push snapshot
        run: |
          git config user.name "github-actions[bot]"
          git config user.email "github-actions[bot]@users.noreply.github.com"
          git add data/
          if git diff --cached --quiet; then
            echo "no changes to commit"
            exit 0
          fi
          git commit -m "data: ETF spot snapshot $(date -u +'%Y-%m-%d %H:%M UTC')"
          if ! git push; then
            git pull --rebase
            git push
          fi
```

- [ ] **Step 2: Validate YAML syntax and required keys locally**

Run:

```bash
python3 -m pip install --quiet pyyaml
python3 -c "
import yaml
with open('.github/workflows/fetch_etf_spot.yml') as f:
    doc = yaml.safe_load(f)
assert 'jobs' in doc
assert doc['permissions'] == {'contents': 'write'}
assert doc['concurrency'] == {'group': 'etf-spot-snapshot', 'cancel-in-progress': False}
assert len(doc[True]['schedule']) == 6  # PyYAML parses bare 'on:' key as boolean True
print('workflow YAML looks structurally correct')
"
```

Expected: prints `workflow YAML looks structurally correct` with no assertion errors.

(`pyyaml` here is a local, throwaway validation tool — it is not added to `requirements.txt`, which only lists what the workflow itself needs at runtime.)

- [ ] **Step 3: Commit**

```bash
git add .github/workflows/fetch_etf_spot.yml
git commit -m "feat: add scheduled workflow to fetch and commit ETF spot snapshots"
```

---

## Task 5: Local end-to-end dry run

**Files:**
- None created or modified — this task only produces a data file under `data/` and verifies the whole local pipeline works before relying on GitHub's scheduler.

**Interfaces:**
- Consumes: `scripts/fetch_etf_spot.py`'s `main()` (Task 3).
- Produces: nothing further tasks depend on.

- [ ] **Step 1: Install real dependencies locally**

Run: `python3 -m pip install -r requirements.txt`
Expected: installs (or confirms already-satisfied) `akshare==1.18.75` and `pandas==3.0.1`.

- [ ] **Step 2: Run the script for real**

Run: `python3 scripts/fetch_etf_spot.py`
Expected: prints `saved <N> rows to data/<today's date>/<HHMMSS>.csv` with `N` in the hundreds/low-thousands (full ETF market snapshot).

- [ ] **Step 3: Inspect the produced file**

Run:

```bash
python3 -c "
import pandas as pd
import glob
path = sorted(glob.glob('data/*/*.csv'))[-1]
df = pd.read_csv(path, encoding='utf-8-sig')
print(path, df.shape)
print(df.columns.tolist())
"
```

Expected: shape roughly `(1000+, 37)` and columns starting with `代码, 名称, 最新价, ...` — matches the shape observed in manual verification during design (`(1560, 37)`).

- [ ] **Step 4: Commit the first real snapshot**

```bash
git add data/
git commit -m "data: first manual ETF spot snapshot"
```

- [ ] **Step 5: Push and verify the workflow remotely (requires explicit go-ahead)**

This step touches the shared GitHub remote and should not be run without confirming with the user first:

1. `git push` — publishes all commits from Tasks 1-5 to `origin/main`.
2. In the GitHub UI (Actions tab) or via `gh workflow run fetch_etf_spot.yml`, manually trigger the workflow using `workflow_dispatch`.
3. Watch the run: confirm checkout → dependency install → fetch → commit → push all succeed, and that a new file lands under `data/` on `origin/main`.
4. Once verified, no further action is needed — the 6 cron schedules take over automatically during the next trading window.

---

## Self-Review Notes

- **Spec coverage:** retry/backoff (Task 1), Beijing-time path + `utf-8-sig` CSV (Task 2), real akshare wiring (Task 3), cron windows/concurrency/permissions/git push (Task 4), end-to-end proof + manual remote verification (Task 5) — every section of the design spec maps to a task.
- **Placeholder scan:** no TBD/TODO; every step has literal code or literal commands.
- **Type/name consistency:** `fetch_with_retry`, `BEIJING_TZ`, `DATA_DIR`, `snapshot_path`, `save_snapshot`, `main`, `RETRY_ATTEMPTS`, `RETRY_BACKOFF_SECONDS` are defined once (Tasks 1-2) and reused with identical names/signatures in Tasks 3-5.
