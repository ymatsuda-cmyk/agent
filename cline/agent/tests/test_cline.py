from __future__ import annotations

import pytest

from agent_core.cline import _window_title_pattern


@pytest.mark.parametrize(
    "issue_number,title,expected",
    [
        (1, "issue-1 - Visual Studio Code", True),
        (1, "issue-12 - Visual Studio Code", False),
        (1, "issue-123 - Visual Studio Code", False),
        (12, "issue-12 - Visual Studio Code", True),
        (12, "issue-123 - Visual Studio Code", False),
        (12, "issue-2 - Visual Studio Code", False),
        (123, "issue-123 - Visual Studio Code", True),
        (12, "● index.html - issue-12 - Visual Studio Code", True),
        (1, "issue-10 - Visual Studio Code", False),
    ],
)
def test_window_title_pattern_boundary_matching(issue_number, title, expected):
    pattern = _window_title_pattern(issue_number)
    assert bool(pattern.search(title)) is expected
