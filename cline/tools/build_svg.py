"""README埋め込み用のSVGを生成する。

GitHubのMarkdownではSVG内のCSSクラスや外部フォントが効かないため、
すべての色をfill/stroke属性で直接指定し、背景を明示する。
これでライトテーマ・ダークテーマのどちらでも同じ見た目になる。
"""

import pathlib
import xml.sax.saxutils as esc

OUT = pathlib.Path("docs/images")
OUT.mkdir(parents=True, exist_ok=True)

FONT = "-apple-system,'Segoe UI','Hiragino Sans','Noto Sans JP',Meiryo,sans-serif"

PALETTE = {
    "red":   {"fill": "#FCEBEB", "stroke": "#A32D2D", "title": "#791F1F", "sub": "#A32D2D"},
    "amber": {"fill": "#FAEEDA", "stroke": "#854F0B", "title": "#633806", "sub": "#854F0B"},
    "gray":  {"fill": "#F1EFE8", "stroke": "#5F5E5A", "title": "#2C2C2A", "sub": "#5F5E5A"},
    "green": {"fill": "#EAF3DE", "stroke": "#3F6212", "title": "#173404", "sub": "#3F6212"},
}

# (工程タイトル, 工程サブ, [(色, 弱点タイトル, 弱点サブ), ...])
STAGES = [
    ("Teams依頼", "request/ にJSON", [
        ("amber", "弱点6 OneDrive依存", "同期遅延・競合コピー・1分ポーリング")]),
    ("Issue作成", "issue_agent.py", [
        ("gray", "弱点10 トークン平文", "gh認証へ一本化すれば廃止できる")]),
    ("並走制御", "orchestrator.py", [
        ("green", "弱点7 ロック誤奪取 (修正済)", "ホスト名を記録し他PCのロックは回収しない")]),
    ("Clineへ投入", "GUI操作で貼り付け", [
        ("green", "弱点1 ウィンドウ誤爆 (修正済)", "正規表現で数字境界を判定"),
        ("amber", "弱点5 GUI座標依存", "解像度やDPIの変更で無言で壊れる")]),
    ("質問と回答", "question → decision", [
        ("green", "弱点2 questionId未照合 (修正済)", "questionId不一致の回答は破棄")]),
    ("完了判定", ".agent-summary.json", [
        ("green", "弱点4 完了判定が甘い (修正済)", "フォールバックは要確認警告付きに")]),
    ("検証公開", "tools-beta preview", [
        ("green", "弱点3 CSSや画像が欠落 (修正済)", "worktree全体を配置するよう変更")]),
    ("Teams承認", "waiting → approval", [
        ("gray", "弱点9 承認者が無制限", "誰でも押せる・記録も残らない")]),
    ("マージとクローズ", "squash merge", [
        ("green", "弱点8 マージ前検査なし (修正済)", "HTML/リンク切れを検査してから")]),
]

LEFT_X, LEFT_W = 40, 250
RIGHT_X, RIGHT_W = 310, 330
BOX_H = 56
GAP = 24          # 工程間のすき間
CARD_GAP = 8      # 弱点カード同士のすき間
TOP = 70

parts: list[str] = []


def text(x, y, value, color, size, weight="400", anchor="start"):
    return (
        f'<text x="{x}" y="{y}" fill="{color}" font-family="{FONT}" '
        f'font-size="{size}" font-weight="{weight}" text-anchor="{anchor}" '
        f'dominant-baseline="central">{esc.escape(value)}</text>'
    )


def box(x, y, w, h, key):
    c = PALETTE[key]
    return (
        f'<rect x="{x}" y="{y}" width="{w}" height="{h}" rx="8" '
        f'fill="{c["fill"]}" stroke="{c["stroke"]}" stroke-width="0.5"/>'
    )


# ---- 高さを先に確定させる ----
positions = []
y = TOP
for title, sub, cards in STAGES:
    card_block = len(cards) * BOX_H + (len(cards) - 1) * CARD_GAP
    positions.append((y, card_block))
    y += max(BOX_H, card_block) + GAP
height = y - GAP + 40

# ---- 凡例 ----
for index, (key, caption) in enumerate(
    (("red", "今すぐ修正"), ("amber", "設計の論点"), ("gray", "運用改善"), ("green", "修正済み"))
):
    lx = 40 + index * 130
    c = PALETTE[key]
    parts.append(
        f'<rect x="{lx}" y="32" width="14" height="14" rx="3" '
        f'fill="{c["fill"]}" stroke="{c["stroke"]}" stroke-width="0.5"/>'
    )
    parts.append(text(lx + 22, 39, caption, "#5F5E5A", 12))

# ---- 本体 ----
for index, ((title, sub, cards), (top, card_block)) in enumerate(zip(STAGES, positions)):
    center = LEFT_X + LEFT_W / 2

    parts.append(box(LEFT_X, top, LEFT_W, BOX_H, "gray"))
    parts.append(text(center, top + 20, title, PALETTE["gray"]["title"], 14, "500", "middle"))
    parts.append(text(center, top + 38, sub, PALETTE["gray"]["sub"], 12, "400", "middle"))

    for card_index, (key, card_title, card_sub) in enumerate(cards):
        cy = top + card_index * (BOX_H + CARD_GAP)
        c = PALETTE[key]
        parts.append(box(RIGHT_X, cy, RIGHT_W, BOX_H, key))
        parts.append(text(RIGHT_X + 16, cy + 20, card_title, c["title"], 14, "500"))
        parts.append(text(RIGHT_X + 16, cy + 38, card_sub, c["sub"], 12))
        parts.append(
            f'<line x1="{LEFT_X + LEFT_W + 2}" y1="{top + BOX_H / 2}" '
            f'x2="{RIGHT_X - 2}" y2="{cy + BOX_H / 2}" stroke="#B4B2A9" '
            f'stroke-width="0.5" stroke-dasharray="3 3"/>'
        )

    if index < len(STAGES) - 1:
        next_top = positions[index + 1][0]
        parts.append(
            f'<line x1="{center}" y1="{top + max(BOX_H, card_block) + 2}" '
            f'x2="{center}" y2="{next_top - 4}" stroke="#888780" '
            f'stroke-width="1.5" marker-end="url(#arrow)"/>'
        )

svg = f"""<svg xmlns="http://www.w3.org/2000/svg" width="680" height="{height}" viewBox="0 0 680 {height}" role="img">
<title>Teams依頼からマージまでの流れと設計上の弱点</title>
<desc>9工程を縦に並べ、各工程の右側に対応する設計上の弱点を緊急度別に色分けして示した図。</desc>
<defs><marker id="arrow" viewBox="0 0 10 10" refX="8" refY="5" markerWidth="6" markerHeight="6" orient="auto-start-reverse"><path d="M2 1L8 5L2 9" fill="none" stroke="#888780" stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round"/></marker></defs>
<rect x="0" y="0" width="680" height="{height}" rx="12" fill="#FFFFFF"/>
{chr(10).join(parts)}
</svg>
"""

path = OUT / "flow-weaknesses.svg"
path.write_text(svg, encoding="utf-8")
print(f"生成: {path} ({len(svg)} bytes, height={height})")
