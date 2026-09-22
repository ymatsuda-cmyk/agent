# 05. 実環境での動作確認チェックリスト

Windows・Teams・GitHub・Power Automateを実際に使った検証手順です。

**一気に全部試さないでください。** Teamsに投稿していきなり承認まで通そうとすると、
途中で止まったときにPython・Power Automate・Teams・GitHubのどこが原因か
切り分けられません。ここでは6段階に分け、**下の層から順に**確認します。

```
段階0  Python単体（Teams/Power Automateを経由しない）
段階1  ①Teams投稿 → Issue作成 だけ
段階2  質問と回答の往復（②③④）
段階3  フル1周（Issue作成 → 実装 → 承認 → マージ）
段階4  却下・再実装
段階5  並走（2件同時）
```

各段階はチェックボックス形式です。詰まったら「11. うまくいかなかったときの記録」
の形式でログをまとめてもらえれば、リモートでも調査できます。

## 0. 前提条件（ここが揃うまで先へ進まない）

- [ ] `python agent\agent_cli.py doctor` が全て `[OK]`
- [ ] `pytest`（`agent/` 配下）がグリーン
- [ ] `docs/03_power_automate.md` 5章の**必須修正**を適用済み
      （③の出力先を `/work/agent/approval` に、①⑤のBot除外・空コメント対策）
- [ ] 依頼チャネルと通知チャネルが別になっている
- [ ] `gh auth status` が通る
- [ ] `tools` と `tools-beta` がクローン済み、`tools-beta` のGitHub Pagesが有効
- [ ] 自動投入を使う場合は `agent_cli.py test-click` でCline入力欄の座標を確認済み
      （不安な場合は最初は `--manual` で進める）

**推奨**: 最初の一周は本番の `tools` ではなく、使い捨てできる検証用リポジトリで
試してください。壊れても被害が無い状態で動きを掴んでから本番へ切り替えるのが安全です。

## 1. 最小テストシナリオ

3つのIssue相当の依頼を用意します。承認・質問・却下の3系統を1つずつ確認できます。

| シナリオ | 内容 | 目的 |
| --- | --- | --- |
| A（正常系） | 「検証用ページ(verify-a)を作ってください。見出しだけのシンプルなページで構いません」 | 承認までの一周を確認 |
| B（質問系） | 「データを表示するページを作ってください」（取得元をわざと曖昧にする） | Clineが質問を出す経路を確認 |
| C（却下系） | シナリオAの完了後にわざと却下する | needs-reworkラベル付与を確認 |

---

## 段階0: Python単体（Teams/Power Automateを経由しない）

Power AutomateやTeamsの設定ミスと、Python側の不具合を切り分けるため、
最初はTeamsを一切使わずに `agent_cli.py` から直接動かします。

```powershell
# 手動でGitHub Issueを1件作る（Webから、または gh issue create）
gh issue create --repo <owner>/tools --title "検証用ページ(verify-a)" `
  --body "見出しだけのシンプルなページを作ってください。"

# 表示されたIssue番号を使って、直接実装ワーカーを起動する
python agent\implement_agent.py <Issue番号> --manual
```

- [ ] `worktrees/issue-<N>` が作られる（`git worktree list` で確認）
- [ ] コンソールに「実装完了を待機します」のログが出て、クリップボードに
      プロンプトがコピーされる
- [ ] VS Codeで該当worktreeを開き、Clineへ `Ctrl+V` → `Enter` で貼り付ける
- [ ] Clineの実装が終わったら `.agent-summary.json` を作らせる
      （プロンプト通りに動いていれば自動で作られるはず）
- [ ] コンソールに「実装完了サマリーを検出しました」と出て、コミット・push・
      ドラフトPR作成まで自動で進む
- [ ] `python agent\agent_cli.py status` で `waiting_approval` になっている
- [ ] GitHub上にドラフトPRができている（`Closes #N` を含む本文）
- [ ] tools-betaに `preview/issue-<N>/` ができ、ブラウザで崩れずに開ける
      （弱点3の確認: 変更していない共有CSS/画像も一緒に配置されているか）

ここまで通れば、Git操作・Cline連携・プレビュー公開・PR作成というPythonの
中核部分は正しく動いています。次に承認だけTeamsを介さず試します。

```powershell
python agent\agent_cli.py approve <Issue番号>
```

- [ ] `finish_agent` のログに、文字化けチェック → 静的チェック（弱点8）→
      マージ、の順で進む
- [ ] GitHubでPRがsquash mergeされ、Issueがクローズされる
- [ ] `preview/issue-<N>/` が削除される
- [ ] `worktrees/issue-<N>` が削除される（`git worktree list` で確認）

段階0が全部通ってから、段階1へ進んでください。

---

## 段階1: ①Teams投稿 → Issue作成 だけ

常駐は `issue_agent.py` だけ起動します。

```powershell
python agent\issue_agent.py
```

Teamsの依頼チャネルへシナリオAの文面を投稿します。

- [ ] OneDriveの `request/` に一瞬JSONが作られ、すぐ `request/done/` へ移動する
- [ ] `issue_agent` のログに「Issue作成」の行が出る
- [ ] GitHub上に新しいIssueが実際に作成される
      （タイトル・本文・受入条件チェックリストの形式を確認）
