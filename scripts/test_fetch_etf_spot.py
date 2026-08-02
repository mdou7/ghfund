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
