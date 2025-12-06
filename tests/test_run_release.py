import builtins
import contextlib
import io
import tarfile
from pathlib import Path
from typing import cast
from unittest.mock import MagicMock, patch

import paramiko
import pytest

import run_release
from release import ReleaseShelf, Tag


# =============================================================================
# Tests for SSH helper functions
# =============================================================================


class TestSSHHelpers:
    """Tests for SSH helper functions (ssh_client, ssh_exec, ssh_exec_or_raise)."""

    def test_ssh_client_context_manager_connects_and_closes(self) -> None:
        """Test that ssh_client connects and closes the connection properly."""
        mock_client = MagicMock(spec=paramiko.SSHClient)

        with patch("run_release.paramiko.SSHClient", return_value=mock_client):
            with run_release.ssh_client("server.example.com", "testuser", "/path/to/key") as client:
                assert client is mock_client
                mock_client.load_system_host_keys.assert_called_once()
                mock_client.set_missing_host_key_policy.assert_called_once()
                mock_client.connect.assert_called_once_with(
                    "server.example.com",
                    port=22,
                    username="testuser",
                    key_filename="/path/to/key",
                )

        mock_client.close.assert_called_once()

    def test_ssh_client_closes_on_exception(self) -> None:
        """Test that ssh_client closes connection even when exception occurs."""
        mock_client = MagicMock(spec=paramiko.SSHClient)

        with patch("run_release.paramiko.SSHClient", return_value=mock_client):
            with pytest.raises(ValueError):
                with run_release.ssh_client("server.example.com", "testuser") as client:
                    raise ValueError("Test error")

        mock_client.close.assert_called_once()

    def test_ssh_client_with_no_key(self) -> None:
        """Test that ssh_client works without an SSH key."""
        mock_client = MagicMock(spec=paramiko.SSHClient)

        with patch("run_release.paramiko.SSHClient", return_value=mock_client):
            with run_release.ssh_client("server.example.com", "testuser") as client:
                mock_client.connect.assert_called_once_with(
                    "server.example.com",
                    port=22,
                    username="testuser",
                    key_filename=None,
                )

    def test_ssh_exec_returns_stdout_stderr_exitcode(self) -> None:
        """Test that ssh_exec returns stdout, stderr, and exit code."""
        mock_client = MagicMock(spec=paramiko.SSHClient)
        mock_transport = MagicMock()
        mock_channel = MagicMock()

        mock_client.get_transport.return_value = mock_transport
        mock_transport.open_session.return_value = mock_channel
        mock_channel.recv_exit_status.return_value = 0
        mock_channel.recv.return_value = b"output text"
        mock_channel.recv_stderr.return_value = b""

        stdout, stderr, exit_code = run_release.ssh_exec(mock_client, "ls -la")

        mock_channel.exec_command.assert_called_once_with("ls -la")
        assert stdout == "output text"
        assert stderr == ""
        assert exit_code == 0

    def test_ssh_exec_with_error(self) -> None:
        """Test that ssh_exec returns stderr and non-zero exit code on error."""
        mock_client = MagicMock(spec=paramiko.SSHClient)
        mock_transport = MagicMock()
        mock_channel = MagicMock()

        mock_client.get_transport.return_value = mock_transport
        mock_transport.open_session.return_value = mock_channel
        mock_channel.recv_exit_status.return_value = 1
        mock_channel.recv.return_value = b""
        mock_channel.recv_stderr.return_value = b"command not found"

        stdout, stderr, exit_code = run_release.ssh_exec(mock_client, "nonexistent")

        assert stdout == ""
        assert stderr == "command not found"
        assert exit_code == 1

    def test_ssh_exec_or_raise_success(self) -> None:
        """Test that ssh_exec_or_raise returns stdout on success."""
        mock_client = MagicMock(spec=paramiko.SSHClient)
        mock_transport = MagicMock()
        mock_channel = MagicMock()

        mock_client.get_transport.return_value = mock_transport
        mock_transport.open_session.return_value = mock_channel
        mock_channel.recv_exit_status.return_value = 0
        mock_channel.recv.return_value = b"success output"
        mock_channel.recv_stderr.return_value = b""

        result = run_release.ssh_exec_or_raise(mock_client, "pwd")

        assert result == "success output"

    def test_ssh_exec_or_raise_raises_on_failure(self) -> None:
        """Test that ssh_exec_or_raise raises ReleaseException on failure."""
        mock_client = MagicMock(spec=paramiko.SSHClient)
        mock_transport = MagicMock()
        mock_channel = MagicMock()

        mock_client.get_transport.return_value = mock_transport
        mock_transport.open_session.return_value = mock_channel
        mock_channel.recv_exit_status.return_value = 1
        mock_channel.recv.return_value = b""
        mock_channel.recv_stderr.return_value = b"permission denied"

        with pytest.raises(run_release.ReleaseException) as exc_info:
            run_release.ssh_exec_or_raise(mock_client, "sudo rm -rf /")

        assert "permission denied" in str(exc_info.value)
        assert "sudo rm -rf /" in str(exc_info.value)


# =============================================================================
# Tests for ask_question
# =============================================================================


