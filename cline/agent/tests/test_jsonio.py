from __future__ import annotations

import json

from agent_core import jsonio


def test_write_json_then_read_json_roundtrip(tmp_path):
    path = tmp_path / "state" / "issue-1.json"
    payload = {"issueNumber": 1, "title": "テスト"}

    assert jsonio.write_json(path, payload) is True
    assert jsonio.read_json(path) == payload

    # BOMなしUTF-8で書かれていること
    raw = path.read_bytes()
    assert not raw.startswith(b"\xef\xbb\xbf")


def test_read_json_returns_none_for_missing_or_empty(tmp_path):
    missing = tmp_path / "missing.json"
    assert jsonio.read_json(missing) is None

    empty = tmp_path / "empty.json"
    empty.write_bytes(b"")
    assert jsonio.read_json(empty) is None


def test_read_json_falls_back_across_encodings(tmp_path):
    path = tmp_path / "cp932.json"
    payload = {"message": "文字化けテスト"}
    # utf-8以外のエンコーディングで書かれた場合でも読めること
    path.write_bytes(json.dumps(payload, ensure_ascii=False).encode("cp932"))

    assert jsonio.read_json(path) == payload


def test_read_json_ignores_non_dict_json(tmp_path):
    path = tmp_path / "array.json"
    path.write_text("[1, 2, 3]", encoding="utf-8")
    assert jsonio.read_json(path) is None


def test_read_json_when_ready_reads_once_stable(tmp_path, monkeypatch):
    # 実行時間短縮のためポーリング間隔を縮める
    monkeypatch.setattr(jsonio, "FILE_READY_INTERVAL_SECONDS", 0.01)

    path = tmp_path / "issue-2.json"
    jsonio.write_json(path, {"issueNumber": 2})

    result = jsonio.read_json_when_ready(path)
    assert result == {"issueNumber": 2}


def test_read_json_when_ready_returns_none_for_missing(tmp_path, monkeypatch):
    monkeypatch.setattr(jsonio, "FILE_READY_INTERVAL_SECONDS", 0.01)
    monkeypatch.setattr(jsonio, "FILE_READY_RETRIES", 2)

    result = jsonio.read_json_when_ready(tmp_path / "nope.json")
    assert result is None


def test_move_to_done_moves_file(tmp_path):
    path = tmp_path / "question" / "issue-3.json"
    jsonio.write_json(path, {"issueNumber": 3})

    destination = jsonio.move_to_done(path)

    assert destination is not None
    assert destination.parent.name == "done"
    assert not path.exists()
    assert destination.exists()


def test_move_to_done_avoids_name_collision(tmp_path):
    done_dir = tmp_path / "question" / "done"

    first = tmp_path / "question" / "issue-4.json"
    jsonio.write_json(first, {"n": 1})
    first_dest = jsonio.move_to_done(first, done_dir)

    second = tmp_path / "question" / "issue-4.json"
    jsonio.write_json(second, {"n": 2})
    second_dest = jsonio.move_to_done(second, done_dir)

    assert first_dest != second_dest
    assert first_dest.exists()
    assert second_dest.exists()


def test_move_to_done_missing_source_returns_none(tmp_path):
    assert jsonio.move_to_done(tmp_path / "no-such-file.json") is None


def test_now_iso_includes_explicit_timezone_offset():
    """
    実環境の検証で、state の mergedAt（ローカル時刻・タイムゾーン無し）と
    GitHub APIの mergedAt（UTC・Z付き）が並んだときに、どちらのタイムゾーン
    か文字列だけでは分からず紛らわしいという指摘があった。
    now_iso() は必ず明示的なオフセット（+09:00 等）を含むこと。
    """
    value = jsonio.now_iso()

    # "YYYY-MM-DDTHH:MM:SS" の19文字より後ろに、
    # "+HH:MM" / "-HH:MM" / "Z" のいずれかのタイムゾーン表記が続くこと
    assert len(value) > 19
    tz_part = value[19:]
    assert tz_part.startswith(("+", "-", "Z")), (
        f"タイムゾーン情報が付いていない: {value!r}"
    )


def test_now_iso_is_parseable_as_aware_datetime():
    """fromisoformatで読み戻したときにtzinfoが付いている(=aware)こと。"""
    from datetime import datetime

    value = jsonio.now_iso()
    parsed = datetime.fromisoformat(value)
    assert parsed.tzinfo is not None
