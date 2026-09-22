"""README.md と docs/*.md を、ナビゲーション付きの静的HTMLサイトに変換する。

出力先: docs-html/
    index.html            ← README.md
    01_architecture.html
    02_setup.html
    03_power_automate.html
    04_operations.html
    images/*.svg          ← docs/images/ をそのままコピー

Markdown内の相対リンク（*.md, docs/*.md, images/*.svg）は
HTML版のパスに書き換える。
"""

import pathlib
import re
import shutil

import markdown
from pymdownx.slugs import slugify

ROOT = pathlib.Path(__file__).resolve().parent.parent

# 見出しIDに日本語をそのまま使う（既定のtoc実装は非ASCIIを "_1" 等に潰してしまい、
# README側のアンカーリンクと一致しなくなるため）。
UNICODE_SLUGIFY = slugify(case="lower")
OUT = ROOT / "docs-html"
IMAGES_OUT = OUT / "images"

# (元ファイル, 出力ファイル名, ナビゲーションの表示名)
PAGES = [
    (ROOT / "README.md", "index.html", "概要"),
    (ROOT / "docs" / "01_architecture.md", "01_architecture.html", "1. アーキテクチャ"),
    (ROOT / "docs" / "02_setup.md", "02_setup.html", "2. 導入手順"),
    (ROOT / "docs" / "03_power_automate.md", "03_power_automate.html", "3. Power Automate"),
    (ROOT / "docs" / "04_operations.md", "04_operations.html", "4. 運用"),
    (ROOT / "docs" / "05_verification.md", "05_verification.html", "5. 動作確認"),
]

MD_EXTENSIONS = [
    "tables",
    "fenced_code",
    "toc",
    "sane_lists",
    "attr_list",
]

MD_EXTENSION_CONFIGS = {
    "toc": {"slugify": UNICODE_SLUGIFY},
}

CSS = """
:root {
  --bg: #F5F4F0; --surface: #FFFFFF; --border: #D3D1C7;
  --text: #2C2C2A; --text-secondary: #5F5E5A; --text-muted: #888780;
  --accent: #0C447C; --accent-bg: #E6F1FB;
  --code-bg: #F1EFE8;
}
* { box-sizing: border-box; }
body {
  margin: 0; background: var(--bg); color: var(--text);
  font-family: -apple-system, "Segoe UI", "Hiragino Sans", "Noto Sans JP", Meiryo, sans-serif;
  line-height: 1.75; font-size: 15px;
}
.layout { display: flex; min-height: 100vh; }
nav {
  width: 220px; flex-shrink: 0; background: var(--surface);
  border-right: 0.5px solid var(--border); padding: 24px 16px;
  position: sticky; top: 0; height: 100vh; overflow-y: auto;
}
nav .brand { font-size: 13px; font-weight: 500; color: var(--text-secondary); margin: 0 8px 16px; }
nav a {
  display: block; padding: 8px; border-radius: 8px; color: var(--text);
  text-decoration: none; font-size: 14px; margin-bottom: 2px;
}
nav a:hover { background: var(--code-bg); }
nav a.active { background: var(--accent-bg); color: var(--accent); font-weight: 500; }
main { flex: 1; min-width: 0; padding: 40px 48px; max-width: 900px; }
h1, h2, h3 { font-weight: 500; line-height: 1.4; }
h1 { font-size: 26px; margin: 0 0 20px; }
h2 { font-size: 20px; margin: 40px 0 16px; padding-top: 8px; border-top: 0.5px solid var(--border); }
h3 { font-size: 17px; margin: 28px 0 12px; }
p { margin: 0 0 14px; }
a { color: var(--accent); }
img { max-width: 100%; height: auto; }
table { border-collapse: collapse; width: 100%; margin: 0 0 20px; font-size: 14px; }
th, td { border: 0.5px solid var(--border); padding: 8px 12px; text-align: left; vertical-align: top; }
th { background: var(--code-bg); font-weight: 500; }
code { background: var(--code-bg); border-radius: 4px; padding: 1px 6px; font-size: 13px;
  font-family: "Consolas", "SF Mono", monospace; }
pre { background: var(--code-bg); border-radius: 8px; padding: 16px; overflow-x: auto;
  font-size: 13px; line-height: 1.6; }
pre code { background: none; padding: 0; }
blockquote { border-left: 3px solid var(--border); margin: 0 0 14px; padding: 4px 16px;
  color: var(--text-secondary); }
ul, ol { padding-left: 24px; margin: 0 0 14px; }
li { margin-bottom: 4px; }
hr { border: none; border-top: 0.5px solid var(--border); margin: 32px 0; }
p[align="center"] { text-align: center; margin: 24px 0; }
"""


def build_nav(active_file: str) -> str:
    items = []
    for _, filename, title in PAGES:
        cls = ' class="active"' if filename == active_file else ""
        items.append(f'<a href="{filename}"{cls}>{title}</a>')
    return (
        '<nav><p class="brand">AIエージェント基盤</p>' + "".join(items) + "</nav>"
    )


def rewrite_links(md_text: str) -> str:
    """Markdown内の相対参照をHTML版のパスへ書き換える。"""
    # docs/xx.md や ./01_xx.md → 01_xx.html（README・docs間の相互参照）
    md_text = re.sub(r"\]\((?:\./)?docs/(\d\d_[a-z_]+)\.md(#[^\)]*)?\)",
                      r"](\1.html\2)", md_text)
    md_text = re.sub(r"\]\((\d\d_[a-z_]+)\.md(#[^\)]*)?\)", r"](\1.html\2)", md_text)
    # docs内からREADME.mdへの相対参照
    md_text = re.sub(r"\]\((?:\.\./)?README\.md(#[^\)]*)?\)", r"](index.html\1)", md_text)
    # docs/images/xxx.svg（README用）→ images/xxx.svg
    md_text = md_text.replace("docs/images/", "images/")
    # <img src="docs/images/..."> 形式のHTMLタグにも対応
    md_text = md_text.replace('src="docs/images/', 'src="images/')
    return md_text


def convert(source: pathlib.Path, filename: str, title: str) -> str:
    raw = source.read_text(encoding="utf-8")
    raw = rewrite_links(raw)

    body = markdown.markdown(
        raw, extensions=MD_EXTENSIONS, extension_configs=MD_EXTENSION_CONFIGS
    )

    return f"""<!DOCTYPE html>
<html lang="ja">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{title} - AIエージェント基盤ドキュメント</title>
<style>{CSS}</style>
</head>
<body>
<div class="layout">
{build_nav(filename)}
<main>
{body}
</main>
</div>
</body>
</html>
"""


def main() -> None:
    if OUT.exists():
        shutil.rmtree(OUT)
    OUT.mkdir(parents=True)

    images_src = ROOT / "docs" / "images"
    if images_src.exists():
        shutil.copytree(images_src, IMAGES_OUT)

    for source, filename, title in PAGES:
        html = convert(source, filename, title)
        (OUT / filename).write_text(html, encoding="utf-8")
        print(f"生成: docs-html/{filename}")


if __name__ == "__main__":
    main()
