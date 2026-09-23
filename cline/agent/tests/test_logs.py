"""
Windows PowerShellの Get-Content は、BOM無しUTF-8ファイルを
システムのロケール（日本語Windowsなら Shift-JIS）で読んでしまい、
日本語ログが文字化けする（.ps1 と同じ系統の問題が、ログファイルでも
実際に発生した）。この回帰テストは、ログファイルの先頭に
BOMが一度だけ付与され、複数回の追記でも壊れないことを確認する。
"""

from __future__ import annotations

from agent_core.logs import Logger

BOM = b"\xef\xbb\xbf"


def test_log_file_starts_with_bom(tmp_path):
    log_file = tmp_path / "issue-1.log"
    logger = Logger("test", log_file)
    logger.info("最初のログ行")

    raw = log_file.read_bytes()
    assert raw.startswith(BOM)


def test_log_file_has_exactly_one_bom_after_multiple_writes(tmp_path):
    log_file = tmp_path / "issue-2.log"
    logger = Logger("test", log_file)

    for i in range(10):
        logger.info(f"ログ行 {i}")
    logger.warn("警告行")
    logger.error("エラー行")

    raw = log_file.read_bytes()
    assert raw.count(BOM) == 1, "追記のたびにBOMが挿入され、ファイルが壊れている"
    assert raw.startswith(BOM)


def test_log_file_content_is_readable_as_utf8_sig(tmp_path):
    log_file = tmp_path / "issue-3.log"
    logger = Logger("test", log_file)
    logger.info("日本語のメッセージ：顧客一覧ページを追加")

    # Windows PowerShellの Get-Content -Encoding UTF8 や、BOMを認識する
    # 読み手であれば、これで正しく読める（utf-8-sig はBOMを自動で剥がす）
    text = log_file.read_text(encoding="utf-8-sig")
    assert "顧客一覧ページを追加" in text
    assert not text.startswith("\ufeff")


def test_log_file_plain_utf8_read_only_has_leading_bom_char(tmp_path):
    """
    BOMを解釈しない素のutf-8デコーダで読んだ場合、
    先頭に \\ufeff が1文字だけ乗る（内容の途中には現れない）ことを確認する。
    """
    log_file = tmp_path / "issue-4.log"
    logger = Logger("test", log_file)
    logger.info("1行目")
    logger.info("2行目")

    text = log_file.read_text(encoding="utf-8")
    assert text.count("\ufeff") == 1
    assert text.startswith("\ufeff")
    # BOM文字を取り除けば、utf-8-sigで読んだ内容と一致する
    assert text.lstrip("\ufeff") == log_file.read_text(encoding="utf-8-sig")


def test_console_only_logger_without_file_still_works(capsys):
    """log_fileを指定しない場合（既存の使い方）が壊れていないこと。"""
    logger = Logger("test")
    logger.info("コンソールだけの出力")

    captured = capsys.readouterr()
    assert "コンソールだけの出力" in captured.out
