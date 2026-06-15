import builtins
import contextlib
import io
import tarfile
from contextlib import nullcontext as does_not_raise
from pathlib import Path
from typing import cast

import pytest

import run_release
from release import ReleaseShelf, Tag
from run_release import ReleaseException


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
        ReleaseException, match="Sigstore version not detected or not valid"
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
        ReleaseException,
        match="Could not parse GitHub owner from 'origin' remote URL: "
        "https://example.com",
    ):
        run_release.extract_github_owner("https://example.com")


def test_join_remote_command_rejects_string_command() -> None:
    assert (
        run_release.join_remote_command(("echo", "hello world")) == "echo 'hello world'"
    )
    with pytest.raises(TypeError, match="remote command must be a list or tuple"):
        run_release.join_remote_command("echo hello")


@pytest.mark.parametrize(
    ["release_tag", "git_current_branch", "expectation"],
    [
        # Success cases
        ("3.15.0rc1", "3.15\n", does_not_raise()),
        ("3.15.0b3", "3.15\n", does_not_raise()),
        ("3.15.0b2", "3.15\n", does_not_raise()),
        ("3.15.0b1", "main\n", does_not_raise()),
        ("3.15.0a6", "main\n", does_not_raise()),
        ("3.14.3", "3.14\n", does_not_raise()),
        ("3.13.12", "3.13\n", does_not_raise()),
        # Failure cases
        (
            "3.15.0rc1",
            "main\n",
            pytest.raises(ReleaseException, match="on main branch, expected 3.15"),
        ),
        (
            "3.15.0b1",
            "3.15\n",
            pytest.raises(ReleaseException, match="on 3.15 branch, expected main"),
        ),
        (
            "3.15.0a6",
            "3.14\n",
            pytest.raises(ReleaseException, match="on 3.14 branch, expected main"),
        ),
        (
            "3.14.3",
            "main\n",
            pytest.raises(ReleaseException, match="on main branch, expected 3.14"),
        ),
    ],
)
def test_check_cpython_repo_branch(
    monkeypatch, release_tag: str, git_current_branch: str, expectation
) -> None:
    # Arrange
    db = {"release": Tag(release_tag), "git_repo": "/fake/repo"}
    monkeypatch.setattr(
        run_release.subprocess,
        "check_output",
        lambda *args, **kwargs: git_current_branch,
    )

    # Act / Assert
    with expectation:
        run_release.check_cpython_repo_branch(cast(ReleaseShelf, db))


@pytest.mark.parametrize(
    ["age_seconds", "user_continues", "expectation"],
    [
        # Recent repo (< 1 day) - no question asked
        (3600, None, does_not_raise()),
        # Old repo (> 1 day) + user says yes
        (90000, True, does_not_raise()),
        # Old repo (> 1 day) + user says no
        (90000, False, pytest.raises(ReleaseException, match="repository is old")),
    ],
)
def test_check_cpython_repo_age(
    monkeypatch, age_seconds: int, user_continues: bool | None, expectation
) -> None:
    # Arrange
    db = {"release": Tag("3.15.0a6"), "git_repo": "/fake/repo"}
    current_time = 1700000000
    commit_timestamp = current_time - age_seconds

    def fake_check_output(cmd, **kwargs):
        cmd_str = " ".join(cmd)
        if "%ct" in cmd_str:
            return f"{commit_timestamp}\n"
        if "%cr" in cmd_str:
            return "some time ago\n"
        return ""

    monkeypatch.setattr(run_release.subprocess, "check_output", fake_check_output)
    monkeypatch.setattr(run_release.time, "time", lambda: current_time)
    if user_continues is not None:
        monkeypatch.setattr(run_release, "ask_question", lambda _: user_continues)

    # Act / Assert
    with expectation:
        run_release.check_cpython_repo_age(cast(ReleaseShelf, db))


