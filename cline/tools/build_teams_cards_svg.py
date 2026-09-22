"""Teamsに投稿される3種類のカードのモックアップ（docs/images/teams-cards.svg）を生成する。

実際のAdaptive Cardの見た目そのものではなく、
「どの情報が」「どの操作と一緒に」届くかを示す簡略版。
GitHubのMarkdownで確実に表示させるため、色はすべて属性で直接指定し、
Webフォントのアイコンは使わずSVG基本図形だけで表現する。
"""

import pathlib
import xml.sax.saxutils as esc

OUT = pathlib.Path("docs/images")
OUT.mkdir(parents=True, exist_ok=True)

FONT = "-apple-system,'Segoe UI','Hiragino Sans','Noto Sans JP',Meiryo,sans-serif"
MONO = "'Consolas','SF Mono',monospace"

WIDTH = 640
PAD = 20
CARD_X = PAD
CARD_W = WIDTH - PAD * 2

INK = {
    "primary": "#2C2C2A",
    "secondary": "#5F5E5A",
    "muted": "#888780",
    "border": "#D3D1C7",
    "accent_bg": "#E6F1FB",
    "accent_text": "#0C447C",
    "amber_bg": "#FAEEDA",
    "amber_text": "#633806",
    "green_bg": "#EAF3DE",
    "green_text": "#173404",
    "red_bg": "#FCEBEB",
    "red_text": "#791F1F",
}

parts: list[str] = []
y = PAD


def text(x, y_, value, color, size, weight="400", family=FONT, anchor="start"):
    return (
        f'<text x="{x}" y="{y_}" fill="{color}" font-family="{family}" '
        f'font-size="{size}" font-weight="{weight}" text-anchor="{anchor}">'
        f"{esc.escape(value)}</text>"
    )


def header(top, timestamp):
    """bot アイコン・名前・時刻の行を描き、次の描画開始y座標を返す。"""
    global parts
    icon_y = top + 14
    parts.append(
        f'<rect x="{CARD_X + 16}" y="{top}" width="28" height="28" rx="6" '
        f'fill="{INK["accent_bg"]}"/>'
    )
    parts.append(text(CARD_X + 30, icon_y + 4, "AI", INK["accent_text"], 11, "500", anchor="middle"))
    parts.append(text(CARD_X + 54, icon_y - 1, "AIエージェント bot", INK["primary"], 13, "500"))
    parts.append(text(CARD_X + 54, icon_y + 15, timestamp, INK["muted"], 11))
    return top + 44


def card_bg(top, height, insert_at):
    """
    カード背景を、そのカードの内容より前（＝下のレイヤー）に挿入する。

    内容を描いた後で高さを確定するため、背景は最後に作るが、
    parts の末尾に追加すると内容の上に重なって隠れてしまう。
    そのため内容の描画を始めた位置(insert_at)へ挿入する。
    """
    parts.insert(
        insert_at,
        f'<rect x="{CARD_X}" y="{top}" width="{CARD_W}" height="{height}" rx="12" '
        f'fill="#FFFFFF" stroke="{INK["border"]}" stroke-width="0.5"/>',
    )


def link(x, y_, label):
    parts.append(text(x, y_, label, INK["accent_text"], 13))
    w = len(label) * 13 * 0.62
    parts.append(
        f'<line x1="{x}" y1="{y_ + 3}" x2="{x + w}" y2="{y_ + 3}" '
        f'stroke="{INK["accent_text"]}" stroke-width="0.6"/>'
    )
    return x + w


def button(x, y_, w, h, label, fill, stroke, textcolor):
    parts.append(
        f'<rect x="{x}" y="{y_}" width="{w}" height="{h}" rx="8" '
        f'fill="{fill}" stroke="{stroke}" stroke-width="0.8"/>'
    )
    parts.append(text(x + w / 2, y_ + h / 2 + 4, label, textcolor, 13, "500", anchor="middle"))


def chevron(cx, cy, expanded, color):
    """開閉トグルのシェブロン。expanded=True で上向き(閉じる)、False で下向き(開く)。"""
    if expanded:
        d = f"M {cx - 5} {cy + 3} L {cx} {cy - 3} L {cx + 5} {cy + 3}"
    else:
        d = f"M {cx - 5} {cy - 3} L {cx} {cy + 3} L {cx + 5} {cy - 3}"
    parts.append(
        f'<path d="{d}" fill="none" stroke="{color}" stroke-width="1.6" '
        f'stroke-linecap="round" stroke-linejoin="round"/>'
    )


