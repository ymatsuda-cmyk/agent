from __future__ import annotations

from agent_core import checks


def test_check_html_balance_detects_unclosed_tag(tmp_path):
    path = tmp_path / "broken.html"
    path.write_text("<div><p>test</div>", encoding="utf-8")

    issues = checks.check_html_balance(path)
    assert any("閉じ" in issue for issue in issues)


def test_check_html_balance_accepts_valid_html(tmp_path):
    path = tmp_path / "ok.html"
    path.write_text("<div><p>test</p></div>", encoding="utf-8")

    assert checks.check_html_balance(path) == []


def test_check_html_balance_ignores_void_tags(tmp_path):
    path = tmp_path / "void.html"
    path.write_text('<div><img src="x.png"><br><p>ok</p></div>', encoding="utf-8")

    assert checks.check_html_balance(path) == []


def test_check_broken_local_links_detects_missing_file(tmp_path):
    path = tmp_path / "page.html"
    path.write_text('<link rel="stylesheet" href="missing.css">', encoding="utf-8")

    issues = checks.check_broken_local_links(path, tmp_path)
    assert any("リンク切れ" in issue for issue in issues)


def test_check_broken_local_links_accepts_existing_file(tmp_path):
    (tmp_path / "style.css").write_text("body{}", encoding="utf-8")
    path = tmp_path / "page.html"
    path.write_text('<link rel="stylesheet" href="style.css">', encoding="utf-8")

    assert checks.check_broken_local_links(path, tmp_path) == []


def test_check_broken_local_links_skips_external_and_anchor(tmp_path):
    path = tmp_path / "page.html"
    path.write_text(
        '<a href="https://example.com">ext</a><a href="#top">anchor</a>'
        '<a href="mailto:a@b.com">mail</a>',
        encoding="utf-8",
    )

    assert checks.check_broken_local_links(path, tmp_path) == []


def test_check_broken_local_links_resolves_root_relative_path(tmp_path):
    (tmp_path / "shared").mkdir()
    (tmp_path / "shared" / "style.css").write_text("body{}", encoding="utf-8")

    nested = tmp_path / "shop"
    nested.mkdir()
    path = nested / "page.html"
    path.write_text('<link rel="stylesheet" href="/shared/style.css">', encoding="utf-8")

    # "/"始まりはリポジトリルート基準で解決されること
    assert checks.check_broken_local_links(path, tmp_path) == []


def test_check_files_only_reports_files_with_issues(tmp_path):
    (tmp_path / "shop").mkdir()
    (tmp_path / "shop" / "style.css").write_text("body{}", encoding="utf-8")

    broken = tmp_path / "shop" / "broken.html"
    broken.write_text(
        '<link rel="stylesheet" href="missing.css"><div><p>test</div>', encoding="utf-8"
    )

    ok = tmp_path / "shop" / "ok.html"
    ok.write_text('<link rel="stylesheet" href="style.css"><p>ok</p>', encoding="utf-8")

    result = checks.check_files(tmp_path, ["shop/broken.html", "shop/ok.html"])

    assert "shop/ok.html" not in result
    assert "shop/broken.html" in result
    assert any("リンク切れ" in issue for issue in result["shop/broken.html"])
    assert any("閉じ" in issue for issue in result["shop/broken.html"])


def test_check_files_ignores_non_html_and_missing_files(tmp_path):
    (tmp_path / "data.json").write_text("{}", encoding="utf-8")
    result = checks.check_files(tmp_path, ["data.json", "does-not-exist.html"])
    assert result == {}
