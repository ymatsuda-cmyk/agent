"""アクター別スイムレーン図（docs/images/actor-swimlane.svg）を生成する。

GitHubのMarkdownではSVG内のCSSクラスが効かないため、
色はすべてfill/stroke属性で直接指定する。
SVGのtextは自動折り返ししないので、改行は行単位で明示する。
"""

import pathlib
import xml.sax.saxutils as esc

OUT = pathlib.Path("docs/images")
OUT.mkdir(parents=True, exist_ok=True)

FONT = "-apple-system,'Segoe UI','Hiragino Sans','Noto Sans JP',Meiryo,sans-serif"

WIDTH = 1080
MARGIN = 15
LANES = ["人 / Teams", "Power Automate", "OneDrive", "Python", "Cline", "GitHub"]
LANE_W = (WIDTH - MARGIN * 2) // len(LANES)
CARD_PAD_X = 8
CARD_W = LANE_W - CARD_PAD_X * 2

HEADER_H = 30
BAND_H = 26
TITLE_LH = 17
SUB_LH = 15
CARD_PAD_Y = 10
ROW_PAD = 12

TONES = {
    "normal": {"band": "#EEEDFE", "bandText": "#3C3489", "cardStroke": "#B4B2A9"},
    "rework": {"band": "#FAEEDA", "bandText": "#633806", "cardStroke": "#BA7517"},
    "reject": {"band": "#FCEBEB", "bandText": "#791F1F", "cardStroke": "#E24B4A"},
}

CARD_FILL = "#FFFFFF"
TITLE_COLOR = "#2C2C2A"
SUB_COLOR = "#77766F"
LANE_LINE = "#D3D1C7"
HEADER_COLOR = "#5F5E5A"

# (トーン, 帯ラベル, [(レーン番号, タイトル行, 補足行), ...])
BANDS = [
    ("normal", "（状態なし） ・ 依頼が入る", [
        (0, ["依頼を投稿"], []),
        (1, ["投稿を検知"], ["1分間隔のポーリング"]),
        (2, ["request/"], []),
    ]),
    ("normal", "created ・ 着手待ち", [
        (2, ["state/ に記録"], ["request → done"]),
        (3, ["issue_agent"], ["Message ID で重複排除"]),
        (5, ["Issue #12 を作成"], ["open"]),
    ]),
    ("normal", "queued → implementing ・ 並走の起点", [
        (3, ["orchestrator"], ["worktree を作成", "GUIロックを取得"]),
        (4, ["実装を開始"], ["プロンプトを受領"]),
        (5, ["ブランチを作成"], ["feature/issue-12-…"]),
    ]),
    ("normal", "waiting_decision ・ 質問が Cline から Teams へ上がる", [
        (0, ["選択して回答"], []),
        (1, ["質問カードを投稿"], []),
        (2, ["question/"], []),
        (3, ["質問を転記"], []),
        (4, [".agent-question", ".json を作成"], ["実装を停止"]),
    ]),
    ("normal", "implementing ・ 回答が Teams から Cline へ戻る", [
        (1, ["回答を書き出し"], []),
        (2, ["decision/"], []),
        (3, ["回答を投入"], ["question / decision", "を done へ退避"]),
        (4, ["実装を再開"], ["完了時にサマリー"]),
    ]),
    ("normal", "preview_deploying → waiting_approval ・ 承認の材料を揃える", [
        (3, ["commit / push", "ドラフトPR作成", "プレビュー公開"], []),
        (5, ["PR #34 draft"], ["tools-beta Pages"]),
    ]),
    ("normal", "waiting_approval ・ 承認カードが出る", [
        (0, ["プレビューを確認"], ["承認 / 却下", "再実装 を選ぶ"]),
        (1, ["承認カードを投稿", "結果を書き出し"], []),
        (2, ["waiting/", "approval/"], []),
        (3, ["approval_agent"], ["3方向へ振り分け"]),
    ]),
    ("normal", "approved → completed ・ 承認された場合", [
        (0, ["スレッドに", "完了通知が届く"], []),
        (1, ["reply/ を投稿", "done へ移動"], []),
        (2, ["state/done/"], []),
        (3, ["finish_agent"], ["worktree を削除", "プレビューを削除"]),
        (5, ["squash merge"], ["main へ反映", "Issue をクローズ"]),
    ]),
    ("rework", "rework ・ 再実装が選ばれた場合は実装中へ戻る", [
        (0, ["修正指示を入力"], []),
        (2, ["waiting/done/"], []),
        (3, ["rework へ戻す"], ["同じブランチ", "同じ worktree"]),
        (4, ["修正を実装"], []),
        (5, ["PRへ追加コミット"], ["PRは自動更新"]),
    ]),
    ("reject", "rejected ・ 却下された場合", [
        (0, ["却下通知が届く"], []),
        (2, ["state/done/"], []),
        (3, ["変更を破棄"], ["worktree を削除"]),
        (5, ["PRをクローズ"], ["ブランチを削除"]),
    ]),
]