def test_check_magic_number() -> None:
    db = {
        "release": Tag("3.14.0rc1"),
        "git_repo": str(Path(__file__).parent / "magicdata"),
    }
    with pytest.raises(ReleaseException, match="Magic numbers in .* don't match"):
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
    # Arrange
    # Only first beta triggers update
    db = {"release": Tag("3.14.0b1")}

    original_toctree_file = Path(__file__).parent / "whatsnew_index.rst"
    toctree__file = tmp_path / "patchlevel.h"
    toctree__file.write_text(original_toctree_file.read_text())

    # Act
    run_release.update_whatsnew_toctree(cast(ReleaseShelf, db), str(toctree__file))

    # Assert
    new_contents = toctree__file.read_text()
    assert "   3.15.rst\n   3.14.rst\n" in new_contents


def test_run_add_to_python_dot_org_quotes_remote_environment(monkeypatch) -> None:
    commands = []

    class FakeSFTPClient:
        def put(self, source: str, destination: str) -> None:
            pass

        def close(self) -> None:
            pass

    class FakeSSHClient:
        def load_system_host_keys(self) -> None:
            pass

        def set_missing_host_key_policy(self, policy) -> None:
            pass

        def connect(self, *args, **kwargs) -> None:
            pass

        def get_transport(self):
            return object()

        def exec_command(self, command: str):
            commands.append(command)
            return None, io.BytesIO(b"ok"), io.BytesIO()

    class FakeIssuer:
        def __init__(self, issuer_url: str) -> None:
            self.issuer_url = issuer_url

        def identity_token(self) -> str:
            return "token; touch /tmp/pwned"

    monkeypatch.setattr(run_release.paramiko, "SSHClient", FakeSSHClient)
    monkeypatch.setattr(
        run_release.MySFTPClient,
        "from_transport",
        staticmethod(lambda transport: FakeSFTPClient()),
    )
    monkeypatch.setattr(run_release.sigstore.oidc, "Issuer", FakeIssuer)

    db = {
        "auth_info": "user:key; echo pwned",
        "release": Tag("3.15.0a1"),
        "ssh_key": None,
        "ssh_user": "release-manager",
    }

    run_release.run_add_to_python_dot_org(cast(ReleaseShelf, db))

    assert commands == [
        "env 'AUTH_INFO=user:key; echo pwned' "
        "'SIGSTORE_IDENTITY_TOKEN=token; touch /tmp/pwned' "
        "python3 add_to_pydotorg.py 3.15.0a1"
    ]


def test_upload_files_to_server_quotes_remote_cleanup_path(
    monkeypatch, tmp_path: Path
) -> None:
    commands = []

    class FakeSFTPClient:
        def mkdir(self, path: str) -> None:
            pass

        def put_dir(self, source: Path, target: str, progress) -> None:
            pass

        def close(self) -> None:
            pass

    class FakeSSHClient:
        def load_system_host_keys(self) -> None:
            pass

        def set_missing_host_key_policy(self, policy) -> None:
            pass

        def connect(self, *args, **kwargs) -> None:
            pass

        def get_transport(self):
            return object()

        def exec_command(self, command: str) -> None:
            commands.append(command)

    @contextlib.contextmanager
    def fake_alive_bar(total: int):
        yield lambda *args, **kwargs: None

    release = Tag("3.15.0a1")
    artifacts_path = tmp_path / str(release)
    (artifacts_path / "downloads").mkdir(parents=True)

    monkeypatch.setattr(run_release.paramiko, "SSHClient", FakeSSHClient)
    monkeypatch.setattr(
        run_release.MySFTPClient,
        "from_transport",
        staticmethod(lambda transport: FakeSFTPClient()),
    )
    monkeypatch.setattr(run_release, "alive_bar", fake_alive_bar)

    db = {
        "git_repo": tmp_path,
        "release": release,
        "ssh_key": None,
        "ssh_user": "release-manager; touch /tmp/pwned #",
    }

    run_release.upload_files_to_server(
        cast(ReleaseShelf, db), run_release.DOWNLOADS_SERVER
    )

    assert commands == [
        "rm -rf '/home/psf-users/release-manager; touch /tmp/pwned #/3.15.0a1'"
    ]