class TestAskQuestion:
    """Tests for the ask_question function."""

    def test_ask_question_yes(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Test that ask_question returns True for 'yes' input."""
        inputs = iter(["yes"])
        monkeypatch.setattr("builtins.input", lambda _: next(inputs))

        result = run_release.ask_question("Continue?")
        assert result is True

    def test_ask_question_no(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Test that ask_question returns False for 'no' input."""
        inputs = iter(["no"])
        monkeypatch.setattr("builtins.input", lambda _: next(inputs))

        result = run_release.ask_question("Continue?")
        assert result is False

    def test_ask_question_retries_on_invalid_input(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Test that ask_question retries on invalid input."""
        inputs = iter(["maybe", "invalid", "yes"])
        monkeypatch.setattr("builtins.input", lambda _: next(inputs))

        result = run_release.ask_question("Continue?")
        assert result is True


# =============================================================================
# Tests for ReleaseDriver
# =============================================================================


class TestReleaseDriver:
    """Tests for ReleaseDriver class."""

    def test_release_driver_preserves_exception_context(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Test that ReleaseDriver preserves exception context when task fails."""
        # GIVEN
        def failing_task(db: ReleaseShelf) -> None:
            raise ValueError("Original error")

        monkeypatch.setattr("run_release.Path.home", lambda: tmp_path)
        task = run_release.Task(failing_task, "Failing task")
        tag = Tag("3.14.0a1")
        driver = run_release.ReleaseDriver(
            tasks=[task],
            release_tag=tag,
            git_repo=str(tmp_path),
            api_key="user:key",
            ssh_user="testuser",
            sign_gpg=False,
        )

        # WHEN
        with pytest.raises(run_release.ReleaseException) as exc_info:
            driver.run()

        # Then
        assert "Failing task" in str(exc_info.value)
        assert exc_info.value.__cause__ is not None
        assert "Original error" in str(exc_info.value.__cause__)

    def test_release_driver_executes_tasks_in_order(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Test that tasks are executed in the exact order provided."""
        # GIVEN
        monkeypatch.setattr("run_release.Path.home", lambda: tmp_path)
        execution_order: list[str] = []

        def make_task(name: str):
            def task_func(db: ReleaseShelf) -> None:
                execution_order.append(name)
            return run_release.Task(task_func, f"Task {name}")

        tasks = [make_task("A"), make_task("B"), make_task("C")]
        tag = Tag("3.14.0a1")
        driver = run_release.ReleaseDriver(
            tasks=tasks,
            release_tag=tag,
            git_repo=str(tmp_path),
            api_key="user:key",
            ssh_user="testuser",
            sign_gpg=False,
        )

        # WHEN
        driver.run()

        # Then
        assert execution_order == ["A", "B", "C"]

    def test_release_driver_resumes_from_checkpoint(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Test that driver resumes from last checkpoint after failure."""
        # GIVEN
        monkeypatch.setattr("run_release.Path.home", lambda: tmp_path)
        execution_count = {"A": 0, "B": 0, "C": 0}

        def make_counting_task(name: str):
            def task_func(db: ReleaseShelf) -> None:
                execution_count[name] += 1
                if name == "B" and execution_count["B"] == 1:
                    raise ValueError(f"Task {name} failed on first attempt")
            return run_release.Task(task_func, f"Task {name}")

        tasks = [make_counting_task("A"), make_counting_task("B"), make_counting_task("C")]
        tag = Tag("3.14.0a1")

        # WHEN - first attempt fails at task B
        driver = run_release.ReleaseDriver(
            tasks=tasks,
            release_tag=tag,
            git_repo=str(tmp_path),
            api_key="user:key",
            ssh_user="testuser",
            sign_gpg=False,
        )
        with pytest.raises(run_release.ReleaseException):
            driver.run()

        # Then - task A ran once, B failed, C never ran
        assert execution_count["A"] == 1
        assert execution_count["B"] == 1
        assert execution_count["C"] == 0

        # WHEN - second attempt resumes from task B
        driver2 = run_release.ReleaseDriver(
            tasks=tasks,
            release_tag=tag,
            git_repo=str(tmp_path),
            api_key="user:key",
            ssh_user="testuser",
            sign_gpg=False,
        )
        driver2.run()

        # Then - task A not re-executed, B ran again, C ran first time
        assert execution_count["A"] == 1
        assert execution_count["B"] == 2
        assert execution_count["C"] == 1

    def test_release_driver_checkpoints_before_each_task(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Test that checkpoint is called before executing each task."""
        # GIVEN
        monkeypatch.setattr("run_release.Path.home", lambda: tmp_path)
        checkpoint_calls: list[tuple[str, list[str]]] = []

        def make_task(name: str):
            def task_func(db: ReleaseShelf) -> None:
                checkpoint_calls.append((name, list(db.get("completed_tasks", []))))
            return run_release.Task(task_func, f"Task {name}")

        tasks = [make_task("A"), make_task("B")]
        tag = Tag("3.14.0a1")
        driver = run_release.ReleaseDriver(
            tasks=tasks,
            release_tag=tag,
            git_repo=str(tmp_path),
            api_key="user:key",
            ssh_user="testuser",
            sign_gpg=False,
        )

        # WHEN
        driver.run()

        # Then
        assert len(checkpoint_calls) == 2
        assert checkpoint_calls[0][1] == []
        assert len(checkpoint_calls[1][1]) == 1

    def test_release_driver_tracks_completed_tasks(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Test that completed_tasks list accurately tracks finished tasks."""
        # GIVEN
        monkeypatch.setattr("run_release.Path.home", lambda: tmp_path)

        def simple_task(db: ReleaseShelf) -> None:
            pass

        tasks = [
            run_release.Task(simple_task, "Task 1"),
            run_release.Task(simple_task, "Task 2"),
            run_release.Task(simple_task, "Task 3"),
        ]
        tag = Tag("3.14.0a1")
        driver = run_release.ReleaseDriver(
            tasks=tasks,
            release_tag=tag,
            git_repo=str(tmp_path),
            api_key="user:key",
            ssh_user="testuser",
            sign_gpg=False,
        )

        # WHEN
        driver.run()

        # Then
        assert len(driver.completed_task_descriptions) == 3
        assert driver.completed_task_descriptions == ["Task 1", "Task 2", "Task 3"]

    def test_release_driver_sets_finished_flag(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Test that finished flag is set to True when all tasks complete."""
        # GIVEN
        monkeypatch.setattr("run_release.Path.home", lambda: tmp_path)

        def simple_task(db: ReleaseShelf) -> None:
            pass

        tasks = [run_release.Task(simple_task, "Task 1")]
        tag = Tag("3.14.0a1")
        driver = run_release.ReleaseDriver(
            tasks=tasks,
            release_tag=tag,
            git_repo=str(tmp_path),
            api_key="user:key",
            ssh_user="testuser",
            sign_gpg=False,
        )
        assert driver.db.get("finished") is False

        # WHEN
        driver.run()

        # Then
        assert driver.db.get("finished") is True

    def test_release_driver_with_no_tasks(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Test that driver handles empty task list gracefully."""
        # GIVEN
        monkeypatch.setattr("run_release.Path.home", lambda: tmp_path)
        tag = Tag("3.14.0a1")
        driver = run_release.ReleaseDriver(
            tasks=[],
            release_tag=tag,
            git_repo=str(tmp_path),
            api_key="user:key",
            ssh_user="testuser",
            sign_gpg=False,
        )

        # WHEN
        driver.run()

        # Then
        assert driver.db.get("finished") is True
        assert len(driver.completed_task_descriptions) == 0

    def test_release_driver_handles_task_failure_mid_sequence(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Test that failure in middle of task sequence stops execution."""
        # GIVEN
        monkeypatch.setattr("run_release.Path.home", lambda: tmp_path)
        executed: list[str] = []

        def make_task(name: str, should_fail: bool = False):
            def task_func(db: ReleaseShelf) -> None:
                executed.append(name)
                if should_fail:
                    raise ValueError(f"{name} failed")
            return run_release.Task(task_func, f"Task {name}")

        tasks = [
            make_task("A"),
            make_task("B"),
            make_task("C", should_fail=True),
            make_task("D"),
            make_task("E"),
        ]
        tag = Tag("3.14.0a1")
        driver = run_release.ReleaseDriver(
            tasks=tasks,
            release_tag=tag,
            git_repo=str(tmp_path),
            api_key="user:key",
            ssh_user="testuser",
            sign_gpg=False,
        )

        # WHEN
        with pytest.raises(run_release.ReleaseException):
            driver.run()

        # Then
        assert executed == ["A", "B", "C"]
        assert len(driver.completed_task_descriptions) == 2

    def test_release_driver_initializes_db_with_constructor_args(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Test that database is initialized with values from constructor."""
        # GIVEN
        monkeypatch.setattr("run_release.Path.home", lambda: tmp_path)
        tag = Tag("3.14.0a1")
        git_repo_path = "/path/to/cpython"

        # WHEN
        driver = run_release.ReleaseDriver(
            tasks=[],
            release_tag=tag,
            git_repo=git_repo_path,
            api_key="myuser:mykey",
            ssh_user="releasemanager",
            ssh_key="/path/to/ssh/key",
            sign_gpg=True,
        )

        # Then
        assert str(driver.db["git_repo"]) == git_repo_path
        assert driver.db["auth_info"] == "myuser:mykey"
        assert driver.db["ssh_user"] == "releasemanager"
        assert driver.db["ssh_key"] == "/path/to/ssh/key"
        assert driver.db["sign_gpg"] is True
        assert driver.db["release"].normalized() == tag.normalized()

    def test_release_driver_preserves_existing_db_values_on_resume(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Test that existing DB values are preserved when resuming."""
        # GIVEN
        monkeypatch.setattr("run_release.Path.home", lambda: tmp_path)
        tag = Tag("3.14.0a1")
        run_release.ReleaseDriver(
            tasks=[],
            release_tag=tag,
            git_repo="/original/path",
            api_key="original:key",
            ssh_user="original_user",
            sign_gpg=True,
        )

        # WHEN
        driver2 = run_release.ReleaseDriver(
            tasks=[],
            release_tag=tag,
            git_repo="/different/path",
            api_key="different:key",
            ssh_user="different_user",
            sign_gpg=False,
        )

        # Then
        assert str(driver2.db["git_repo"]) == "/original/path"
        assert driver2.db["auth_info"] == "original:key"
        assert driver2.db["ssh_user"] == "original_user"
        assert driver2.db["sign_gpg"] is True

    def test_release_driver_resets_db_if_previous_release_finished(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Test that DB is reset if previous release was finished."""
        # GIVEN
        monkeypatch.setattr("run_release.Path.home", lambda: tmp_path)
        tag = Tag("3.14.0a1")
        driver1 = run_release.ReleaseDriver(
            tasks=[],
            release_tag=tag,
            git_repo="/path/to/repo",
            api_key="user:key",
            ssh_user="testuser",
            sign_gpg=False,
        )
        driver1.run()
        assert driver1.db["finished"] is True

        # WHEN
        tag2 = Tag("3.14.0a2")
        driver2 = run_release.ReleaseDriver(
            tasks=[],
            release_tag=tag2,
            git_repo="/path/to/repo",
            api_key="user:key",
            ssh_user="testuser",
            sign_gpg=False,
        )

        # Then
        assert driver2.db.get("finished", False) is False
        assert str(driver2.db["release"]) == "3.14.0a2"

    def test_release_driver_handles_all_tasks_already_completed(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Test behavior when resuming and all tasks are already completed."""
        # GIVEN
        monkeypatch.setattr("run_release.Path.home", lambda: tmp_path)

        def simple_task(db: ReleaseShelf) -> None:
            pass

        tasks = [
            run_release.Task(simple_task, "Task 1"),
            run_release.Task(simple_task, "Task 2"),
        ]
        tag = Tag("3.14.0a1")
        driver1 = run_release.ReleaseDriver(
            tasks=tasks,
            release_tag=tag,
            git_repo=str(tmp_path),
            api_key="user:key",
            ssh_user="testuser",
            sign_gpg=False,
        )
        driver1.run()
        driver1.db["finished"] = False

        # WHEN
        driver2 = run_release.ReleaseDriver(
            tasks=tasks,
            release_tag=tag,
            git_repo=str(tmp_path),
            api_key="user:key",
            ssh_user="testuser",
            sign_gpg=False,
        )
        driver2.run()

        # Then
        assert driver2.db["finished"] is True


# =============================================================================
# Tests for check_ssh_connection
# =============================================================================


class TestCheckSSHConnection:
    """Tests for check_ssh_connection function."""

    def test_check_ssh_connection_success(self) -> None:
        """Test that check_ssh_connection succeeds with valid connections."""
        mock_client = MagicMock(spec=paramiko.SSHClient)
        mock_transport = MagicMock()
        mock_channel = MagicMock()

        mock_client.get_transport.return_value = mock_transport
        mock_transport.open_session.return_value = mock_channel
        mock_channel.recv_exit_status.return_value = 0
        mock_channel.recv.return_value = b"/home/user"
        mock_channel.recv_stderr.return_value = b""

        db = cast(ReleaseShelf, {
            "ssh_user": "testuser",
            "ssh_key": "/path/to/key",
        })

        with patch("run_release.paramiko.SSHClient", return_value=mock_client):
            run_release.check_ssh_connection(db)

        # Should be called twice (once for DOWNLOADS_SERVER, once for DOCS_SERVER)
        assert mock_client.connect.call_count == 2


# =============================================================================
# Tests for check_sigstore_client
# =============================================================================


class TestCheckSigstoreClient:
    """Tests for check_sigstore_client function."""

    def test_check_sigstore_client_success(self) -> None:
        """Test that check_sigstore_client succeeds with valid version."""
        mock_client = MagicMock(spec=paramiko.SSHClient)
        mock_transport = MagicMock()
        mock_channel = MagicMock()

        mock_client.get_transport.return_value = mock_transport
        mock_transport.open_session.return_value = mock_channel
        mock_channel.recv_exit_status.return_value = 0
        mock_channel.recv.return_value = b"sigstore 3.6.6"
        mock_channel.recv_stderr.return_value = b""

        db = cast(ReleaseShelf, {
            "ssh_user": "testuser",
            "ssh_key": "/path/to/key",
        })

        with patch("run_release.paramiko.SSHClient", return_value=mock_client):
            run_release.check_sigstore_client(db)

    def test_check_sigstore_client_invalid_version(self) -> None:
        """Test that check_sigstore_client fails with invalid version."""
        mock_client = MagicMock(spec=paramiko.SSHClient)
        mock_transport = MagicMock()
        mock_channel = MagicMock()

        mock_client.get_transport.return_value = mock_transport
        mock_transport.open_session.return_value = mock_channel
        mock_channel.recv_exit_status.return_value = 0
        mock_channel.recv.return_value = b"sigstore 4.0.0"
        mock_channel.recv_stderr.return_value = b""

        db = cast(ReleaseShelf, {
            "ssh_user": "testuser",
            "ssh_key": "/path/to/key",
        })

        with patch("run_release.paramiko.SSHClient", return_value=mock_client):
            with pytest.raises(run_release.ReleaseException) as exc_info:
                run_release.check_sigstore_client(db)

        assert "Sigstore version not detected or not valid" in str(exc_info.value)


# =============================================================================
# Tests for ReleaseState (JSON-based state management)
# =============================================================================


class TestReleaseState:
    """Tests for the ReleaseState dataclass."""

    def test_default_state(self) -> None:
        """Test that default state has expected values."""
        state = run_release.ReleaseState()
        assert state.finished is False
        assert state.completed_tasks == []
        assert state.gpg_key is None
        assert state.git_repo is None
        assert state.auth_info is None
        assert state.ssh_user is None
        assert state.ssh_key is None
        assert state.sign_gpg is True
        assert state.security_release is False
        assert state.release_tag is None

    def test_save_and_load(self, tmp_path: Path) -> None:
        """Test that state can be saved and loaded from JSON."""
        state_file = tmp_path / "release_state.json"

        # Create and save state
        state = run_release.ReleaseState(
            finished=False,
            completed_tasks=["Task 1", "Task 2"],
            gpg_key="ABC123",
            git_repo="/path/to/repo",
            auth_info="user:key",
            ssh_user="testuser",
            ssh_key="/path/to/key",
            sign_gpg=True,
            security_release=False,
            release_tag="3.14.0a1",
        )
        state.save(state_file)

        # Load and verify
        loaded = run_release.ReleaseState.load(state_file)
        assert loaded.finished is False
        assert loaded.completed_tasks == ["Task 1", "Task 2"]
        assert loaded.gpg_key == "ABC123"
        assert loaded.git_repo == "/path/to/repo"
        assert loaded.auth_info == "user:key"
        assert loaded.ssh_user == "testuser"
        assert loaded.ssh_key == "/path/to/key"
        assert loaded.sign_gpg is True
        assert loaded.security_release is False
        assert loaded.release_tag == "3.14.0a1"

    def test_load_nonexistent_file(self, tmp_path: Path) -> None:
        """Test that loading nonexistent file returns default state."""
        state_file = tmp_path / "nonexistent.json"
        state = run_release.ReleaseState.load(state_file)
        assert state.finished is False
        assert state.completed_tasks == []

    def test_load_corrupted_file(self, tmp_path: Path) -> None:
        """Test that loading corrupted file returns default state."""
        state_file = tmp_path / "corrupted.json"
        state_file.write_text("not valid json {{{")
        state = run_release.ReleaseState.load(state_file)
        assert state.finished is False

    def test_clear_removes_file(self, tmp_path: Path) -> None:
        """Test that clear removes the state file."""
        state_file = tmp_path / "release_state.json"
        state = run_release.ReleaseState(finished=True)
        state.save(state_file)
        assert state_file.exists()

        state.clear(state_file)
        assert not state_file.exists()

    def test_clear_nonexistent_file(self, tmp_path: Path) -> None:
        """Test that clear on nonexistent file doesn't raise."""
        state_file = tmp_path / "nonexistent.json"
        state = run_release.ReleaseState()
        state.clear(state_file)  # Should not raise


# =============================================================================
# Tests for with_retry decorator
# =============================================================================


class TestWithRetry:
    """Tests for the with_retry decorator."""

    def test_succeeds_on_first_attempt(self) -> None:
        """Test that function succeeds without retry."""
        call_count = 0

        @run_release.with_retry(max_attempts=3, delay=0.01)
        def success_func() -> str:
            nonlocal call_count
            call_count += 1
            return "success"

        result = success_func()
        assert result == "success"
        assert call_count == 1

    def test_retries_on_failure_then_succeeds(self) -> None:
        """Test that function retries on failure and eventually succeeds."""
        call_count = 0

        @run_release.with_retry(max_attempts=3, delay=0.01, exceptions=(ValueError,))
        def intermittent_func() -> str:
            nonlocal call_count
            call_count += 1
            if call_count < 3:
                raise ValueError("Temporary failure")
            return "success"

        result = intermittent_func()
        assert result == "success"
        assert call_count == 3

    def test_raises_after_max_attempts(self) -> None:
        """Test that function raises after max attempts exceeded."""
        call_count = 0

        @run_release.with_retry(max_attempts=3, delay=0.01, exceptions=(ValueError,))
        def always_fails() -> str:
            nonlocal call_count
            call_count += 1
            raise ValueError("Permanent failure")

        with pytest.raises(ValueError, match="Permanent failure"):
            always_fails()
        assert call_count == 3

    def test_does_not_retry_unexpected_exceptions(self) -> None:
        """Test that unexpected exceptions are not retried."""
        call_count = 0

        @run_release.with_retry(max_attempts=3, delay=0.01, exceptions=(ValueError,))
        def raises_type_error() -> str:
            nonlocal call_count
            call_count += 1
            raise TypeError("Unexpected error")

        with pytest.raises(TypeError, match="Unexpected error"):
            raises_type_error()
        assert call_count == 1

    def test_exponential_backoff(self) -> None:
        """Test that delay increases exponentially."""
        import time

        start_times: list[float] = []

        @run_release.with_retry(max_attempts=3, delay=0.05, backoff=2.0, exceptions=(ValueError,))
        def timed_fails() -> str:
            start_times.append(time.time())
            raise ValueError("Fail")

        with pytest.raises(ValueError):
            timed_fails()

        assert len(start_times) == 3
        # First retry should be after ~0.05s, second after ~0.1s
        first_delay = start_times[1] - start_times[0]
        second_delay = start_times[2] - start_times[1]
        assert first_delay >= 0.04  # Allow some tolerance
        assert second_delay >= 0.08  # Should be ~2x first delay


# =============================================================================
# Tests for check_tool functions
# =============================================================================


class TestCheckTools:
    """Tests for tool availability checking functions."""

    def test_check_tool_found(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Test that check_tool succeeds when tool is found."""
        monkeypatch.setattr("shutil.which", lambda tool: f"/usr/bin/{tool}")
        db = cast(ReleaseShelf, {})
        run_release.check_tool(db, "git")  # Should not raise

    def test_check_tool_not_found(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Test that check_tool raises when tool is not found."""
        monkeypatch.setattr("shutil.which", lambda tool: None)
        db = cast(ReleaseShelf, {})
        with pytest.raises(run_release.ReleaseException, match="nonexistent is not available"):
            run_release.check_tool(db, "nonexistent")

    def test_check_gh(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Test check_gh function."""
        monkeypatch.setattr("shutil.which", lambda tool: f"/usr/bin/{tool}")
        db = cast(ReleaseShelf, {})
        run_release.check_gh(db)  # Should not raise

    def test_check_git(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Test check_git function."""
        monkeypatch.setattr("shutil.which", lambda tool: f"/usr/bin/{tool}")
        db = cast(ReleaseShelf, {})
        run_release.check_git(db)  # Should not raise


# =============================================================================
# Tests for cd context manager
# =============================================================================


class TestCdContextManager:
    """Tests for the cd context manager."""

    def test_cd_changes_and_restores_directory(self, tmp_path: Path) -> None:
        """Test that cd changes directory and restores on exit."""
        import os

        original = os.getcwd()
        subdir = tmp_path / "subdir"
        subdir.mkdir()

        with run_release.cd(subdir):
            assert os.getcwd() == str(subdir)

        assert os.getcwd() == original

    def test_cd_restores_on_exception(self, tmp_path: Path) -> None:
        """Test that cd restores directory even on exception."""
        import os

        original = os.getcwd()
        subdir = tmp_path / "subdir"
        subdir.mkdir()

        with pytest.raises(ValueError):
            with run_release.cd(subdir):
                raise ValueError("Test error")

        assert os.getcwd() == original


# =============================================================================
# Tests for check_cpython_repo_is_clean
# =============================================================================


class TestCheckCPythonRepoIsClean:
    """Tests for check_cpython_repo_is_clean function."""

    def test_clean_repo(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        """Test that clean repo passes."""
        db = cast(ReleaseShelf, {"git_repo": tmp_path})

        def mock_check_output(cmd, cwd=None):
            return b""

        monkeypatch.setattr("subprocess.check_output", mock_check_output)
        run_release.check_cpython_repo_is_clean(db)  # Should not raise

    def test_dirty_repo(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        """Test that dirty repo raises."""
        db = cast(ReleaseShelf, {"git_repo": tmp_path})

        def mock_check_output(cmd, cwd=None):
            return b"M modified_file.py\n"

        monkeypatch.setattr("subprocess.check_output", mock_check_output)
        with pytest.raises(run_release.ReleaseException, match="Git repository is not clean"):
            run_release.check_cpython_repo_is_clean(db)


# =============================================================================
# Test fixtures for release tasks
# =============================================================================


@pytest.fixture
def mock_release_db(tmp_path: Path) -> dict[str, Any]:
    """Create a mock release database for testing tasks."""
    return {
        "release": Tag("3.14.0a1"),
        "git_repo": tmp_path,
        "ssh_user": "testuser",
        "ssh_key": "/path/to/key",
        "auth_info": "user:apikey",
        "sign_gpg": False,
        "security_release": False,
        "gpg_key": None,
    }


@pytest.fixture
def mock_ssh_client() -> MagicMock:
    """Create a mock SSH client for testing SSH operations."""
    mock = MagicMock(spec=paramiko.SSHClient)
    mock_transport = MagicMock()
    mock_channel = MagicMock()

    mock.get_transport.return_value = mock_transport
    mock_transport.open_session.return_value = mock_channel
    mock_channel.recv_exit_status.return_value = 0
    mock_channel.recv.return_value = b""
    mock_channel.recv_stderr.return_value = b""

    return mock


@pytest.fixture
def mock_subprocess(monkeypatch: pytest.MonkeyPatch):
    """Mock subprocess calls for testing."""
    mock_call = MagicMock(return_value=0)
    mock_output = MagicMock(return_value=b"")

    monkeypatch.setattr("subprocess.check_call", mock_call)
    monkeypatch.setattr("subprocess.check_output", mock_output)

    return {"check_call": mock_call, "check_output": mock_output}


# =============================================================================
# Tests for version checking
# =============================================================================


@pytest.mark.parametrize(
    "version",
    ["sigstore 3.6.2", "sigstore 3.6.6"],
)
def test_check_sigstore_version_success(version) -> None:
    # Verify runs with no exceptions
    run_release.check_sigstore_version(version)


@pytest.mark.parametrize(
    "version",
    ["sigstore 3.4.0", "sigstore 3.6.0", "sigstore 4.0.0", ""],
)
def test_check_sigstore_version_exception(version) -> None:
    with pytest.raises(
        run_release.ReleaseException,
        match="Sigstore version not detected or not valid",
    ):
        run_release.check_sigstore_version(version)


@pytest.mark.parametrize(
    ["url", "expected"],
    [
        ("github.com/hugovk/cpython.git", "hugovk"),
        ("git@github.com:hugovk/cpython.git", "hugovk"),
        ("https://github.com/hugovk/cpython.git", "hugovk"),
    ],
)
def test_extract_github_owner(url: str, expected: str) -> None:
    assert run_release.extract_github_owner(url) == expected


def test_invalid_extract_github_owner() -> None:
    with pytest.raises(
        run_release.ReleaseException,
        match="Could not parse GitHub owner from 'origin' remote URL: "
        "https://example.com",
    ):
        run_release.extract_github_owner("https://example.com")


def test_check_magic_number() -> None:
    db = {
        "release": Tag("3.14.0rc1"),
        "git_repo": str(Path(__file__).parent / "magicdata"),
    }
    with pytest.raises(
        run_release.ReleaseException, match="Magic numbers in .* don't match"
    ):
        run_release.check_magic_number(cast(ReleaseShelf, db))


def prepare_fake_docs(tmp_path: Path, content: str) -> None:
    docs_path = tmp_path / "3.13.0rc1/docs"
    docs_path.mkdir(parents=True)
    tarball = tarfile.open(docs_path / "python-3.13.0rc1-docs-html.tar.bz2", "w:bz2")
    with tarball:
        tarinfo = tarfile.TarInfo("index.html")
        tarinfo.size = len(content)
        tarball.addfile(tarinfo, io.BytesIO(content.encode()))


@contextlib.contextmanager
def fake_answers(monkeypatch: pytest.MonkeyPatch, answers: list[str]) -> None:
    """Monkey-patch input() to give the given answers. All must be consumed."""

    answers_left = list(answers)

    def fake_input(question):
        print(question, "--", answers_left[0])
        return answers_left.pop(0)

    with monkeypatch.context() as ctx:
        ctx.setattr(builtins, "input", fake_input)
        yield
    assert answers_left == []


def test_check_doc_unreleased_version_no_file(tmp_path: Path) -> None:
    db = {
        "release": Tag("3.13.0rc1"),
        "git_repo": str(tmp_path),
    }
    with pytest.raises(AssertionError):
        # There should be a docs artefact available
        run_release.check_doc_unreleased_version(cast(ReleaseShelf, db))


def test_check_doc_unreleased_version_no_file_alpha(tmp_path: Path) -> None:
    db = {
        "release": Tag("3.13.0a1"),
        "git_repo": str(tmp_path),
    }
    # No docs artefact needed for alphas
    run_release.check_doc_unreleased_version(cast(ReleaseShelf, db))


def test_check_doc_unreleased_version_ok(tmp_path: Path) -> None:
    prepare_fake_docs(
        tmp_path,
        "<div>New in 3.13</div>",
    )
    db = {
        "release": Tag("3.13.0rc1"),
        "git_repo": str(tmp_path),
    }
    run_release.check_doc_unreleased_version(cast(ReleaseShelf, db))


def test_check_doc_unreleased_version_not_ok(monkeypatch, tmp_path: Path) -> None:
    prepare_fake_docs(
        tmp_path,
        "<div>New in 3.13.0rc1 (unreleased)</div>",
    )
    db = {
        "release": Tag("3.13.0rc1"),
        "git_repo": str(tmp_path),
    }
    with fake_answers(monkeypatch, ["no"]), pytest.raises(AssertionError):
        run_release.check_doc_unreleased_version(cast(ReleaseShelf, db))


def test_check_doc_unreleased_version_waived(monkeypatch, tmp_path: Path) -> None:
    prepare_fake_docs(
        tmp_path,
        "<div>New in 3.13.0rc1 (unreleased)</div>",
    )
    db = {
        "release": Tag("3.13.0rc1"),
        "git_repo": str(tmp_path),
    }
    with fake_answers(monkeypatch, ["yes"]):
        run_release.check_doc_unreleased_version(cast(ReleaseShelf, db))


def test_update_whatsnew_toctree(tmp_path: Path) -> None:
    # GIVEN
    # Only first beta triggers update
    db = {"release": Tag("3.14.0b1")}

    original_toctree_file = Path(__file__).parent / "whatsnew_index.rst"
    toctree__file = tmp_path / "patchlevel.h"
    toctree__file.write_text(original_toctree_file.read_text())

    # WHEN
    run_release.update_whatsnew_toctree(cast(ReleaseShelf, db), str(toctree__file))

    # THEN
    new_contents = toctree__file.read_text()
    assert "   3.15.rst\n   3.14.rst\n" in new_contents


# =============================================================================
# Tests for upload_files_to_server
# =============================================================================


class TestUploadFilesToServer:
    """Tests for upload_files_to_server function."""

    def test_upload_to_downloads_server_creates_correct_directories(
        self, tmp_path: Path
    ) -> None:
        """Test that upload creates the expected directory structure on downloads server."""
        # GIVEN
        release_tag = Tag("3.14.0a1")
        artifacts_path = tmp_path / "3.14.0a1"
        downloads_dir = artifacts_path / "downloads"
        downloads_dir.mkdir(parents=True)
        (downloads_dir / "Python-3.14.0a1.tgz").touch()
        (downloads_dir / "Python-3.14.0a1.tar.xz").touch()

        db = cast(
            ReleaseShelf,
            {
                "release": release_tag,
                "git_repo": tmp_path,
                "ssh_user": "testuser",
                "ssh_key": "/path/to/key",
            },
        )

        mock_client = MagicMock(spec=paramiko.SSHClient)
        mock_transport = MagicMock()
        mock_sftp = MagicMock(spec=run_release.MySFTPClient)

        mock_client.get_transport.return_value = mock_transport
        mock_transport.open_session.return_value = MagicMock()

        # Mock SFTP operations
        with patch("run_release.MySFTPClient.from_transport", return_value=mock_sftp), \
             patch("run_release.ssh_client") as mock_ssh_ctx, \
             patch("shutil.rmtree"):
            mock_ssh_ctx.return_value.__enter__.return_value = mock_client

            # WHEN
            run_release.upload_files_to_server(db, run_release.DOWNLOADS_SERVER)

        # THEN - verify correct destination path was created
        expected_dest = Path(f"/home/psf-users/testuser/{release_tag}")
        mock_sftp.mkdir.assert_any_call(str(expected_dest))
        mock_sftp.mkdir.assert_any_call(str(expected_dest / "downloads"))

    def test_upload_to_docs_server_only_uploads_docs(self, tmp_path: Path) -> None:
        """Test that uploading to docs server only uploads docs subdirectory."""
        # GIVEN
        release_tag = Tag("3.14.0a1")
        artifacts_path = tmp_path / "3.14.0a1"
        docs_dir = artifacts_path / "docs"
        downloads_dir = artifacts_path / "downloads"
        docs_dir.mkdir(parents=True)
        downloads_dir.mkdir(parents=True)
        (docs_dir / "python-3.14.0a1-docs-html.zip").touch()
        (downloads_dir / "Python-3.14.0a1.tgz").touch()

        db = cast(
            ReleaseShelf,
            {
                "release": release_tag,
                "git_repo": tmp_path,
                "ssh_user": "testuser",
                "ssh_key": "/path/to/key",
            },
        )

        mock_client = MagicMock(spec=paramiko.SSHClient)
        mock_transport = MagicMock()
        mock_sftp = MagicMock(spec=run_release.MySFTPClient)

        mock_client.get_transport.return_value = mock_transport

        with patch("run_release.MySFTPClient.from_transport", return_value=mock_sftp), \
             patch("run_release.ssh_client") as mock_ssh_ctx, \
             patch("shutil.rmtree"):
            mock_ssh_ctx.return_value.__enter__.return_value = mock_client

            # WHEN
            run_release.upload_files_to_server(db, run_release.DOCS_SERVER)

        # THEN - docs uploaded, downloads NOT uploaded
        expected_dest = Path(f"/home/psf-users/testuser/{release_tag}")
        mock_sftp.put_dir.assert_called_once()
        call_args = mock_sftp.put_dir.call_args
        assert "docs" in str(call_args[0][0])  # source contains 'docs'
        assert "downloads" not in str(call_args[0][0])  # source does NOT contain 'downloads'

    def test_upload_cleans_up_existing_destination(self, tmp_path: Path) -> None:
        """Test that upload removes existing files at destination before uploading."""
        # GIVEN
        release_tag = Tag("3.14.0a1")
        artifacts_path = tmp_path / "3.14.0a1"
        downloads_dir = artifacts_path / "downloads"
        downloads_dir.mkdir(parents=True)
        (downloads_dir / "Python-3.14.0a1.tgz").touch()

        db = cast(
            ReleaseShelf,
            {
                "release": release_tag,
                "git_repo": tmp_path,
                "ssh_user": "testuser",
                "ssh_key": "/path/to/key",
            },
        )

        mock_client = MagicMock(spec=paramiko.SSHClient)
        mock_transport = MagicMock()
        mock_channel = MagicMock()
        mock_sftp = MagicMock(spec=run_release.MySFTPClient)

        mock_client.get_transport.return_value = mock_transport
        mock_transport.open_session.return_value = mock_channel
        mock_channel.recv_exit_status.return_value = 0
        mock_channel.recv.return_value = b""
        mock_channel.recv_stderr.return_value = b""

        with patch("run_release.MySFTPClient.from_transport", return_value=mock_sftp), \
             patch("run_release.ssh_client") as mock_ssh_ctx, \
             patch("shutil.rmtree"):
            mock_ssh_ctx.return_value.__enter__.return_value = mock_client

            # WHEN
            run_release.upload_files_to_server(db, run_release.DOWNLOADS_SERVER)

        # THEN - rm -rf was called to clean destination
        expected_dest = f"/home/psf-users/testuser/{release_tag}"
        mock_channel.exec_command.assert_called_once_with(f"rm -rf {expected_dest}")

    def test_upload_handles_ssh_transport_failure(self, tmp_path: Path) -> None:
        """Test that upload fails gracefully when SSH transport is unavailable."""
        # GIVEN
        release_tag = Tag("3.14.0a1")
        db = cast(
            ReleaseShelf,
            {
                "release": release_tag,
                "git_repo": tmp_path,
                "ssh_user": "testuser",
                "ssh_key": "/path/to/key",
            },
        )

        mock_client = MagicMock(spec=paramiko.SSHClient)
        mock_client.get_transport.return_value = None  # Transport failed

        with patch("run_release.ssh_client") as mock_ssh_ctx:
            mock_ssh_ctx.return_value.__enter__.return_value = mock_client

            # WHEN & Assert
            with pytest.raises(AssertionError, match="SSH transport.*is None"):
                run_release.upload_files_to_server(db, run_release.DOWNLOADS_SERVER)

    def test_upload_handles_sftp_client_creation_failure(self, tmp_path: Path) -> None:
        """Test that upload fails gracefully when SFTP client creation fails."""
        # GIVEN
        release_tag = Tag("3.14.0a1")
        db = cast(
            ReleaseShelf,
            {
                "release": release_tag,
                "git_repo": tmp_path,
                "ssh_user": "testuser",
                "ssh_key": "/path/to/key",
            },
        )

        mock_client = MagicMock(spec=paramiko.SSHClient)
        mock_transport = MagicMock()
        mock_client.get_transport.return_value = mock_transport

        with patch("run_release.MySFTPClient.from_transport", return_value=None), \
             patch("run_release.ssh_client") as mock_ssh_ctx:
            mock_ssh_ctx.return_value.__enter__.return_value = mock_client

            # WHEN & Assert
            with pytest.raises(AssertionError, match="SFTP client.*is None"):
                run_release.upload_files_to_server(db, run_release.DOWNLOADS_SERVER)


class TestPlaceFilesInDownloadFolder:
    """Tests for place_files_in_download_folder function."""

    def test_places_downloads_in_correct_destination(self) -> None:
        """Test that downloads are copied to the correct public FTP directory."""
        # GIVEN
        release_tag = Tag("3.14.0a1")
        db = cast(
            ReleaseShelf,
            {
                "release": release_tag,
                "ssh_user": "testuser",
                "ssh_key": "/path/to/key",
            },
        )

        mock_client = MagicMock(spec=paramiko.SSHClient)
        mock_transport = MagicMock()

        # Create a list to track all commands executed
        executed_commands = []

        def mock_open_session():
            mock_channel = MagicMock()
            mock_channel.recv_exit_status.return_value = 0
            mock_channel.recv.return_value = b"success"
            mock_channel.recv_stderr.return_value = b""

            def track_command(cmd):
                executed_commands.append(cmd)

            mock_channel.exec_command.side_effect = track_command
            return mock_channel

        mock_client.get_transport.return_value = mock_transport
        mock_transport.open_session.side_effect = mock_open_session

        with patch("run_release.ssh_client") as mock_ssh_ctx:
            mock_ssh_ctx.return_value.__enter__.return_value = mock_client

            # WHEN
            run_release.place_files_in_download_folder(db)

        # THEN - verify destination path is correct
        expected_source = "/home/psf-users/testuser/3.14.0a1"
        # Note: normalized() converts "3.14.0a1" to "3.14.0"
        expected_dest = "/srv/www.python.org/ftp/python/3.14.0"

        assert any(f"mkdir -p {expected_dest}" in cmd for cmd in executed_commands)
        assert any(f"cp {expected_source}/downloads/* {expected_dest}" in cmd for cmd in executed_commands)

    def test_sets_correct_permissions_on_download_directory(self) -> None:
        """Test that download directory gets correct group and permissions."""
        # GIVEN
        release_tag = Tag("3.14.0a1")
        db = cast(
            ReleaseShelf,
            {
                "release": release_tag,
                "ssh_user": "testuser",
                "ssh_key": "/path/to/key",
            },
        )

        mock_client = MagicMock(spec=paramiko.SSHClient)
        mock_transport = MagicMock()

        # Create a list to track all commands executed
        executed_commands = []

        def mock_open_session():
            mock_channel = MagicMock()
            mock_channel.recv_exit_status.return_value = 0
            mock_channel.recv.return_value = b""
            mock_channel.recv_stderr.return_value = b""

            def track_command(cmd):
                executed_commands.append(cmd)

            mock_channel.exec_command.side_effect = track_command
            return mock_channel

        mock_client.get_transport.return_value = mock_transport
        mock_transport.open_session.side_effect = mock_open_session

        with patch("run_release.ssh_client") as mock_ssh_ctx:
            mock_ssh_ctx.return_value.__enter__.return_value = mock_client

            # WHEN
            run_release.place_files_in_download_folder(db)

        # THEN - verify permissions are set
        # Note: normalized() converts "3.14.0a1" to "3.14.0"
        expected_dest = "/srv/www.python.org/ftp/python/3.14.0"

        assert any(f"chgrp downloads {expected_dest}" in cmd for cmd in executed_commands)
        assert any(f"chmod 775 {expected_dest}" in cmd for cmd in executed_commands)
        assert any(f"find {expected_dest} -type f -exec chmod 664" in cmd for cmd in executed_commands)

    def test_places_docs_for_release_candidate(self) -> None:
        """Test that docs are placed in public location for release candidates."""
        # GIVEN
        release_tag = Tag("3.14.0rc1")  # Release candidate
        db = cast(
            ReleaseShelf,
            {
                "release": release_tag,
                "ssh_user": "testuser",
                "ssh_key": "/path/to/key",
            },
        )

        mock_client = MagicMock(spec=paramiko.SSHClient)
        mock_transport = MagicMock()

        # Create a list to track all commands executed
        executed_commands = []

        def mock_open_session():
            mock_channel = MagicMock()
            mock_channel.recv_exit_status.return_value = 0
            mock_channel.recv.return_value = b""
            mock_channel.recv_stderr.return_value = b""

            def track_command(cmd):
                executed_commands.append(cmd)

            mock_channel.exec_command.side_effect = track_command
            return mock_channel

        mock_client.get_transport.return_value = mock_transport
        mock_transport.open_session.side_effect = mock_open_session

        with patch("run_release.ssh_client") as mock_ssh_ctx:
            mock_ssh_ctx.return_value.__enter__.return_value = mock_client

            # WHEN
            run_release.place_files_in_download_folder(db)

        # THEN - verify docs are also copied
        expected_docs_dest = "/srv/www.python.org/ftp/python/doc/3.14.0rc1"

        assert any(f"mkdir -p {expected_docs_dest}" in cmd for cmd in executed_commands)
        assert any(f"cp /home/psf-users/testuser/3.14.0rc1/docs/* {expected_docs_dest}" in cmd for cmd in executed_commands)

    def test_does_not_place_docs_for_alpha(self) -> None:
        """Test that docs are NOT placed for alpha releases."""
        # GIVEN
        release_tag = Tag("3.14.0a1")  # Alpha release
        db = cast(
            ReleaseShelf,
            {
                "release": release_tag,
                "ssh_user": "testuser",
                "ssh_key": "/path/to/key",
            },
        )

        mock_client = MagicMock(spec=paramiko.SSHClient)
        mock_transport = MagicMock()

        # Create a list to track all commands executed
        executed_commands = []

        def mock_open_session():
            mock_channel = MagicMock()
            mock_channel.recv_exit_status.return_value = 0
            mock_channel.recv.return_value = b""
            mock_channel.recv_stderr.return_value = b""

            def track_command(cmd):
                executed_commands.append(cmd)

            mock_channel.exec_command.side_effect = track_command
            return mock_channel

        mock_client.get_transport.return_value = mock_transport
        mock_transport.open_session.side_effect = mock_open_session

        with patch("run_release.ssh_client") as mock_ssh_ctx:
            mock_ssh_ctx.return_value.__enter__.return_value = mock_client

            # WHEN
            run_release.place_files_in_download_folder(db)

        # THEN - verify docs destination is NOT created
        # Should not have any commands referencing /ftp/python/doc/
        assert not any("/ftp/python/doc/" in cmd for cmd in executed_commands)

    def test_handles_permission_command_failure(self) -> None:
        """Test that function raises clear error when permission commands fail."""
        # GIVEN
        release_tag = Tag("3.14.0a1")
        db = cast(
            ReleaseShelf,
            {
                "release": release_tag,
                "ssh_user": "testuser",
                "ssh_key": "/path/to/key",
            },
        )

        mock_client = MagicMock(spec=paramiko.SSHClient)
        mock_transport = MagicMock()

        call_count = [0]

        def mock_open_session():
            mock_channel = MagicMock()
            # First 3 commands succeed, 4th fails
            call_count[0] += 1
            if call_count[0] <= 3:
                mock_channel.recv_exit_status.return_value = 0
                mock_channel.recv.return_value = b""
                mock_channel.recv_stderr.return_value = b""
            else:
                mock_channel.recv_exit_status.return_value = 1
                mock_channel.recv.return_value = b""
                mock_channel.recv_stderr.return_value = b"Operation not permitted"
            return mock_channel

        mock_client.get_transport.return_value = mock_transport
        mock_transport.open_session.side_effect = mock_open_session

        with patch("run_release.ssh_client") as mock_ssh_ctx:
            mock_ssh_ctx.return_value.__enter__.return_value = mock_client

            # WHEN & Assert
            with pytest.raises(run_release.ReleaseException, match="Operation not permitted"):
                run_release.place_files_in_download_folder(db)


class TestUnpackDocsInTheDocsServer:
    """Tests for unpack_docs_in_the_docs_server function."""

    def test_unpacks_docs_for_release_candidate(self) -> None:
        """Test that docs are unpacked and deployed for release candidates."""
        # GIVEN
        release_tag = Tag("3.14.0rc1")
        db = cast(
            ReleaseShelf,
            {
                "release": release_tag,
                "ssh_user": "testuser",
                "ssh_key": "/path/to/key",
            },
        )

        mock_client = MagicMock(spec=paramiko.SSHClient)
        mock_transport = MagicMock()

        # Create a list to track all commands executed
        executed_commands = []

        def mock_open_session():
            mock_channel = MagicMock()
            mock_channel.recv_exit_status.return_value = 0
            mock_channel.recv.return_value = b""
            mock_channel.recv_stderr.return_value = b""

            def track_command(cmd):
                executed_commands.append(cmd)

            mock_channel.exec_command.side_effect = track_command
            return mock_channel

        mock_client.get_transport.return_value = mock_transport
        mock_transport.open_session.side_effect = mock_open_session

        with patch("run_release.ssh_client") as mock_ssh_ctx:
            mock_ssh_ctx.return_value.__enter__.return_value = mock_client

            # WHEN
            run_release.unpack_docs_in_the_docs_server(db)

        # THEN - verify unzip and move commands
        expected_source = "/home/psf-users/testuser/3.14.0rc1"
        expected_dest = "/srv/docs.python.org/release/3.14.0rc1"

        assert any(f"mkdir -p {expected_dest}" in cmd for cmd in executed_commands)
        assert any(f"unzip {expected_source}/docs/python-3.14.0rc1-docs-html.zip" in cmd for cmd in executed_commands)
        assert any(f"mv /{expected_dest}/python-3.14.0rc1-docs-html/* {expected_dest}" in cmd for cmd in executed_commands)
        assert any(f"rm -rf /{expected_dest}/python-3.14.0rc1-docs-html" in cmd for cmd in executed_commands)

    def test_sets_correct_permissions_on_docs(self) -> None:
        """Test that docs get correct group ownership and permissions."""
        # GIVEN
        release_tag = Tag("3.14.0rc1")
        db = cast(
            ReleaseShelf,
            {
                "release": release_tag,
                "ssh_user": "testuser",
                "ssh_key": "/path/to/key",
            },
        )

        mock_client = MagicMock(spec=paramiko.SSHClient)
        mock_transport = MagicMock()
        mock_channel = MagicMock()

        mock_client.get_transport.return_value = mock_transport
        mock_transport.open_session.return_value = mock_channel
        mock_channel.recv_exit_status.return_value = 0
        mock_channel.recv.return_value = b""
        mock_channel.recv_stderr.return_value = b""

        with patch("run_release.ssh_client") as mock_ssh_ctx:
            mock_ssh_ctx.return_value.__enter__.return_value = mock_client

            # WHEN
            run_release.unpack_docs_in_the_docs_server(db)

        # THEN - verify permissions
        expected_dest = "/srv/docs.python.org/release/3.14.0rc1"
        exec_commands = [call[0][0] for call in mock_channel.exec_command.call_args_list]

        assert any(f"chgrp -R docs {expected_dest}" in cmd for cmd in exec_commands)
        assert any(f"chmod -R 775 {expected_dest}" in cmd for cmd in exec_commands)
        assert any(f"find {expected_dest} -type f -exec chmod 664" in cmd for cmd in exec_commands)

    def test_does_nothing_for_alpha_release(self) -> None:
        """Test that function does nothing for alpha releases."""
        # GIVEN
        release_tag = Tag("3.14.0a1")
        db = cast(
            ReleaseShelf,
            {
                "release": release_tag,
                "ssh_user": "testuser",
                "ssh_key": "/path/to/key",
            },
        )

        mock_client = MagicMock(spec=paramiko.SSHClient)

        with patch("run_release.ssh_client") as mock_ssh_ctx:
            mock_ssh_ctx.return_value.__enter__.return_value = mock_client

            # WHEN
            run_release.unpack_docs_in_the_docs_server(db)

        # THEN - ssh_client should not even be entered
        mock_ssh_ctx.assert_not_called()

    def test_does_nothing_for_beta_release(self) -> None:
        """Test that function does nothing for beta releases."""
        # GIVEN
        release_tag = Tag("3.14.0b1")
        db = cast(
            ReleaseShelf,
            {
                "release": release_tag,
                "ssh_user": "testuser",
                "ssh_key": "/path/to/key",
            },
        )

        mock_client = MagicMock(spec=paramiko.SSHClient)

        with patch("run_release.ssh_client") as mock_ssh_ctx:
            mock_ssh_ctx.return_value.__enter__.return_value = mock_client

            # WHEN
            run_release.unpack_docs_in_the_docs_server(db)

        # THEN - ssh_client should not be called
        mock_ssh_ctx.assert_not_called()

    def test_unpacks_docs_for_final_release(self) -> None:
        """Test that docs are unpacked for final releases."""
        # GIVEN
        release_tag = Tag("3.14.0")  # Final release
        db = cast(
            ReleaseShelf,
            {
                "release": release_tag,
                "ssh_user": "testuser",
                "ssh_key": "/path/to/key",
            },
        )

        mock_client = MagicMock(spec=paramiko.SSHClient)
        mock_transport = MagicMock()
        mock_channel = MagicMock()

        mock_client.get_transport.return_value = mock_transport
        mock_transport.open_session.return_value = mock_channel
        mock_channel.recv_exit_status.return_value = 0
        mock_channel.recv.return_value = b""
        mock_channel.recv_stderr.return_value = b""

        with patch("run_release.ssh_client") as mock_ssh_ctx:
            mock_ssh_ctx.return_value.__enter__.return_value = mock_client

            # WHEN
            run_release.unpack_docs_in_the_docs_server(db)

        # THEN - commands were executed
        mock_channel.exec_command.assert_called()

    def test_handles_unzip_failure(self) -> None:
        """Test that function raises clear error when unzip fails."""
        # GIVEN
        release_tag = Tag("3.14.0rc1")
        db = cast(
            ReleaseShelf,
            {
                "release": release_tag,
                "ssh_user": "testuser",
                "ssh_key": "/path/to/key",
            },
        )

        mock_client = MagicMock(spec=paramiko.SSHClient)
        mock_transport = MagicMock()
        mock_channel = MagicMock()

        mock_client.get_transport.return_value = mock_transport
        mock_transport.open_session.return_value = mock_channel
        # First command (mkdir) succeeds, second (unzip) fails
        mock_channel.recv_exit_status.side_effect = [0, 1]
        mock_channel.recv.return_value = b""
        mock_channel.recv_stderr.return_value = b"cannot find zipfile"

        with patch("run_release.ssh_client") as mock_ssh_ctx:
            mock_ssh_ctx.return_value.__enter__.return_value = mock_client

            # WHEN & Assert
            with pytest.raises(run_release.ReleaseException, match="cannot find zipfile"):
                run_release.unpack_docs_in_the_docs_server(db)


class TestWaitUntilAllFilesAreInFolder:
    """Tests for wait_until_all_files_are_in_folder function."""

    def test_succeeds_immediately_when_all_files_present(self) -> None:
        """Test that function returns immediately when all required files exist."""
        # GIVEN
        release_tag = Tag("3.14.0a1")
        db = cast(
            ReleaseShelf,
            {
                "release": release_tag,
                "ssh_user": "testuser",
                "ssh_key": "/path/to/key",
                "security_release": False,
            },
        )

        mock_client = MagicMock(spec=paramiko.SSHClient)
        mock_sftp = MagicMock()

        # All required files are present
        mock_sftp.listdir.return_value = [
            "Python-3.14.0a1.tgz",  # Linux
            "python-3.14.0a1.exe",  # Windows
            "python-3.14.0a1-macos11.pkg",  # macOS
            "Python-3.14.0a1.tar.xz",
        ]

        mock_client.open_sftp.return_value = mock_sftp

        with patch("run_release.ssh_client") as mock_ssh_ctx:
            mock_ssh_ctx.return_value.__enter__.return_value = mock_client

            # WHEN
            run_release.wait_until_all_files_are_in_folder(db)

        # THEN - listdir called only once (no retry loop)
        assert mock_sftp.listdir.call_count == 1

    def test_waits_and_retries_when_files_missing(self) -> None:
        """Test that function polls repeatedly when files are not yet available."""
        # GIVEN
        release_tag = Tag("3.14.0a1")
        db = cast(
            ReleaseShelf,
            {
                "release": release_tag,
                "ssh_user": "testuser",
                "ssh_key": "/path/to/key",
                "security_release": False,
            },
        )

        mock_client = MagicMock(spec=paramiko.SSHClient)
        mock_sftp = MagicMock()

        # Simulate files appearing gradually
        mock_sftp.listdir.side_effect = [
            ["Python-3.14.0a1.tgz"],  # First check: only Linux
            ["Python-3.14.0a1.tgz", "python-3.14.0a1.exe"],  # Second: + Windows
            ["Python-3.14.0a1.tgz", "python-3.14.0a1.exe", "python-3.14.0a1-macos11.pkg"],  # Third: all
        ]

        mock_client.open_sftp.return_value = mock_sftp

        with patch("run_release.ssh_client") as mock_ssh_ctx, \
             patch("time.sleep") as mock_sleep:
            mock_ssh_ctx.return_value.__enter__.return_value = mock_client

            # WHEN
            run_release.wait_until_all_files_are_in_folder(db)

        # THEN - polled 3 times, slept 2 times
        assert mock_sftp.listdir.call_count == 3
        assert mock_sleep.call_count == 2

    def test_security_release_only_checks_linux_files(self) -> None:
        """Test that security releases only wait for Linux source files."""
        # GIVEN
        release_tag = Tag("3.14.0a1")
        db = cast(
            ReleaseShelf,
            {
                "release": release_tag,
                "ssh_user": "testuser",
                "ssh_key": "/path/to/key",
                "security_release": True,  # Security release
            },
        )

        mock_client = MagicMock(spec=paramiko.SSHClient)
        mock_sftp = MagicMock()

        # Only Linux files present (no Windows/macOS)
        mock_sftp.listdir.return_value = [
            "Python-3.14.0a1.tgz",
            "Python-3.14.0a1.tar.xz",
        ]

        mock_client.open_sftp.return_value = mock_sftp

        with patch("run_release.ssh_client") as mock_ssh_ctx:
            mock_ssh_ctx.return_value.__enter__.return_value = mock_client

            # WHEN - should succeed without waiting for Windows/macOS
            run_release.wait_until_all_files_are_in_folder(db)

        # THEN - completed with only Linux files
        assert mock_sftp.listdir.call_count == 1

    def test_raises_clear_error_when_destination_folder_missing(self) -> None:
        """Test that function fails fast when release folder doesn't exist."""
        # GIVEN
        release_tag = Tag("3.14.0a1")
        db = cast(
            ReleaseShelf,
            {
                "release": release_tag,
                "ssh_user": "testuser",
                "ssh_key": "/path/to/key",
                "security_release": False,
            },
        )

        mock_client = MagicMock(spec=paramiko.SSHClient)
        mock_sftp = MagicMock()

        # Folder doesn't exist
        mock_sftp.listdir.side_effect = FileNotFoundError("No such file or directory")
        mock_client.open_sftp.return_value = mock_sftp

        with patch("run_release.ssh_client") as mock_ssh_ctx:
            mock_ssh_ctx.return_value.__enter__.return_value = mock_client

            # WHEN & Assert
            with pytest.raises(FileNotFoundError, match="release folder.*has not been created"):
                run_release.wait_until_all_files_are_in_folder(db)

    def test_identifies_linux_files_correctly(self) -> None:
        """Test that function correctly identifies Linux source tarballs."""
        # GIVEN
        release_tag = Tag("3.14.0a1")
        db = cast(
            ReleaseShelf,
            {
                "release": release_tag,
                "ssh_user": "testuser",
                "ssh_key": "/path/to/key",
                "security_release": False,
            },
        )

        mock_client = MagicMock(spec=paramiko.SSHClient)
        mock_sftp = MagicMock()

        # Has .tar.xz but not .tgz - should wait
        mock_sftp.listdir.side_effect = [
            ["Python-3.14.0a1.tar.xz", "python-3.14.0a1.exe", "python-3.14.0a1-macos11.pkg"],
            ["Python-3.14.0a1.tgz", "Python-3.14.0a1.tar.xz", "python-3.14.0a1.exe", "python-3.14.0a1-macos11.pkg"],
        ]

        mock_client.open_sftp.return_value = mock_sftp

        with patch("run_release.ssh_client") as mock_ssh_ctx, \
             patch("time.sleep"):
            mock_ssh_ctx.return_value.__enter__.return_value = mock_client

            # WHEN
            run_release.wait_until_all_files_are_in_folder(db)

        # THEN - had to check twice because .tgz was missing initially
        assert mock_sftp.listdir.call_count == 2

    def test_identifies_windows_files_correctly(self) -> None:
        """Test that function correctly identifies Windows .exe installer."""
        # GIVEN
        release_tag = Tag("3.14.0a1")
        db = cast(
            ReleaseShelf,
            {
                "release": release_tag,
                "ssh_user": "testuser",
                "ssh_key": "/path/to/key",
                "security_release": False,
            },
        )

        mock_client = MagicMock(spec=paramiko.SSHClient)
        mock_sftp = MagicMock()

        # Missing .exe file
        mock_sftp.listdir.side_effect = [
            ["Python-3.14.0a1.tgz", "python-3.14.0a1-macos11.pkg"],
            ["Python-3.14.0a1.tgz", "python-3.14.0a1.exe", "python-3.14.0a1-macos11.pkg"],
        ]

        mock_client.open_sftp.return_value = mock_sftp

        with patch("run_release.ssh_client") as mock_ssh_ctx, \
             patch("time.sleep"):
            mock_ssh_ctx.return_value.__enter__.return_value = mock_client

            # WHEN
            run_release.wait_until_all_files_are_in_folder(db)

        # THEN - retried until .exe appeared
        assert mock_sftp.listdir.call_count == 2

    def test_identifies_macos_files_correctly(self) -> None:
        """Test that function correctly identifies macOS .pkg installer."""
        # GIVEN
        release_tag = Tag("3.14.0a1")
        db = cast(
            ReleaseShelf,
            {
                "release": release_tag,
                "ssh_user": "testuser",
                "ssh_key": "/path/to/key",
                "security_release": False,
            },
        )

        mock_client = MagicMock(spec=paramiko.SSHClient)
        mock_sftp = MagicMock()

        # Missing macOS pkg file
        mock_sftp.listdir.side_effect = [
            ["Python-3.14.0a1.tgz", "python-3.14.0a1.exe"],
            ["Python-3.14.0a1.tgz", "python-3.14.0a1.exe", "python-3.14.0a1-macos11.pkg"],
        ]

        mock_client.open_sftp.return_value = mock_sftp

        with patch("run_release.ssh_client") as mock_ssh_ctx, \
             patch("time.sleep"):
            mock_ssh_ctx.return_value.__enter__.return_value = mock_client

            # WHEN
            run_release.wait_until_all_files_are_in_folder(db)

        # THEN - retried until macOS pkg appeared
        assert mock_sftp.listdir.call_count == 2
