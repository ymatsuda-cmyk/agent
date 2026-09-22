"""
テスト隔離そのものを検証する診断テスト。

test_statefile.py 等で「前のテストで作ったIssueが見えてしまう」現象が
Windows + Python 3.14 の環境で報告された。この環境（Linux）では再現しない。

このファイルの2つのテストは、意図的に同じ issue 番号(1)を使って
状態を作り、片方だけ確認する。もし本当にテスト間の漏れがあるなら、
2つ目のテストの実行前に conftest.py の agent_env フィクスチャが持つ
assert（隔離チェック）が発火し、原因がフィクスチャ側にあることが
確定できる。逆にここが通ってしまい、他のファイルの組み合わせでだけ
再現するなら、原因はテストの実行順序やファイル間の何かに絞り込める。

失敗した場合は、表示されたエラーメッセージ全文をそのまま報告してほしい。
"""

from __future__ import annotations

from agent_core import statefile


def test_isolation_diagnostic_first(agent_env):
    """1つ目: issue-1 を作って、意図的に他のstatusへ更新する。"""
    statefile.create(1, {"issueTitle": "diagnostic-first"})
    statefile.update(1, {"status": statefile.Status.APPROVED})

    assert statefile.load(1)["status"] == statefile.Status.APPROVED


def test_isolation_diagnostic_second(agent_env):
    """
    2つ目: 同じ issue-1 を新規作成する。

    もし1つ目のテストの内容が漏れているなら、ここで作られる state は
    本来 "created" のはずが、1つ目が残した "approved" のまま
    上書きされずに見えてしまう（statefile.create は既存stateがあれば
    新規のcreatedにはならず、既存へ差分反映するだけのため）。
    """
    state = statefile.create(1, {"issueTitle": "diagnostic-second"})

    assert state["status"] == statefile.Status.CREATED, (
        "テスト間でstateが漏れています。1つ目のテストが作った "
        f"issue-1 の内容がまだ残っています: {state}\n"
        "agent_envフィクスチャのassertで検知されなかったということは、"
        "CONFIG.state_dir自体は正しく空だったが、statefile.create()が"
        "何か別の場所（キャッシュ等）を見ている可能性があります。"
    )