def text(x, y, value, color, size, weight="400"):
    return (
        f'<text x="{x}" y="{y}" fill="{color}" font-family="{FONT}" '
        f'font-size="{size}" font-weight="{weight}">{esc.escape(value)}</text>'
    )


def lane_x(index: int) -> int:
    return MARGIN + index * LANE_W


def card_height(titles: list[str], subs: list[str]) -> int:
    return CARD_PAD_Y * 2 + len(titles) * TITLE_LH + len(subs) * SUB_LH


parts: list[str] = []
y = 0

# ---- ヘッダー ----
for index, name in enumerate(LANES):
    cx = lane_x(index) + LANE_W // 2
    parts.append(
        f'<text x="{cx}" y="{20}" fill="{HEADER_COLOR}" font-family="{FONT}" '
        f'font-size="12" font-weight="500" text-anchor="middle">{esc.escape(name)}</text>'
    )
y = HEADER_H
parts.append(
    f'<line x1="{MARGIN}" y1="{y}" x2="{WIDTH - MARGIN}" y2="{y}" '
    f'stroke="{HEADER_COLOR}" stroke-width="1"/>'
)

# ---- 帯と行 ----
for tone_key, label, cards in BANDS:
    tone = TONES[tone_key]

    parts.append(
        f'<rect x="{MARGIN}" y="{y}" width="{WIDTH - MARGIN * 2}" height="{BAND_H}" '
        f'fill="{tone["band"]}"/>'
    )
    parts.append(text(MARGIN + 12, y + 17, label, tone["bandText"], 12, "500"))
    y += BAND_H

    row_h = max(card_height(t, s) for _, t, s in cards) + ROW_PAD

    for index in range(1, len(LANES)):
        lx = lane_x(index)
        parts.append(
            f'<line x1="{lx}" y1="{y}" x2="{lx}" y2="{y + row_h}" '
            f'stroke="{LANE_LINE}" stroke-width="0.5"/>'
        )

    for index, titles, subs in cards:
        cx = lane_x(index) + CARD_PAD_X
        ch = card_height(titles, subs)
        cy = y + (row_h - ch) // 2

        parts.append(
            f'<rect x="{cx}" y="{cy}" width="{CARD_W}" height="{ch}" rx="6" '
            f'fill="{CARD_FILL}" stroke="{tone["cardStroke"]}" stroke-width="0.8"/>'
        )

        ty = cy + CARD_PAD_Y + 12
        for line in titles:
            parts.append(text(cx + 10, ty, line, TITLE_COLOR, 12, "500"))
            ty += TITLE_LH
        for line in subs:
            parts.append(text(cx + 10, ty, line, SUB_COLOR, 11))
            ty += SUB_LH

    y += row_h

height = y + MARGIN

svg = f"""<svg xmlns="http://www.w3.org/2000/svg" width="{WIDTH}" height="{height}" viewBox="0 0 {WIDTH} {height}" role="img">
<title>アクター別スイムレーンと状態遷移</title>
<desc>人とTeams、Power Automate、OneDrive、Python、Cline、GitHubの6アクターを列に取り、state/issue-N.json の状態ごとに誰が何をするかを上から下に並べた図。</desc>
<rect x="0" y="0" width="{WIDTH}" height="{height}" fill="#FFFFFF"/>
{chr(10).join(parts)}
</svg>
"""

path = OUT / "actor-swimlane.svg"
path.write_text(svg, encoding="utf-8")
print(f"生成: {path} ({len(svg)} bytes, {WIDTH}x{height})")
