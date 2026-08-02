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