def accordion_row(top, w, label, expanded, highlight=False):
    """アコーディオンの見出し行（上罫線＋ラベル＋シェブロン）を描く。次の開始y座標を返す。"""
    row_h = 40
    if highlight:
        parts.append(
            f'<rect x="{CARD_X + 12}" y="{top}" width="{w + 8}" height="{row_h}" rx="6" '
            f'fill="{INK["accent_bg"]}"/>'
        )
    parts.append(
        f'<line x1="{CARD_X + 16}" y1="{top}" x2="{CARD_X + 16 + w}" y2="{top}" '
        f'stroke="{INK["border"]}" stroke-width="0.5"/>'
    )
    parts.append(text(CARD_X + 16, top + row_h / 2 + 4, label, INK["primary"], 13, "500"))
    chevron(CARD_X + 16 + w - 8, top + row_h / 2, expanded, INK["secondary"])
    return top + row_h


def accordion_body(top, w, lines, mono=False):
    """展開時の本文（インデント表示）を描く。次の開始y座標を返す。"""
    line_h = 20
    for i, line in enumerate(lines):
        parts.append(
            text(CARD_X + 16, top + 14 + i * line_h, line, INK["primary"],
                 12 if mono else 13, family=MONO if mono else FONT)
        )
    return top + len(lines) * line_h + 8


# ============================================================
# カード1: Issue作成通知
# ============================================================

card1_top = y
card1_at = len(parts)
inner = card1_top + 20
inner = header(inner, "火曜 14:02")
parts.append(text(CARD_X + 16, inner + 6, "Issue #12 を作成しました", INK["primary"], 14, "500"))
parts.append(text(CARD_X + 16, inner + 27, "タイトル: 顧客一覧ページを追加", INK["secondary"], 13))
parts.append(text(CARD_X + 16, inner + 46, "順番が来次第、自動で実装を開始します。", INK["secondary"], 13))
link(CARD_X + 16, inner + 70, "Issueを開く \u2197")
card1_h = (inner + 70) - card1_top + 16
card_bg(card1_top, card1_h, card1_at)
y = card1_top + card1_h + 20

# ============================================================
# カード2: 質問カード
# ============================================================

card2_top = y
card2_at = len(parts)
inner = card2_top + 20
inner = header(inner, "火曜 14:11")
parts.append(text(CARD_X + 16, inner + 6, "Issue #12 顧客一覧ページを追加", INK["primary"], 14, "500"))
parts.append(text(CARD_X + 16, inner + 27, "顧客データの取得元をどうしますか。", INK["secondary"], 13))

choices = [
    ("customer.json から読む", "既存の静的ファイルを流用する", True),
    ("既存APIを流用する", "社内APIから都度取得する", False),
    ("その他（下に入力）", None, False),
]
choice_top = inner + 44
choice_h = 40
for i, (title, sub, checked) in enumerate(choices):
    cy = choice_top + i * (choice_h + 8)
    parts.append(
        f'<rect x="{CARD_X + 16}" y="{cy}" width="{CARD_W - 32}" height="{choice_h}" rx="8" '
        f'fill="#FFFFFF" stroke="{INK["border"]}" stroke-width="0.6"/>'
    )
    cx, ccy = CARD_X + 32, cy + choice_h / 2
    parts.append(f'<circle cx="{cx}" cy="{ccy}" r="7" fill="none" stroke="{INK["muted"]}" stroke-width="1.2"/>')
    if checked:
        parts.append(f'<circle cx="{cx}" cy="{ccy}" r="3.5" fill="{INK["accent_text"]}"/>')
    tx = CARD_X + 48
    if sub:
        parts.append(text(tx, cy + 17, title, INK["primary"], 12, "500"))
        parts.append(text(tx, cy + 32, sub, INK["secondary"], 11))
    else:
        parts.append(text(tx, ccy + 4, title, INK["primary"], 12, "500"))

input_top = choice_top + len(choices) * (choice_h + 8) + 4
parts.append(
    f'<rect x="{CARD_X + 16}" y="{input_top}" width="{CARD_W - 32}" height="34" rx="8" '
    f'fill="#FFFFFF" stroke="{INK["border"]}" stroke-width="0.6"/>'
)
parts.append(text(CARD_X + 28, input_top + 21, "カスタム回答を入力（任意）", INK["muted"], 12))

btn_top = input_top + 34 + 12
button(CARD_X + 16, btn_top, 120, 34, "回答を送信", INK["primary"], INK["primary"], "#FFFFFF")

card2_h = (btn_top + 34) - card2_top + 16
card_bg(card2_top, card2_h, card2_at)
y = card2_top + card2_h + 20

# ============================================================
# カード3a: 承認カード（未展開）
# ============================================================

CONTENT_W = CARD_W - 32


