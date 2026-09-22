from __future__ import annotations

from agent_core import statefile


def test_create_sets_initial_status_and_history(agent_env):
    state = statefile.create(1, {"issueTitle": "テストIssue"})

    assert state["status"] == statefile.Status.CREATED
    assert state["issueTitle"] == "テストIssue"
    assert len(state["history"]) == 1
    assert state["history"][0]["status"] == statefile.Status.CREATED


def test_update_appends_history_only_on_status_change(agent_env):
    statefile.create(1, {"issueTitle": "t"})
    statefile.update(1, {"status": statefile.Status.QUEUED})
    statefile.update(1, {"note": "変更なし"})  # statusは変えない
    statefile.update(1, {"status": statefile.Status.IMPLEMENTING})

    state = statefile.load(1)
    statuses = [entry["status"] for entry in state["history"]]
    assert statuses == [
        statefile.Status.CREATED,
        statefile.Status.QUEUED,
        statefile.Status.IMPLEMENTING,
    ]
    assert state["note"] == "変更なし"


def test_load_missing_returns_empty_dict(agent_env):
    assert statefile.load(999) == {}


def test_status_of_and_branch_of_and_message_id_of(agent_env):
    statefile.create(2, {"branch": "feature/issue-2-x", "messageId": "msg-1"})
    assert statefile.status_of(2) == statefile.Status.CREATED
    assert statefile.branch_of(2) == "feature/issue-2-x"
    assert statefile.message_id_of(2) == "msg-1"


def test_iter_states_orders_by_issue_number(agent_env):
    statefile.create(30, {})
    statefile.create(5, {})
    statefile.create(12, {})

    numbers = [number for number, _ in statefile.iter_states()]
    assert numbers == [5, 12, 30]


def test_active_issue_numbers_filters_running_statuses(agent_env):
    statefile.create(1, {"status": statefile.Status.CREATED})
    statefile.create(2, {"status": statefile.Status.IMPLEMENTING})
    statefile.create(3, {"status": statefile.Status.WAITING_APPROVAL})
    statefile.create(4, {"status": statefile.Status.COMPLETED})

    active = statefile.active_issue_numbers()
    assert active == {2}


def test_archive_moves_state_to_done_folder(agent_env):
    statefile.create(7, {"status": statefile.Status.COMPLETED})
    statefile.archive(7)

    assert not agent_env.state_path(7).exists()
    assert (agent_env.state_dir / "done" / "issue-7.json").exists()
    assert statefile.load(7) == {}


def test_restore_from_archive_round_trip(agent_env):
    statefile.create(8, {"issueTitle": "却下されたIssue"})
    statefile.update(8, {"status": statefile.Status.REJECTED})
    statefile.archive(8)

    assert statefile.load(8) == {}

    restored = statefile.restore_from_archive(8)
    assert restored is True
    assert statefile.load(8)["issueTitle"] == "却下されたIssue"
    assert agent_env.state_path(8).exists()


def test_restore_from_archive_returns_false_when_nothing_archived(agent_env):
    assert statefile.restore_from_archive(12345) is False


def test_terminal_statuses_and_dispatchable_statuses_are_disjoint():
    assert statefile.TERMINAL_STATUSES.isdisjoint(statefile.DISPATCHABLE_STATUSES)


def test_archive_flow_files_moves_question_decision_waiting_approval(agent_env):
    from agent_core.jsonio import write_json

    for folder_name in ("question", "decision", "waiting", "approval"):
        directory = getattr(agent_env, f"{folder_name}_dir")
        write_json(directory / "issue-9.json", {"issueNumber": 9})

    statefile.archive_flow_files(9)

    for folder_name in ("question", "decision", "waiting", "approval"):
        directory = getattr(agent_env, f"{folder_name}_dir")
        assert not (directory / "issue-9.json").exists()
        assert (directory / "done" / "issue-9.json").exists()
