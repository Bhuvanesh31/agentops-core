"""Tests for the post-capture git reconcile step in capture/claude_code/cli.py."""

from unittest.mock import MagicMock, patch

from capture.claude_code.cli import main


def _mock_report():
    r = MagicMock()
    r.render.return_value = ""
    return r


@patch("capture.claude_code.cli.process")
@patch("capture.git.reconcile.reconcile_runs")
def test_cli_git_step_skipped_on_dry_run(mock_reconcile, mock_process, tmp_path):
    mock_process.return_value = _mock_report()
    rc = main(["--dry-run", "--projects-dir", str(tmp_path)])
    assert rc == 0
    mock_reconcile.assert_not_called()


@patch("capture.claude_code.cli.process")
@patch("capture.git.reconcile.reconcile_runs", side_effect=RuntimeError("db down"))
@patch("psycopg.connect")
def test_cli_git_step_non_fatal(mock_connect, mock_reconcile, mock_process, tmp_path, monkeypatch):
    mock_process.return_value = _mock_report()
    mock_connect.return_value = MagicMock()  # MagicMock supports __enter__/__exit__
    monkeypatch.setenv("DATABASE_URL", "postgresql://fake/db")
    rc = main(["--projects-dir", str(tmp_path)])
    assert rc == 0
