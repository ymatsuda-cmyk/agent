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


def test_log_file_starts_with_bom(agent_env, tmp_path):
    # agent_env: CONFIG._agent_root_was_set を確実にTrueへ揃える。
    # Loggerは書き込み時に現在のCONFIGを見に行くため、これが無いと
    # 他のテストが最後に残したCONFIG状態次第で結果が変わってしまう。
    log_file = tmp_path / "issue-1.log"
    logger = Logger("test", log_file)
    logger.info("最初のログ行")

    raw = log_file.read_bytes()
    assert raw.startswith(BOM)


def test_log_file_has_exactly_one_bom_after_multiple_writes(agent_env, tmp_path):
    log_file = tmp_path / "issue-2.log"
    logger = Logger("test", log_file)

    for i in range(10):
        logger.info(f"ログ行 {i}")
    logger.warn("警告行")
    logger.error("エラー行")

    raw = log_file.read_bytes()
    assert raw.count(BOM) == 1, "追記のたびにBOMが挿入され、ファイルが壊れている"
    assert raw.startswith(BOM)


def test_log_file_content_is_readable_as_utf8_sig(agent_env, tmp_path):
    log_file = tmp_path / "issue-3.log"
    logger = Logger("test", log_file)
    logger.info("日本語のメッセージ：顧客一覧ページを追加")

    # Windows PowerShellの Get-Content -Encoding UTF8 や、BOMを認識する
    # 読み手であれば、これで正しく読める（utf-8-sig はBOMを自動で剥がす）
    text = log_file.read_text(encoding="utf-8-sig")
    assert "顧客一覧ページを追加" in text
    assert not text.startswith("\ufeff")


def test_log_file_plain_utf8_read_only_has_leading_bom_char(agent_env, tmp_path):
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


# ============================================================
# 回帰テスト: Loggerが構築時のパスを固定で覚えず、
# 書き込みのたびに現在のCONFIGを見に行くこと。
#
# 以前は get_logger() 呼び出し時点のパスを固定で保持していたため、
# モジュール読み込み時（テストのagent_envフィクスチャがCONFIGを
# 再構築する前）に作られる各スクリプトのモジュールレベルLOGGERが、
# リポジトリ本体の agent/logs/ へ書き込み続けてしまう実害があった。
# ============================================================


def test_logger_follows_config_changes_made_after_construction(agent_env, monkeypatch):
    """
    get_logger()（＝モジュール読み込み時を模す）した後にCONFIGを
    別のAGENT_ROOTへ再構築しても、そのLoggerは新しい場所へ書くこと。
    """
    from agent_core.config import CONFIG
    from agent_core.logs import get_logger

    # 1回目の設定でLoggerを作る（各スクリプトのモジュール読み込み相当）
    logger = get_logger("test_agent")
    first_agent_root = CONFIG.agent_root

    # agent_envフィクスチャ的に、後からCONFIGを別の場所へ再構築する
    import uuid

    new_root = first_agent_root.parent / f"reconfigured-{uuid.uuid4().hex[:8]}"
    monkeypatch.setenv("AGENT_ROOT", str(new_root))
    CONFIG.__init__()
    CONFIG.ensure_directories()

    logger.info("再構築後のメッセージ")

    old_log = first_agent_root / "logs" / "test_agent.log"
    new_log = new_root / "logs" / "test_agent.log"

    assert not old_log.exists(), "古い(構築時の)場所へ書き込んでしまっている"
    assert new_log.exists(), "新しい(再構築後の)場所へ書き込まれていない"
    assert "再構築後のメッセージ" in new_log.read_text(encoding="utf-8-sig")


def test_get_logger_does_not_eagerly_create_directory(agent_env):
    """
    get_logger() の呼び出し自体では logs/ ディレクトリを作らない
    （実際に書き込むまでディレクトリ作成を遅延する）こと。
    """
    from agent_core.config import CONFIG
    from agent_core.logs import get_logger

    import shutil

    shutil.rmtree(CONFIG.logs_dir, ignore_errors=True)
    assert not CONFIG.logs_dir.exists()

    get_logger("not-yet-written")

    assert not CONFIG.logs_dir.exists(), "get_logger()の時点でディレクトリが作られている"


def test_logger_skips_file_write_when_agent_root_unset(monkeypatch, tmp_path, capsys):
    """
    AGENT_ROOT未設定のままログを書いても、カレントディレクトリ等へ
    ファイルを作らず、コンソール出力だけは行われること。
    """
    from agent_core.config import CONFIG
    from agent_core.logs import get_logger

    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("AGENT_ROOT", raising=False)
    monkeypatch.setenv("TARGET_REPO_PATH", str(tmp_path / "tools"))
    monkeypatch.setenv("GITHUB_TOKEN", "dummy")
    monkeypatch.setenv("GITHUB_OWNER", "test-owner")
    monkeypatch.setenv("GITHUB_REPO", "tools")
    CONFIG.__init__()

    before = set(tmp_path.iterdir())
    logger = get_logger("unset-test")
    logger.info("これはファイルに書かれないはず")
    after = set(tmp_path.iterdir())

    assert after == before, f"AGENT_ROOT未設定なのに何か作られた: {after - before}"

    captured = capsys.readouterr()
    assert "これはファイルに書かれないはず" in captured.out

    # 後始末: 次のテストに影響しないよう正常な状態へ戻す
    monkeypatch.setenv("AGENT_ROOT", str(tmp_path / "agent-root"))
    CONFIG.__init__()