- [ ] Teamsの依頼スレッドに📝作成通知の**返信**が来る（④フロー経由）
- [ ] 同じ投稿をもう一度Power Automateから手動実行しても、Issueが二重に
      作られない（`state/processed/` の重複防止マーカーを確認）
- [ ] `python agent\agent_cli.py status` で対象Issueが `created` になっている

---

## 段階2: 質問と回答の往復（②③④）

`orchestrator.py` も起動し、シナリオBの文面を投稿します。

```powershell
python agent\orchestrator.py --parallel 1 --manual
```

- [ ] `orchestrator` のログで `created` → `queued` → `implementing` と進む
- [ ] Clineが `.agent-question.json` を作る
- [ ] `question/` にJSONが出力され、Teams通知チャネルに選択式カードが出る
- [ ] **3件目の選択肢が無い質問で、カードの3件目が空欄のまま出ないか確認**
      （`docs/03_power_automate.md` 3章で触れた既知の注意点）
- [ ] カードで回答を選ぶ → `decision/` にJSONが作られる
- [ ] コンソールログに `questionId` 不一致の警告が出ていないこと
      （出ていたら弱点2のロジックが意図せず発火している可能性があるので、
      `question/` と `decision/` に残っているJSONの `questionId` を見比べる）
- [ ] 回答がClineへ渡り、実装が再開して `.agent-summary.json` が作られる

---

## 段階3: フル1周（承認まで）

`approval_agent.py` も起動し、3常駐すべてを動かします。

```powershell
powershell -ExecutionPolicy Bypass -File .\scripts\start_all.ps1 -Parallel 1 -Manual
```

シナリオAをTeamsへ投稿し、承認まで通します。

- [ ] `waiting/` にJSONが出力され、Teams通知チャネルに承認カードが出る
- [ ] 「未実施の確認」欄の有無を確認する。**空のまま届いた場合、
      `.agent-summary.json` が作られずフォールバック進行している可能性がある**
      （弱点4の警告文が出ているか、`logs/issue-<N>.log` を確認）
- [ ] プレビューURLとPRリンクが両方クリックでき、正しいページが開く
- [ ] 「✅ 承認」を押す
- [ ] `approval_agent` のログで振り分け（approve → finish_agent起動）を確認
- [ ] mainへマージされ、Issueが自動クローズされる
- [ ] Teamsに🎉完了通知が届く
- [ ] `preview/issue-<N>/` と `worktrees/issue-<N>` が両方削除される

---

## 段階4: 却下・再実装

シナリオAをもう一度通し、承認カードで「却下」を押します。

- [ ] `finish_agent` の `reject()` が動く
- [ ] **GitHub Issueがcloseされず、openのまま残る**（弱点9の確認）
- [ ] Issueに `needs-rework` ラベルが付く
- [ ] Issueへ却下理由と `agent_cli.py retry` の案内コメントが入る
- [ ] Teamsに❌却下通知が届く
- [ ] `python agent\agent_cli.py retry <Issue番号>` で `created` に戻り、
      `state/done/` から復元されたことがログで分かる
- [ ] 再度orchestratorが拾って実装が始まる

次に、承認カードで「再実装」を選ぶケースも確認します。

- [ ] `.agent-rework.txt` がworktree直下に作られる
- [ ] 同じブランチ・同じworktreeのまま実装が再開する
- [ ] 既存のPRにコミットが追加される（新しいPRが作られていないこと）

---

## 段階5: 並走（2件同時）

この一式の目的そのものです。2つの依頼をほぼ同時にTeamsへ投稿します。

```powershell
powershell -ExecutionPolicy Bypass -File .\scripts\start_all.ps1 -Parallel 2
```

- [ ] 2件とも `state` 上で `implementing` まで同時に進む
      （片方が終わるまでもう片方が `queued` のまま止まらないこと）
- [ ] 2つ目のVS Codeウィンドウが**正しいworktree**で開く
      （誤って1つ目のウィンドウに入らないか。弱点1の確認）
- [ ] Cline貼り付けの瞬間だけ順番待ちになる
      （ログに「GUIロック待機中」が出て、実装自体は両方進んでいること）
- [ ] 2件とも最終的に別々のPR・別々のプレビューURLで承認まで進む

---

## 6. 段階を通して見ておきたいポイント

| 確認項目 | 見る場所 |
| --- | --- |
| 各工程フォルダにJSONが溜まっていないか | `request/` `question/` `decision/` `waiting/` `approval/` `reply/`（`done/`以外） |
| ロックが残っていないか | `python agent\agent_cli.py unlock`（起動前後で実行して差分を見る） |
| 文字化けしていないか | 生成されたHTMLをブラウザで直接開く（日本語部分を目視） |
| ログの異常終了 | `logs/issue-<N>.log` に `[ERROR]` が無いか |

## 7. うまくいかなかったときの記録の仕方

次の形式でまとめてもらえると、リモートでも原因を調査しやすくなります。

```
■ 起きたこと
（期待した動きと、実際の動きの差）

■ どの段階・どのシナリオか
（例: 段階2, シナリオB）

■ ログ（logs/issue-<N>.log の該当部分）


■ state/issue-<N>.json の中身


■ 該当フォルダに残っているJSON（あれば、ファイル名とdoneに移動したか）

```

このテンプレへの記入を貼ってもらえれば、コード側の問題として調査・修正します。