def test_release_file_placement_quotes_remote_paths(monkeypatch) -> None:
    commands = []

    class FakeChannel:
        def exec_command(self, command: str) -> None:
            commands.append(command)

        def recv_exit_status(self) -> int:
            return 0

        def recv_stderr(self, size: int) -> bytes:
            return b""

    class FakeTransport:
        def open_session(self) -> FakeChannel:
            return FakeChannel()

    class FakeSSHClient:
        def load_system_host_keys(self) -> None:
            pass

        def set_missing_host_key_policy(self, policy) -> None:
            pass

        def connect(self, *args, **kwargs) -> None:
            pass

        def get_transport(self) -> FakeTransport:
            return FakeTransport()

    monkeypatch.setattr(run_release.paramiko, "SSHClient", FakeSSHClient)

    db = {
        "release": Tag("3.15.0rc1"),
        "ssh_key": None,
        "ssh_user": "release-manager; touch /tmp/pwned #",
    }

    run_release.place_files_in_download_folder(cast(ReleaseShelf, db))
    run_release.unpack_docs_in_the_docs_server(cast(ReleaseShelf, db))

    assert commands == [
        "mkdir -p /srv/www.python.org/ftp/python/3.15.0",
        'sh -c \'cp "$1"/* "$2"\' sh '
        "'/home/psf-users/release-manager; touch /tmp/pwned #/3.15.0rc1/downloads' "
        "/srv/www.python.org/ftp/python/3.15.0",
        "find /srv/www.python.org/ftp/python/3.15.0 -maxdepth 0 '!' "
        "-group downloads -exec chgrp downloads '{}' +",
        "find /srv/www.python.org/ftp/python/3.15.0 -maxdepth 0 '!' -perm 775 "
        "-exec chmod 775 '{}' +",
        "find /srv/www.python.org/ftp/python/3.15.0 -maxdepth 1 -type f -user "
        "'release-manager; touch /tmp/pwned #' '!' -perm 664 -exec chmod 664 '{}' +",
        "mkdir -p /srv/www.python.org/ftp/python/doc/3.15.0rc1",
        'sh -c \'cp "$1"/* "$2"\' sh '
        "'/home/psf-users/release-manager; touch /tmp/pwned #/3.15.0rc1/docs' "
        "/srv/www.python.org/ftp/python/doc/3.15.0rc1",
        "find /srv/www.python.org/ftp/python/doc/3.15.0rc1 -maxdepth 0 '!' "
        "-group downloads -exec chgrp downloads '{}' +",
        "find /srv/www.python.org/ftp/python/doc/3.15.0rc1 -maxdepth 0 '!' "
        "-perm 775 -exec chmod 775 '{}' +",
        "find /srv/www.python.org/ftp/python/doc/3.15.0rc1 -maxdepth 1 -type f "
        "-user 'release-manager; touch /tmp/pwned #' '!' -perm 664 "
        "-exec chmod 664 '{}' +",
        "mkdir -p /srv/docs.python.org/release/3.15.0rc1",
        "unzip '/home/psf-users/release-manager; touch /tmp/pwned #/3.15.0rc1/docs/"
        "python-3.15.0rc1-docs-html.zip' -d /srv/docs.python.org/release/3.15.0rc1",
        'sh -c \'mv "$1"/* "$2"\' sh '
        "//srv/docs.python.org/release/3.15.0rc1/python-3.15.0rc1-docs-html "
        "/srv/docs.python.org/release/3.15.0rc1",
        "rm -rf //srv/docs.python.org/release/3.15.0rc1/python-3.15.0rc1-docs-html",
        "chgrp -R docs /srv/docs.python.org/release/3.15.0rc1",
        "chmod -R 775 /srv/docs.python.org/release/3.15.0rc1",
        "find /srv/docs.python.org/release/3.15.0rc1 -type f -exec chmod 664 '{}' ';'",
    ]
