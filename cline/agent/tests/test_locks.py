from __future__ import annotations

import json
import os

import pytest

from agent_core import locks


def test_try_acquire_records_pid_and_host(tmp_path):
    path = tmp_path / "a.lock"
    assert locks.try_acquire(path, note="unit-test") is True

    info = locks.lock_info(path)
    assert info["pid"] == os.getpid()
    assert info["host"] == locks._current_host()
    assert info["note"] == "unit-test"


def test_try_acquire_fails_when_already_locked(tmp_path):
    path = tmp_path / "a.lock"
    assert locks.try_acquire(path) is True
    assert locks.try_acquire(path) is False  # 既に取得済み


def test_release_allows_reacquire(tmp_path):
    path = tmp_path / "a.lock"
    locks.try_acquire(path)
    locks.release(path)
    assert locks.try_acquire(path) is True


def test_cross_host_lock_is_never_auto_reclaimed(tmp_path):
    """
    別ホストが持つロックは、こちらのプロセス空間ではPIDの生死を
    判定できない。存在しないであろう架空のPIDであっても、
    ホスト名が異なる限り絶対に自動回収してはいけない（弱点7）。
    """
    path = tmp_path / "cross-host.lock"
    path.write_text(
        json.dumps({"pid": 999999, "host": "some-other-pc", "at": "x", "note": ""}),
        encoding="utf-8",
    )

    assert locks._reclaim_if_stale(path) is False
    assert path.exists()

    # try_acquireを通しても、他ホストのロックとして扱われ取得できない
    assert locks.try_acquire(path) is False


def test_same_host_dead_pid_is_reclaimed(tmp_path):
    path = tmp_path / "same-host.lock"
    dead_pid = 2**30  # 実在しないとみなせる大きなPID
    path.write_text(
        json.dumps({"pid": dead_pid, "host": locks._current_host(), "at": "x", "note": ""}),
        encoding="utf-8",
    )

    assert locks._reclaim_if_stale(path) is True
    assert not path.exists()


def test_same_host_alive_pid_is_not_reclaimed(tmp_path):
    path = tmp_path / "alive.lock"
    path.write_text(
        json.dumps({"pid": os.getpid(), "host": locks._current_host(), "at": "x", "note": ""}),
        encoding="utf-8",
    )

    assert locks._reclaim_if_stale(path) is False
    assert path.exists()


def test_legacy_lock_format_is_still_readable(tmp_path):
    """ホスト名が無い旧形式（PID/タイムスタンプ/note の3行）も読めること。"""
    path = tmp_path / "legacy.lock"
    dead_pid = 2**30
    path.write_text(f"{dead_pid}\n2026-01-01T00:00:00\nold-note\n", encoding="utf-8")

    info = locks.lock_info(path)
    assert info["pid"] == dead_pid
    assert info["host"] == ""
    assert info["note"] == "old-note"

    # ホスト情報が無い(ローカルとみなす)ため、死んでいれば回収される
    assert locks._reclaim_if_stale(path) is True


def test_lock_info_on_missing_or_corrupt_file(tmp_path):
    assert locks.lock_info(tmp_path / "missing.lock") == {}

    corrupt = tmp_path / "corrupt.lock"
    corrupt.write_text("", encoding="utf-8")
    assert locks.lock_info(corrupt) == {}


def test_acquire_or_wait_succeeds_immediately_when_free(tmp_path):
    path = tmp_path / "free.lock"
    assert locks.acquire_or_wait(path, timeout_seconds=1) is True


def test_acquire_or_wait_times_out_on_cross_host_lock(tmp_path):
    path = tmp_path / "busy.lock"
    path.write_text(
        json.dumps({"pid": 999999, "host": "other-host", "at": "x", "note": ""}),
        encoding="utf-8",
    )

    waited_with = []
    result = locks.acquire_or_wait(
        path,
        timeout_seconds=0.2,
        poll_seconds=0.05,
        on_wait=lambda owner: waited_with.append(owner),
    )

    assert result is False
    assert waited_with and waited_with[0]["host"] == "other-host"


def test_gui_lock_context_manager_acquires_and_releases(tmp_path, monkeypatch):
    from agent_core.config import CONFIG

    monkeypatch.setattr(CONFIG, "locks_dir", tmp_path)

    with locks.gui_lock(issue_number=1, timeout_seconds=1):
        assert locks.gui_lock_path().exists()

    assert not locks.gui_lock_path().exists()


def test_gui_lock_raises_timeout_when_held_by_other_host(tmp_path, monkeypatch):
    from agent_core.config import CONFIG

    monkeypatch.setattr(CONFIG, "locks_dir", tmp_path)
    locks.gui_lock_path().write_text(
        json.dumps({"pid": 1, "host": "other-host", "at": "x", "note": "issue-9"}),
        encoding="utf-8",
    )

    with pytest.raises(TimeoutError):
        with locks.gui_lock(issue_number=2, timeout_seconds=0.2):
            pass