def approval_card(top, timestamp, impl_expanded, files_expanded, caption):
    """承認カードを描く。折りたたみ状態を引数で切り替えられる。次の開始y座標を返す。"""
    global y
    at = len(parts)
    parts.append(text(CARD_X, top - 8, caption, INK["secondary"], 12, "500"))
    card_top = top + 12
    inner = card_top + 20
    inner = header(inner, timestamp)
    parts.append(text(CARD_X + 16, inner + 6, "Issue #12 顧客一覧ページを追加", INK["primary"], 14, "500"))

    row_top = inner + 24
    row_top = accordion_row(row_top, CONTENT_W, "実装内容", impl_expanded, highlight=impl_expanded)
    if impl_expanded:
        row_top = accordion_body(row_top, CONTENT_W, [
            "customer.json を読み込む一覧ページを作成し、",
            "氏名・会社名・登録日でソートできるようにしました。",
        ])

    row_top = accordion_row(row_top, CONTENT_W, "変更ファイル（1件）", files_expanded, highlight=files_expanded)
    if files_expanded:
        row_top = accordion_body(row_top, CONTENT_W, ["customer/index.html"], mono=True)

    warn_top = row_top + 10
    warn_h = 46
    parts.append(
        f'<rect x="{CARD_X + 16}" y="{warn_top}" width="{CONTENT_W}" height="{warn_h}" rx="8" '
        f'fill="{INK["amber_bg"]}"/>'
    )
    parts.append(text(CARD_X + 28, warn_top + 18, "未実施の確認（常時表示）", INK["amber_text"], 12, "500"))
    parts.append(text(CARD_X + 28, warn_top + 34, "スマートフォン表示の確認は行っていません", INK["amber_text"], 12))

    link_top = warn_top + warn_h + 22
    lx = link(CARD_X + 16, link_top, "検証環境で確認 \u2197")
    parts.append(text(lx + 12, link_top, "\u00b7", INK["border"], 13))
    link(lx + 24, link_top, "差分を見る \u2197")

    btn_top = link_top + 20
    btn_w = (CONTENT_W - 16) / 3
    button(CARD_X + 16, btn_top, btn_w, 36, "承認", INK["green_bg"], INK["green_bg"], INK["green_text"])
    button(CARD_X + 16 + btn_w + 8, btn_top, btn_w, 36, "再実装", "#FFFFFF", INK["border"], INK["primary"])
    button(CARD_X + 16 + (btn_w + 8) * 2, btn_top, btn_w, 36, "却下", INK["red_bg"], INK["red_bg"], INK["red_text"])

    card_h = (btn_top + 36) - card_top + 16
    card_bg(card_top, card_h, at)
    return card_top + card_h


card3a_top = y + 20
card3a_bottom = approval_card(
    card3a_top, "火曜 14:48", impl_expanded=False, files_expanded=False,
    caption="承認カード（未展開）",
)

# --- 「実装内容」行をクリックした、という遷移を示す矢印 ---
arrow_top = card3a_bottom + 14
arrow_cx = WIDTH / 2
parts.append(
    f'<line x1="{arrow_cx}" y1="{arrow_top}" x2="{arrow_cx}" y2="{arrow_top + 26}" '
    f'stroke="{INK["accent_text"]}" stroke-width="1.4" marker-end="url(#click-arrow)"/>'
)
parts.append(
    text(arrow_cx + 12, arrow_top + 18, "「実装内容」の行をクリック", INK["accent_text"], 12, "500")
)

card3b_top = arrow_top + 40
card3b_bottom = approval_card(
    card3b_top, "火曜 14:48", impl_expanded=True, files_expanded=False,
    caption="承認カード（実装内容を展開）",
)

y = card3b_bottom + PAD
height = y

svg = f"""<svg xmlns="http://www.w3.org/2000/svg" width="{WIDTH}" height="{height}" viewBox="0 0 {WIDTH} {height}" role="img">
<title>Teamsに投稿される3種類のカードのモックアップ</title>
<desc>Issue作成通知、Clineからの質問カード、実装完了の承認カード（未展開と実装内容を展開した状態）を時系列に並べたモックアップ。実際のAdaptive Cardのレイアウトを簡略化したもの。</desc>
<defs>
  <marker id="click-arrow" viewBox="0 0 10 10" refX="6" refY="5" markerWidth="7" markerHeight="7" orient="auto-start-reverse">
    <path d="M1 1 L8 5 L1 9" fill="none" stroke="{INK["accent_text"]}" stroke-width="1.6" stroke-linecap="round" stroke-linejoin="round"/>
  </marker>
</defs>
<rect x="0" y="0" width="{WIDTH}" height="{height}" fill="#F5F4F0" rx="12"/>
{chr(10).join(parts)}
</svg>
"""

path = OUT / "teams-cards.svg"
path.write_text(svg, encoding="utf-8")
print(f"生成: {path} ({len(svg)} bytes, {WIDTH}x{height})")
