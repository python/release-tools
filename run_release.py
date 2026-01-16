#!/usr/bin/env python3

"""An automatic engine for Python releases

Original code by Pablo Galindo
"""
from __future__ import annotations

import argparse
import asyncio
import contextlib
import functools
import getpass
import json
import os
import re
import shelve
import shlex
import shutil
import subprocess
import sys
import tempfile
import time
import urllib.request
from collections.abc import Iterator
from pathlib import Path
from typing import Any, cast

import aiohttp
import gnupg  # type: ignore[import-untyped]
import paramiko
import sigstore.oidc
from alive_progress import alive_bar

import release as release_mod
import sbom
import update_version_next
from buildbotapi import BuildBotAPI, Builder
from release import ReleaseShelf, Tag, Task, ask_question

API_KEY_REGEXP = re.compile(r"(?P<user>\w+):(?P<key>\w+)")
RELEASE_REGEXP = re.compile(
    r"(?P<major>\d+)\.(?P<minor>\d+)\.(?P<patch>\d+)\.?(?P<extra>.*)?"
)
DOWNLOADS_SERVER = "downloads.nyc1.psf.io"
DOCS_SERVER = "docs.nyc1.psf.io"

WHATS_NEW_TEMPLATE = """
****************************
  What's new in Python {version}
****************************

:Editor: TBD

.. Rules for maintenance:

   * Anyone can add text to this document.  Do not spend very much time
   on the wording of your changes, because your text will probably
   get rewritten to some degree.

   * The maintainer will go through Misc/NEWS periodically and add
   changes; it's therefore more important to add your changes to
   Misc/NEWS than to this file.

   * This is not a complete list of every single change; completeness
   is the purpose of Misc/NEWS.  Some changes I consider too small
   or esoteric to include.  If such a change is added to the text,
   I'll just remove it.  (This is another reason you shouldn't spend
   too much time on writing your addition.)

   * If you want to draw your new text to the attention of the
   maintainer, add 'XXX' to the beginning of the paragraph or
   section.

   * It's OK to just add a fragmentary note about a change.  For
   example: "XXX Describe the transmogrify() function added to the
   socket module."  The maintainer will research the change and
   write the necessary text.

   * You can comment out your additions if you like, but it's not
   necessary (especially when a final release is some months away).

   * Credit the author of a patch or bugfix.   Just the name is
   sufficient; the e-mail address isn't necessary.

   * It's helpful to add the issue number as a comment:

   XXX Describe the transmogrify() function added to the socket
   module.
   (Contributed by P.Y. Developer in :gh:`12345`.)

   This saves the maintainer the effort of going through the VCS log
   when researching a change.

This article explains the new features in Python {version}, compared to {prev_version}.

For full details, see the :ref:`changelog <changelog>`.

.. note::

   Prerelease users should be aware that this document is currently in draft
   form. It will be updated substantially as Python {version} moves towards release,
   so it's worth checking back even after reading earlier versions.


Summary --- release highlights
==============================

.. This section singles out the most important changes in Python {version}.
   Brevity is key.


.. PEP-sized items next.



New features
============



Other language changes
======================



New modules
===========

* None yet.


Improved modules
================

module_name
-----------

* TODO

.. Add improved modules above alphabetically, not here at the end.

Optimizations
=============

module_name
-----------

* TODO



Removed
=======

module_name
-----------

* TODO
.. Add removals above alphabetically, not here at the end.


Deprecated
==========

* module_name:
  TODO


.. Add deprecations above alphabetically, not here at the end.


Porting to Python {version}
======================

This section lists previously described changes and other bugfixes
that may require changes to your code.


Build changes
=============


C API changes
=============

New features
------------

* TODO

Porting to Python {version}
----------------------

* TODO

Deprecated C APIs
-----------------

* TODO

.. Add C API deprecations above alphabetically, not here at the end.

Removed C APIs
--------------

"""


class ReleaseException(Exception):
    """An error happened in the release process"""


class ReleaseDriver:
    def __init__(
        self,
        tasks: list[Task],
        *,
        release_tag: Tag,
        git_repo: str,
        api_key: str,
        ssh_user: str,
        sign_gpg: bool,
        ssh_key: str | None = None,
        first_state: Task | None = None,
    ) -> None:
        self.tasks = tasks
        dbfile = Path.home() / ".python_release"
        self.db: ReleaseShelf = cast(ReleaseShelf, shelve.open(str(dbfile), "c"))
        if not self.db.get("finished"):
            self.db["finished"] = False
        else:
            self.db.close()
            self.db = cast(ReleaseShelf, shelve.open(str(dbfile), "n"))

        self.current_task: Task | None = first_state
        self.completed_tasks = self.db.get("completed_tasks", [])
        self.remaining_tasks = iter(tasks[len(self.completed_tasks) :])
        if self.db.get("gpg_key"):
            os.environ["GPG_KEY_FOR_RELEASE"] = self.db["gpg_key"]
        if not self.db.get("git_repo"):
            self.db["git_repo"] = Path(git_repo)
        if not self.db.get("auth_info"):
            self.db["auth_info"] = api_key
        if not self.db.get("ssh_user"):
            self.db["ssh_user"] = ssh_user
        if not self.db.get("ssh_key"):
            self.db["ssh_key"] = ssh_key
        if not self.db.get("sign_gpg"):
            self.db["sign_gpg"] = sign_gpg
        if not self.db.get("release"):
            self.db["release"] = release_tag
        if not self.db.get("security_release"):
            self.db["security_release"] = self.db["release"].is_security_release

        print("Release data: ")
        print(f"- Branch: {release_tag.branch}")
        print(f"- Release tag: {self.db['release']}")
        print(f"- Normalized release tag: {release_tag.normalized()}")
        print(f"- Git repo: {self.db['git_repo']}")
        print(f"- SSH username: {self.db['ssh_user']}")
        print(f"- SSH key: {self.db['ssh_key'] or 'Default'}")
        print(f"- Sign with GPG: {self.db['sign_gpg']}")
        print(f"- Security release: {self.db['security_release']}")
        print()

    def checkpoint(self) -> None:
        self.db["completed_tasks"] = self.completed_tasks

    def run(self) -> None:
        for task in self.completed_tasks:
            print(f"✅  {task.description}")

        self.current_task = next(self.remaining_tasks, None)
        while self.current_task is not None:
            self.checkpoint()
            try:
                self.current_task(self.db)
            except Exception as e:
                print(f"\r💥  {self.current_task.description}")
                raise e from None
            print(f"\r✅  {self.current_task.description}")
            self.completed_tasks.append(self.current_task)
            self.current_task = next(self.remaining_tasks, None)
        self.db["finished"] = True
        print()
        print(f"Congratulations, Python {self.db['release']} is released 🎉🎉🎉")


@contextlib.contextmanager
def cd(path: Path) -> Iterator[None]:
    current_path = os.getcwd()
    os.chdir(path)
    yield
    os.chdir(current_path)


def check_tool(db: ReleaseShelf, tool: str) -> None:
    if shutil.which(tool) is None:
        raise ReleaseException(f"{tool} is not available")


check_gh = functools.partial(check_tool, tool="gh")
check_git = functools.partial(check_tool, tool="git")
check_make = functools.partial(check_tool, tool="make")
check_blurb = functools.partial(check_tool, tool="blurb")
check_autoconf = functools.partial(check_tool, tool="autoconf")
check_docker = functools.partial(check_tool, tool="docker")


def check_gpg_keys(db: ReleaseShelf) -> None:
    pg = gnupg.GPG()
    keys = pg.list_keys(secret=True)
    if not keys:
        raise ReleaseException("There are no valid GPG keys for release")
    for index, key in enumerate(keys):
        print(f"{index} - {key['keyid']}: {key['uids']}")
    selected_key_index = -1
    while not (0 <= selected_key_index < len(keys)):
        with contextlib.suppress(ValueError):
            selected_key_index = int(
                input("Select one GPG key for release (by index):")
            )
    selected_key = keys[selected_key_index]["keyid"]
    os.environ["GPG_KEY_FOR_db['release']"] = selected_key
    if selected_key not in {key["keyid"] for key in keys}:
        raise ReleaseException("Invalid GPG key selected")
    db["gpg_key"] = selected_key
    os.environ["GPG_KEY_FOR_RELEASE"] = db["gpg_key"]


def check_ssh_connection(db: ReleaseShelf) -> None:
    client = paramiko.SSHClient()
    client.load_system_host_keys()
    client.set_missing_host_key_policy(paramiko.WarningPolicy)
    client.connect(
        DOWNLOADS_SERVER, port=22, username=db["ssh_user"], key_filename=db["ssh_key"]
    )
    client.exec_command("pwd")
    client.connect(
        DOCS_SERVER, port=22, username=db["ssh_user"], key_filename=db["ssh_key"]
    )
    client.exec_command("pwd")


def check_sigstore_client(db: ReleaseShelf) -> None:
    client = paramiko.SSHClient()
    client.load_system_host_keys()
    client.set_missing_host_key_policy(paramiko.WarningPolicy)
    client.connect(
        DOWNLOADS_SERVER, port=22, username=db["ssh_user"], key_filename=db["ssh_key"]
    )
    _, stdout, _ = client.exec_command("python3 -m sigstore --version")
    sigstore_version = stdout.read(1000).decode()
    check_sigstore_version(sigstore_version)


def check_sigstore_version(version: str) -> None:
    version_match = re.match("^sigstore ([0-9.]+)", version)
    if version_match:
        version_tuple = tuple(int(part) for part in version_match.group(1).split("."))
        if (3, 6, 2) <= version_tuple < (4, 0):
            # good version
            return

    raise ReleaseException(
        f"Sigstore version not detected or not valid. "
        f"Expecting >= 3.6.2 and < 4.0.0, got: {version}"
    )


def check_buildbots(db: ReleaseShelf) -> None:
    async def _check() -> set[Builder]:
        async def _get_builder_status(
            buildbot_api: BuildBotAPI, the_builder: Builder
        ) -> tuple[Builder, bool]:
            return the_builder, await buildbot_api.is_builder_failing_currently(
                the_builder
            )

        async with aiohttp.ClientSession() as session:
            api = BuildBotAPI(session)
            await api.authenticate(token="")
            release_branch = db["release"].branch
            stable_builders = await api.stable_builders(branch=release_branch)
            if not stable_builders:
                release_branch = "3.x"
                stable_builders = await api.stable_builders(branch="3.x")
            if not stable_builders:
                raise ReleaseException(
                    f"Failed to get the stable buildbots for the {release_branch} tag"
                )
            builders = await asyncio.gather(
                *[
                    _get_builder_status(api, the_builder)
                    for the_builder in stable_builders.values()
                ]
            )
            return {the_builder for (the_builder, is_failing) in builders if is_failing}

    failing_builders = asyncio.run(_check())
    if not failing_builders:
        return
    print()
    print("The following buildbots are failing:")
    for builder in failing_builders:
        print(f"- {builder.name}")
    print()
    print("Check https://buildbot.python.org/all/#/release_status for more information")
    print()
    if not ask_question("Do you want to continue even if these builders are failing?"):
        raise ReleaseException("Buildbots are failing!")


def check_docker_running(db: ReleaseShelf) -> None:
    subprocess.check_call(["docker", "container", "ls"])


def run_blurb_release(db: ReleaseShelf) -> None:
    subprocess.check_call(["blurb", "release", str(db["release"])], cwd=db["git_repo"])
    subprocess.check_call(
        ["git", "commit", "-m", f"Python {db['release']}"],
        cwd=db["git_repo"],
    )


def check_cpython_repo_age(db: ReleaseShelf) -> None:
    # %ct = committer date, UNIX timestamp (for example, "1768300016")
    timestamp = subprocess.check_output(
        shlex.split('git log -1 --format="%ct"'), text=True, cwd=db["git_repo"]
    ).strip()
    age_seconds = time.time() - int(timestamp.strip())
    is_old = age_seconds > 86400  # 1 day

    # cr = committer date, relative (for example, "3 days ago")
    out = subprocess.check_output(
        shlex.split('git log -1 --format="%cr"'), text=True, cwd=db["git_repo"]
    )
    print(f"Last CPython commit was {out.strip()}")

    if is_old and not ask_question("Continue with old repo?"):
        raise ReleaseException("CPython repository is old")


def check_cpython_repo_is_clean(db: ReleaseShelf) -> None:
    if subprocess.check_output(["git", "status", "--porcelain"], cwd=db["git_repo"]):
        raise ReleaseException("Git repository is not clean")


def check_magic_number(db: ReleaseShelf) -> None:
    release_tag = db["release"]
    if release_tag.is_final or release_tag.is_release_candidate:

        def out(msg: str) -> None:
            raise ReleaseException(msg)

    else:

        def out(msg: str) -> None:
            print("warning:", msg, file=sys.stderr, flush=True)

    def get_magic(source: Path, regex: re.Pattern[str]) -> str:
        if m := regex.search(source.read_text()):
            return m.group("magic")

        out(f"Cannot find magic in {source}, tried {regex.pattern}")
        return "unknown"

    work_dir = Path(db["git_repo"])
    magic_actual_file = work_dir / "Include" / "internal" / "pycore_magic_number.h"
    magic_actual_re = re.compile(
        r"^#define\s+PYC_MAGIC_NUMBER\s+(?P<magic>\d+)$", re.MULTILINE
    )
    magic_actual = get_magic(magic_actual_file, magic_actual_re)

    magic_expected_file = work_dir / "Lib" / "test" / "test_importlib" / "test_util.py"
    magic_expected_re = re.compile(
        r"^\s+EXPECTED_MAGIC_NUMBER = (?P<magic>\d+)$", re.MULTILINE
    )
    magic_expected = get_magic(magic_expected_file, magic_expected_re)

    if magic_actual == magic_expected:
        return

    out(
        f"Magic numbers in {magic_actual_file} ({magic_actual})"
        f" and {magic_expected_file} ({magic_expected}) don't match."
    )
    if not ask_question("Do you want to continue? This will fail tests in RC stage."):
        raise ReleaseException("Magic numbers don't match!")


def prepare_temporary_branch(db: ReleaseShelf) -> None:
    subprocess.check_call(
        ["git", "checkout", "-b", f"branch-{db['release']}"], cwd=db["git_repo"]
    )


def remove_temporary_branch(db: ReleaseShelf) -> None:
    subprocess.check_call(
        ["git", "branch", "-D", f"branch-{db['release']}"], cwd=db["git_repo"]
    )


def prepare_pydoc_topics(db: ReleaseShelf) -> None:
    subprocess.check_call(["make", "venv"], cwd=db["git_repo"] / "Doc")
    subprocess.check_call(["make", "pydoc-topics"], cwd=db["git_repo"] / "Doc")
    shutil.copy2(
        db["git_repo"] / "Doc" / "build" / "pydoc-topics" / "topics.py",
        db["git_repo"] / "Lib" / "pydoc_data" / "topics.py",
    )
    subprocess.check_call(
        ["git", "commit", "-a", "--amend", "--no-edit"], cwd=db["git_repo"]
    )


def run_autoconf(db: ReleaseShelf) -> None:
    # Python 3.12 and newer have a script that runs autoconf.
    regen_configure_sh = db["git_repo"] / "Tools/build/regen-configure.sh"
    if regen_configure_sh.exists():
        subprocess.check_call(
            [regen_configure_sh],
            cwd=db["git_repo"],
        )
    # Python 3.11 and prior rely on autoconf built within a container
    # in order to maintain stability of autoconf generation.
    else:
        # Corresponds to the tag '269' and 'cp311'
        cpython_autoconf_sha256 = (
            "f370fee95eefa3d57b00488bce4911635411fa83e2d293ced8cf8a3674ead939"
        )
        subprocess.check_call(
            [
                "docker",
                "run",
                "--rm",
                "--pull=always",
                f"-v{db['git_repo']}:/src",
                f"quay.io/tiran/cpython_autoconf@sha256:{cpython_autoconf_sha256}",
            ],
            cwd=db["git_repo"],
        )
        subprocess.check_call(["docker", "rmi", "quay.io/tiran/cpython_autoconf", "-f"])

    subprocess.check_call(
        ["git", "commit", "-a", "--amend", "--no-edit"], cwd=db["git_repo"]
    )


def check_pyspecific(db: ReleaseShelf) -> None:
    with open(
        db["git_repo"] / "Doc" / "tools" / "extensions" / "pyspecific.py"
    ) as pyspecific:
        for line in pyspecific:
            if "SOURCE_URI =" in line:
                break
    expected_branch = db["release"].branch
    expected = (
        f"SOURCE_URI = 'https://github.com/python/cpython/tree/{expected_branch}/%s'"
    )
    if expected != line.strip():
        raise ReleaseException(
            f"SOURCE_URI is incorrect (it needs changing before beta 1):\n"
            f"expected: {expected}\n"
            f"got     : {line.strip()}"
        )


def bump_version(db: ReleaseShelf) -> None:
    with cd(db["git_repo"]):
        release_mod.bump(db["release"])
    subprocess.check_call(
        ["git", "commit", "-a", "--amend", "--no-edit"], cwd=db["git_repo"]
    )


def bump_version_in_docs(db: ReleaseShelf) -> None:
    update_version_next.main([db["release"].doc_version, str(db["git_repo"])])
    subprocess.check_call(
        ["git", "commit", "-a", "--amend", "--no-edit"], cwd=db["git_repo"]
    )


def create_tag(db: ReleaseShelf) -> None:
    with cd(db["git_repo"]):
        if not release_mod.make_tag(db["release"], sign_gpg=db["sign_gpg"]):
            raise ReleaseException("Error when creating tag")
    subprocess.check_call(
        ["git", "commit", "-a", "--amend", "--no-edit"], cwd=db["git_repo"]
    )


def wait_for_build_release(db: ReleaseShelf) -> None:
    # Determine if we need to wait for docs.
    release_tag = db["release"]
    should_wait_for_docs = release_tag.includes_docs

    # Create the directory so it's easier to place the artifacts there.
    release_path = Path(db["git_repo"] / str(release_tag))
    downloads_path = release_path / "downloads"
    downloads_path.mkdir(parents=True, exist_ok=True)

    # Build the list of filepaths we're expecting.
    wait_for_paths = [
        downloads_path / f"Python-{release_tag}.tgz",
        downloads_path / f"Python-{release_tag}.tar.xz",
    ]
    if release_tag.as_tuple() >= (3, 14):
        wait_for_paths += [
            downloads_path / f"python-{release_tag}-{arch}-linux-android.tar.gz"
            for arch in ["aarch64", "x86_64"]
        ]
    if should_wait_for_docs:
        docs_path = release_path / "docs"
        docs_path.mkdir(parents=True, exist_ok=True)
        wait_for_paths.extend(
            [
                docs_path / f"python-{release_tag}-docs.epub",
                docs_path / f"python-{release_tag}-docs-html.tar.bz2",
                docs_path / f"python-{release_tag}-docs-html.zip",
                docs_path / f"python-{release_tag}-docs-texinfo.tar.bz2",
                docs_path / f"python-{release_tag}-docs-texinfo.zip",
                docs_path / f"python-{release_tag}-docs-text.tar.bz2",
                docs_path / f"python-{release_tag}-docs-text.zip",
            ]
        )

    print("Once the build-release workflow is complete:")
    print("- Download its artifacts from the workflow summary page.")
    print(f"- Copy the following files into {release_path}:")
    for path in wait_for_paths:
        print(f"  - {os.path.relpath(path, release_path)}")
    print("The script will continue once all files are present.")

    while not all(path.exists() for path in wait_for_paths):
        time.sleep(1)


def check_doc_unreleased_version(db: ReleaseShelf) -> None:
    print("Checking built docs for '(unreleased)'")
    # This string is generated when a `versionadded:: next` directive is
    # left in the docs, which means the `bump_version_in_docs` step
    # didn't do its job.
    # But, there could also be a false positive.
    release_tag = db["release"]
    docs_path = Path(db["git_repo"]) / str(release_tag) / "docs"
    archive_path = docs_path / f"python-{release_tag}-docs-html.tar.bz2"
    if release_tag.includes_docs:
        assert archive_path.exists()
    if archive_path.exists():
        with tempfile.TemporaryDirectory() as temp_dir:
            subprocess.run(["tar", "-xjf", archive_path, "-C", temp_dir])
            proc = subprocess.run(["grep", "-rHn", "[(]unreleased[)]", temp_dir])
            if proc.returncode == 0:
                if not ask_question(
                    "Are these `(unreleased)` strings in built docs OK?"
                ):
                    raise AssertionError("`(unreleased)` strings found in docs")


def sign_source_artifacts(db: ReleaseShelf) -> None:
    print("Signing tarballs with GPG")
    uid = os.environ.get("GPG_KEY_FOR_RELEASE")
    if not uid:
        print("List of available private keys:")
        subprocess.check_call('gpg -K | grep -A 1 "^sec"', shell=True)
        uid = input("Please enter key ID to use for signing: ")

    tarballs_path = Path(db["git_repo"] / str(db["release"]) / "downloads")
    tgz = str(tarballs_path / f"Python-{db['release']}.tgz")
    xz = str(tarballs_path / f"Python-{db['release']}.tar.xz")

    subprocess.check_call(["gpg", "-bas", "-u", uid, tgz])
    subprocess.check_call(["gpg", "-bas", "-u", uid, xz])

    print("Signing tarballs with Sigstore")
    for filename in (tgz, xz):
        cert_file = filename + ".crt"
        sig_file = filename + ".sig"
        bundle_file = filename + ".sigstore"

        subprocess.check_call(
            [
                sys.executable,
                "-m",
                "sigstore",
                "sign",
                "--oidc-disable-ambient-providers",
                "--signature",
                sig_file,
                "--certificate",
                cert_file,
                "--bundle",
                bundle_file,
                filename,
            ]
        )


def build_sbom_artifacts(db: ReleaseShelf) -> None:

    # Skip building an SBOM if there isn't a 'Misc/sbom.spdx.json' file.
    if not (db["git_repo"] / "Misc/sbom.spdx.json").exists():
        print("Skipping building an SBOM, missing 'Misc/sbom.spdx.json'")
        return

    release_version = db["release"]
    # For each source tarball build an SBOM.
    for ext in (".tgz", ".tar.xz"):
        tarball_name = f"Python-{release_version}{ext}"
        tarball_path = str(
            db["git_repo"] / str(db["release"]) / "downloads" / tarball_name
        )

        print(f"Building an SBOM for artifact '{tarball_name}'")
        sbom_data = sbom.create_sbom_for_source_tarball(tarball_path)

        with open(tarball_path + ".spdx.json", mode="w") as f:
            f.write(json.dumps(sbom_data, indent=2, sort_keys=True))


class MySFTPClient(paramiko.SFTPClient):
    def put_dir(
        self, source: str | Path, target: str | Path, progress: Any = None
    ) -> None:
        for item in os.listdir(source):
            if os.path.isfile(os.path.join(source, item)):
                progress.text(item)
                self.put(os.path.join(source, item), f"{target}/{item}")
                progress()
            else:
                self.mkdir(f"{target}/{item}", ignore_existing=True)
                self.put_dir(
                    os.path.join(source, item),
                    f"{target}/{item}",
                    progress=progress,
                )

    def mkdir(
        self, path: bytes | str, mode: int = 511, ignore_existing: bool = False
    ) -> None:
        try:
            super().mkdir(path, mode)
        except OSError:
            if ignore_existing:
                pass
            else:
                raise


def upload_files_to_server(db: ReleaseShelf, server: str) -> None:
    client = paramiko.SSHClient()
    client.load_system_host_keys()
    client.set_missing_host_key_policy(paramiko.WarningPolicy)
    client.connect(server, port=22, username=db["ssh_user"], key_filename=db["ssh_key"])
    transport = client.get_transport()
    assert transport is not None, f"SSH transport to {server} is None"

    destination = Path(f"/home/psf-users/{db['ssh_user']}/{db['release']}")
    ftp_client = MySFTPClient.from_transport(transport)
    assert ftp_client is not None, f"SFTP client to {server} is None"

    client.exec_command(f"rm -rf {destination}")

    with contextlib.suppress(OSError):
        ftp_client.mkdir(str(destination))

    artifacts_path = Path(db["git_repo"] / str(db["release"]))

    shutil.rmtree(artifacts_path / f"Python-{db['release']}", ignore_errors=True)

    def upload_subdir(subdir: str) -> None:
        with contextlib.suppress(OSError):
            ftp_client.mkdir(str(destination / subdir))
        with alive_bar(len(tuple((artifacts_path / subdir).glob("**/*")))) as progress:
            ftp_client.put_dir(
                artifacts_path / subdir,
                str(destination / subdir),
                progress=progress,
            )

    if server == DOCS_SERVER:
        upload_subdir("docs")
    elif server == DOWNLOADS_SERVER:
        upload_subdir("downloads")
        if (artifacts_path / "docs").exists():
            upload_subdir("docs")

    ftp_client.close()


def upload_files_to_downloads_server(db: ReleaseShelf) -> None:
    upload_files_to_server(db, DOWNLOADS_SERVER)


def place_files_in_download_folder(db: ReleaseShelf) -> None:
    client = paramiko.SSHClient()
    client.load_system_host_keys()
    client.set_missing_host_key_policy(paramiko.WarningPolicy)
    client.connect(
        DOWNLOADS_SERVER, port=22, username=db["ssh_user"], key_filename=db["ssh_key"]
    )
    transport = client.get_transport()
    assert transport is not None, f"SSH transport to {DOWNLOADS_SERVER} is None"

    # Downloads

    source = f"/home/psf-users/{db['ssh_user']}/{db['release']}"
    destination = f"/srv/www.python.org/ftp/python/{db['release'].normalized()}"

    def execute_command(command: str) -> None:
        channel = transport.open_session()
        channel.exec_command(command)
        if channel.recv_exit_status() != 0:
            raise ReleaseException(channel.recv_stderr(1000))

    execute_command(f"mkdir -p {destination}")
    execute_command(f"cp {source}/downloads/* {destination}")
    execute_command(f"chgrp downloads {destination}")
    execute_command(f"chmod 775 {destination}")
    execute_command(f"find {destination} -type f -exec chmod 664 {{}} \\;")

    # Docs

    release_tag = db["release"]
    if release_tag.is_final or release_tag.is_release_candidate:
        source = f"/home/psf-users/{db['ssh_user']}/{db['release']}"
        destination = f"/srv/www.python.org/ftp/python/doc/{release_tag}"

        execute_command(f"mkdir -p {destination}")
        execute_command(f"cp {source}/docs/* {destination}")
        execute_command(f"chgrp downloads {destination}")
        execute_command(f"chmod 775 {destination}")
        execute_command(f"find {destination} -type f -exec chmod 664 {{}} \\;")


def upload_docs_to_the_docs_server(db: ReleaseShelf) -> None:
    release_tag: release_mod.Tag = db["release"]
    if not (release_tag.is_final or release_tag.is_release_candidate):
        return

    upload_files_to_server(db, DOCS_SERVER)


def unpack_docs_in_the_docs_server(db: ReleaseShelf) -> None:
    release_tag: release_mod.Tag = db["release"]
    if not (release_tag.is_final or release_tag.is_release_candidate):
        return

    client = paramiko.SSHClient()
    client.load_system_host_keys()
    client.set_missing_host_key_policy(paramiko.WarningPolicy)
    client.connect(
        DOCS_SERVER, port=22, username=db["ssh_user"], key_filename=db["ssh_key"]
    )
    transport = client.get_transport()
    assert transport is not None, f"SSH transport to {DOCS_SERVER} is None"

    # Sources

    source = f"/home/psf-users/{db['ssh_user']}/{db['release']}"
    destination = f"/srv/docs.python.org/release/{release_tag}"

    def execute_command(command: str) -> None:
        channel = transport.open_session()
        channel.exec_command(command)
        if channel.recv_exit_status() != 0:
            raise ReleaseException(channel.recv_stderr(1000))

    docs_filename = f"python-{release_tag}-docs-html"
    execute_command(f"mkdir -p {destination}")
    execute_command(f"unzip {source}/docs/{docs_filename}.zip -d {destination}")
    execute_command(f"mv /{destination}/{docs_filename}/* {destination}")
    execute_command(f"rm -rf /{destination}/{docs_filename}")
    execute_command(f"chgrp -R docs {destination}")
    execute_command(f"chmod -R 775 {destination}")
    execute_command(f"find {destination} -type f -exec chmod 664 {{}} \\;")


@functools.cache
def extract_github_owner(url: str) -> str:
    if https_match := re.match(r"(https://)?github\.com/([^/]+)/", url):
        return https_match.group(2)
    elif ssh_match := re.match(r"^git@github\.com:([^/]+)/", url):
        return ssh_match.group(1)
    else:
        raise ReleaseException(
            f"Could not parse GitHub owner from 'origin' remote URL: {url}"
        )


@functools.cache
def get_commit_sha(git_version: str, git_repo: Path) -> str:
    """Get the Git commit SHA for the tag"""
    commit_sha = (
        subprocess.check_output(
            ["git", "rev-list", "-n", "1", git_version], cwd=git_repo
        )
        .decode()
        .strip()
    )
    return commit_sha


@functools.cache
def get_origin_remote_url(git_repo: Path) -> str:
    """Get the owner of the GitHub repo (first path segment in a 'github.com' remote URL)
    This works for both 'https' and 'ssh' style remote URLs."""
    origin_remote_url = (
        subprocess.check_output(
            ["git", "ls-remote", "--get-url", "origin"], cwd=git_repo
        )
        .decode()
        .strip()
    )
    return origin_remote_url


def start_build_release(db: ReleaseShelf) -> None:
    commit_sha = get_commit_sha(db["release"].gitname, db["git_repo"])
    origin_remote_url = get_origin_remote_url(db["git_repo"])
    origin_remote_github_owner = extract_github_owner(origin_remote_url)
    # We ask for human verification at this point since this commit SHA is 'locked in'
    print()
    print(
        f"Go to https://github.com/{origin_remote_github_owner}/cpython/commit/{commit_sha}"
    )
    print("- Ensure that the commit diff does not contain any unexpected changes.")
    print(
        "- For the next step, ensure the commit SHA matches the one you verified on GitHub in this step."
    )
    print()
    if not ask_question(
        "Have you verified the release commit hasn't been tampered with on GitHub?"
    ):
        raise ReleaseException("Commit must be visually reviewed before starting build")

    # After visually confirming the release manager can start the build process
    # with the known good commit SHA.
    print()
    cmd = (
        "gh workflow run build-release.yml --repo python/release-tools"
        f" -f git_remote={origin_remote_github_owner}"
        f" -f git_commit={commit_sha}"
        f" -f cpython_release={db['release']}"
    )
    subprocess.check_call(shlex.split(cmd))
    print(
        "Go to https://github.com/python/release-tools/actions/workflows/build-release.yml"
    )
    print()

    if not ask_question("Have you started the build-release workflow?"):
        raise ReleaseException("build-release workflow must be started")


def send_email_to_platform_release_managers(db: ReleaseShelf) -> None:
    commit_sha = get_commit_sha(db["release"].gitname, db["git_repo"])
    origin_remote_url = get_origin_remote_url(db["git_repo"])
    origin_remote_github_owner = extract_github_owner(origin_remote_url)
    github_prefix = f"https://github.com/{origin_remote_github_owner}/cpython/tree"

    print()
    print(f"{github_prefix}/{db['release'].gitname}")
    print(f"Git commit SHA: {commit_sha}")
    print(
        "build-release workflow: https://github.com/python/release-tools/actions/runs/[ENTER-RUN-ID-HERE]"
    )
    print()

    if not ask_question(
        "Have you notified the platform release managers about the availability of the commit SHA and tag?"
    ):
        raise ReleaseException("Platform release managers must be notified")


def create_release_object_in_db(db: ReleaseShelf) -> None:
    print(
        "Go to https://www.python.org/admin/downloads/release/add/ and create a new release"
    )
    if not ask_question(f"Have you already created a new release for {db['release']}?"):
        raise ReleaseException("The Django release object has not been created")


def wait_until_all_files_are_in_folder(db: ReleaseShelf) -> None:
    client = paramiko.SSHClient()
    client.load_system_host_keys()
    client.set_missing_host_key_policy(paramiko.WarningPolicy)
    client.connect(
        DOWNLOADS_SERVER, port=22, username=db["ssh_user"], key_filename=db["ssh_key"]
    )
    ftp_client = client.open_sftp()

    destination = f"/srv/www.python.org/ftp/python/{db['release'].normalized()}"

    are_all_files_there = False
    release = str(db["release"])
    print()
    while not are_all_files_there:
        try:
            all_files = set(ftp_client.listdir(destination))
        except FileNotFoundError:
            raise FileNotFoundError(
                f"The release folder in {destination} has not been created"
            ) from None
        are_windows_files_there = f"python-{release}.exe" in all_files
        are_macos_files_there = f"python-{release}-macos11.pkg" in all_files
        are_linux_files_there = f"Python-{release}.tgz" in all_files

        if db["security_release"]:
            # For security releases, only check Linux files
            are_all_files_there = are_linux_files_there
        else:
            # For regular releases, check all platforms
            are_all_files_there = (
                are_linux_files_there
                and are_windows_files_there
                and are_macos_files_there
            )

        if not are_all_files_there:
            linux_tick = "✅" if are_linux_files_there else "❌"
            windows_tick = "✅" if are_windows_files_there else "❌"
            macos_tick = "✅" if are_macos_files_there else "❌"

            if db["security_release"]:
                waiting = f"\rWaiting for files: Linux {linux_tick} (security release - only checking Linux)"
            else:
                waiting = f"\rWaiting for files: Linux {linux_tick}  Windows {windows_tick}  Mac {macos_tick} "

            print(waiting, flush=True, end="")
            time.sleep(1)
    print()


def run_add_to_python_dot_org(db: ReleaseShelf) -> None:
    client = paramiko.SSHClient()
    client.load_system_host_keys()
    client.set_missing_host_key_policy(paramiko.WarningPolicy)
    client.connect(
        DOWNLOADS_SERVER, port=22, username=db["ssh_user"], key_filename=db["ssh_key"]
    )
    transport = client.get_transport()
    assert transport is not None, f"SSH transport to {DOWNLOADS_SERVER} is None"

    # Ensure the file is there
    source = Path(__file__).parent / "add_to_pydotorg.py"
    destination = Path(f"/home/psf-users/{db['ssh_user']}/add_to_pydotorg.py")
    ftp_client = MySFTPClient.from_transport(transport)
    assert ftp_client is not None, f"SFTP client to {DOWNLOADS_SERVER} is None"
    ftp_client.put(str(source), str(destination))
    ftp_client.close()

    auth_info = db["auth_info"]
    assert auth_info is not None

    # Do the interactive flow to get an identity for Sigstore
    issuer = sigstore.oidc.Issuer(sigstore.oidc.DEFAULT_OAUTH_ISSUER_URL)
    identity_token = issuer.identity_token()

    print("Adding files to python.org...")
    stdin, stdout, stderr = client.exec_command(
        f"AUTH_INFO={auth_info} SIGSTORE_IDENTITY_TOKEN={identity_token} python3 add_to_pydotorg.py {db['release']}"
    )
    stderr_text = stderr.read().decode()
    if stderr_text:
        raise paramiko.SSHException(f"Failed to execute the command: {stderr_text}")
    stdout_text = stdout.read().decode()
    print("-- Command output --")
    print(stdout_text)
    print("-- End of command output --")


def purge_the_cdn(db: ReleaseShelf) -> None:
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows; U; Windows NT 6.1; en-US; rv:1.9.1.6) Gecko/20091201 Firefox/3.5.6"
    }
    normalized_release = db["release"].normalized()
    urls = [
        f"https://www.python.org/downloads/release/python-{str(db['release']).replace('.', '')}/",
        f"https://docs.python.org/release/{db['release']}/",
        f"https://www.python.org/ftp/python/{normalized_release}/",
        f"https://docs.python.org/release/{normalized_release}/",
        "https://www.python.org/downloads/",
        "https://www.python.org/downloads/windows/",
        "https://www.python.org/downloads/macos/",
    ]
    # Purge the source URLs and their associated metadata files.
    source_urls = [
        f"https://www.python.org/ftp/python/{normalized_release}/Python-{db['release']}.tgz",
        f"https://www.python.org/ftp/python/{normalized_release}/Python-{db['release']}.tar.xz",
    ]
    for source_url in source_urls:
        urls.extend(
            [
                f"{source_url}",
                f"{source_url}.asc",
                f"{source_url}.crt",
                f"{source_url}.sig",
                f"{source_url}.sigstore",
                f"{source_url}.spdx.json",
            ]
        )

    for url in urls:
        req = urllib.request.Request(url=url, headers=headers, method="PURGE")
        # try:
        response = urllib.request.urlopen(req)
        if response.code != 200:
            raise RuntimeError("Failed to purge the python.org/downloads CDN")


def announce_release(db: ReleaseShelf) -> None:
    if not ask_question(
        "Have you announced the release at https://discuss.python.org/c/core-dev/23 "
        "and https://www.blogger.com?\n"
        "Tip: use the 'release' tag and 'releases' label respectively."
    ):
        raise ReleaseException("The release has not been announced")


def post_release_merge(db: ReleaseShelf) -> None:
    subprocess.check_call(
        ["git", "fetch", "--all"],
        cwd=db["git_repo"],
    )

    release_tag: release_mod.Tag = db["release"]
    if release_tag.is_feature_freeze_release:
        subprocess.check_call(
            ["git", "checkout", "main"],
            cwd=db["git_repo"],
        )
    else:
        subprocess.check_call(
            ["git", "checkout", release_tag.branch],
            cwd=db["git_repo"],
        )

    subprocess.check_call(
        ["git", "merge", "--no-squash", f"v{db['release']}"],
        cwd=db["git_repo"],
    )


def post_release_tagging(db: ReleaseShelf) -> None:
    release_tag: release_mod.Tag = db["release"]

    subprocess.check_call(
        ["git", "fetch", "--all"],
        cwd=db["git_repo"],
    )

    subprocess.check_call(
        ["git", "checkout", release_tag.branch],
        cwd=db["git_repo"],
    )

    with cd(db["git_repo"]):
        release_mod.done(db["release"])

    subprocess.check_call(
        ["git", "commit", "-a", "-m", f"Post {db['release']}"],
        cwd=db["git_repo"],
    )


def maybe_prepare_new_main_branch(db: ReleaseShelf) -> None:
    release_tag: release_mod.Tag = db["release"]

    if not release_tag.is_feature_freeze_release:
        return

    subprocess.check_call(
        ["git", "checkout", "main"],
        cwd=db["git_repo"],
    )

    new_release = release_tag.next_minor_release()
    with cd(db["git_repo"]):
        release_mod.bump(new_release)

    prev_branch = f"{release_tag.major}.{release_tag.minor}"
    new_branch = f"{release_tag.major}.{int(release_tag.minor)+1}"
    whatsnew_file = f"Doc/whatsnew/{new_branch}.rst"
    with cd(db["git_repo"]), open(whatsnew_file, "w") as f:
        f.write(WHATS_NEW_TEMPLATE.format(version=new_branch, prev_version=prev_branch))

    subprocess.check_call(
        ["git", "add", whatsnew_file],
        cwd=db["git_repo"],
    )

    whatsnew_toctree_file = "Doc/whatsnew/index.rst"
    with cd(db["git_repo"]):
        update_whatsnew_toctree(db, whatsnew_toctree_file)

    subprocess.check_call(
        ["git", "add", whatsnew_toctree_file],
        cwd=db["git_repo"],
    )

    subprocess.check_call(
        ["git", "commit", "-a", "-m", f"Python {new_release}"],
        cwd=db["git_repo"],
    )


def update_whatsnew_toctree(db: ReleaseShelf, filename: str) -> None:
    release_tag: release_mod.Tag = db["release"]
    this_rst = f"   {release_tag.major}.{release_tag.minor}.rst"
    next_rst = f"   {release_tag.major}.{release_tag.minor+1}.rst"
    new = next_rst + "\n" + this_rst

    with open(filename) as f:
        contents = f.read()
    contents = contents.replace(this_rst, new)
    with open(filename, "w") as f:
        f.write(contents)


def branch_new_versions(db: ReleaseShelf) -> None:
    release_tag: release_mod.Tag = db["release"]

    if not release_tag.is_feature_freeze_release:
        return

    subprocess.check_call(["git", "checkout", "main"], cwd=db["git_repo"])

    subprocess.check_call(
        ["git", "checkout", "-b", release_tag.branch],
        cwd=db["git_repo"],
    )


def is_mirror(repo: Path, remote: str) -> bool:
    """Return True if the `repo` directory was created with --mirror."""

    cmd = ["git", "config", "--local", "--get", f"remote.{remote}.mirror"]
    try:
        out = subprocess.check_output(cmd, cwd=repo)
    except subprocess.CalledProcessError:
        return False
    return out.startswith(b"true")


def push_to_local_fork(db: ReleaseShelf) -> None:
    def _push_to_local(dry_run: bool = False) -> None:
        git_command = ["git", "push"]
        if dry_run:
            git_command.append("--dry-run")

        git_command.append("origin")
        if not is_mirror(db["git_repo"], "origin"):
            # mirrors push everything always, specifying `--tags` or refspecs doesn't work.
            git_command += ["HEAD", "--tags"]

        subprocess.check_call(
            git_command,
            cwd=db["git_repo"],
        )

    _push_to_local(dry_run=True)
    if not ask_question(
        "Does these operations look reasonable? ⚠️⚠️⚠️ Answering 'yes' will push to your origin remote ⚠️⚠️⚠️"
    ):
        raise ReleaseException("Something is wrong - Push to remote aborted")
    _push_to_local(dry_run=False)


def push_to_upstream(db: ReleaseShelf) -> None:
    release_tag: release_mod.Tag = db["release"]

    def _push_to_upstream(dry_run: bool = False) -> None:
        branch = f"{release_tag.major}.{release_tag.minor}"
        git_command = ["git", "push"]
        if dry_run:
            git_command.append("--dry-run")

        if release_tag.is_alpha_release:
            subprocess.check_call(
                git_command + ["--tags", "git@github.com:python/cpython.git", "main"],
                cwd=db["git_repo"],
            )
        elif release_tag.is_feature_freeze_release:
            subprocess.check_call(
                git_command + ["--tags", "git@github.com:python/cpython.git", branch],
                cwd=db["git_repo"],
            )
            subprocess.check_call(
                git_command + ["--tags", "git@github.com:python/cpython.git", "main"],
                cwd=db["git_repo"],
            )
        else:
            subprocess.check_call(
                git_command + ["--tags", "git@github.com:python/cpython.git", branch],
                cwd=db["git_repo"],
            )

    _push_to_upstream(dry_run=True)
    if not ask_question(
        "Do these operations look reasonable? ⚠️⚠️⚠️ Answering 'yes' will push to the upstream repository ⚠️⚠️⚠️"
    ):
        raise ReleaseException("Something is wrong - Push to upstream aborted")
    if not ask_question(
        "Is the target branch unprotected for your user? "
        "Check at https://github.com/python/cpython/settings/branches"
    ):
        raise ReleaseException("The target branch is not unprotected for your user")
    _push_to_upstream(dry_run=False)


def main() -> None:

    parser = argparse.ArgumentParser(description="Make a CPython release.")

    def _release_type(release: str) -> str:
        if not RELEASE_REGEXP.match(release):
            raise argparse.ArgumentTypeError("Invalid release string")
        return release

    parser.add_argument(
        "--release",
        dest="release",
        help="Release tag",
        required=True,
        type=_release_type,
    )
    parser.add_argument(
        "--repository",
        dest="repo",
        help="Location of the CPython repository",
        required=True,
        type=str,
    )

    def _api_key(api_key: str) -> str:
        if not API_KEY_REGEXP.match(api_key):
            raise argparse.ArgumentTypeError(
                "Invalid API key format. It must be on the form USER:API_KEY"
            )
        return api_key

    parser.add_argument(
        "--auth-key",
        dest="auth_key",
        help="API key for python.org in the form 'USER:API_KEY'",
        type=_api_key,
    )
    parser.add_argument(
        "--ssh-user",
        dest="ssh_user",
        default=getpass.getuser(),
        help="Username to be used when authenticating via ssh",
        type=str,
    )
    parser.add_argument(
        "--ssh-key",
        dest="ssh_key",
        default=None,
        help="Path to the SSH key file to use for authentication",
        type=str,
    )
    args = parser.parse_args()

    auth_key = args.auth_key or os.getenv("AUTH_INFO")
    assert isinstance(auth_key, str), "We need an AUTH_INFO env var or --auth-key"

    if sys.platform not in ("darwin", "linux"):
        print(
            """\
WARNING! This script has not been tested on a platform other than Linux and macOS.

Although it should work correctly as long as you have all the dependencies,
some things may not work as expected. As a release manager, you should try to
fix these things in this script so it also supports your platform.
"""
        )
        if not ask_question("Do you want to continue?"):
            raise ReleaseException(
                "This release script is not compatible with the running platform"
            )

    release_tag = release_mod.Tag(args.release)
    magic = release_tag.as_tuple() >= (3, 14)
    no_gpg = release_tag.as_tuple() >= (3, 14)  # see PEP 761
    tasks = [
        Task(check_gh, "Checking gh is available"),
        Task(check_git, "Checking Git is available"),
        Task(check_make, "Checking make is available"),
        Task(check_blurb, "Checking blurb is available"),
        Task(check_docker, "Checking Docker is available"),
        Task(check_docker_running, "Checking Docker is running"),
        Task(check_autoconf, "Checking autoconf is available"),
        *([] if no_gpg else [Task(check_gpg_keys, "Checking GPG keys")]),
        Task(
            check_ssh_connection,
            f"Validating ssh connection to {DOWNLOADS_SERVER} and {DOCS_SERVER}",
        ),
        Task(check_sigstore_client, "Checking Sigstore CLI"),
        Task(check_buildbots, "Check buildbots are good"),
        Task(check_cpython_repo_age, "Checking CPython repository age"),
        Task(check_cpython_repo_is_clean, "Checking Git repository is clean"),
        *(
            [Task(check_magic_number, "Checking the magic number is up-to-date")]
            if magic
            else []
        ),
        Task(prepare_temporary_branch, "Checking out a temporary release branch"),
        Task(run_blurb_release, "Run blurb release"),
        Task(check_cpython_repo_is_clean, "Checking Git repository is clean"),
        Task(prepare_pydoc_topics, "Preparing pydoc topics"),
        Task(bump_version, "Bump version"),
        Task(bump_version_in_docs, "Bump version in docs"),
        Task(check_cpython_repo_is_clean, "Checking Git repository is clean"),
        Task(run_autoconf, "Running autoconf"),
        Task(check_cpython_repo_is_clean, "Checking Git repository is clean"),
        Task(check_pyspecific, "Checking pyspecific"),
        Task(check_cpython_repo_is_clean, "Checking Git repository is clean"),
        Task(create_tag, "Create tag"),
        Task(push_to_local_fork, "Push new tags and branches to private fork"),
        Task(start_build_release, "Start the build-release workflow"),
        Task(
            send_email_to_platform_release_managers,
            "Platform release managers have been notified of the commit SHA",
        ),
        Task(wait_for_build_release, "Wait for build-release workflow"),
        Task(check_doc_unreleased_version, "Check docs for `(unreleased)`"),
        Task(build_sbom_artifacts, "Building SBOM artifacts"),
        *([] if no_gpg else [Task(sign_source_artifacts, "Sign source artifacts")]),
        Task(
            upload_files_to_downloads_server, "Upload files to the PSF downloads server"
        ),
        Task(place_files_in_download_folder, "Place files in the download folder"),
        Task(upload_docs_to_the_docs_server, "Upload docs to the PSF docs server"),
        Task(unpack_docs_in_the_docs_server, "Place docs files in the docs folder"),
        Task(wait_until_all_files_are_in_folder, "Wait until all files are ready"),
        Task(create_release_object_in_db, "The Django release object has been created"),
        Task(post_release_merge, "Merge the tag into the release branch"),
        Task(branch_new_versions, "Branch out new versions and prepare main branch"),
        Task(post_release_tagging, "Final touches for the release"),
        Task(
            maybe_prepare_new_main_branch,
            "prepare new main branch for feature freeze",
        ),
        Task(push_to_upstream, "Push new tags and branches to upstream"),
        Task(remove_temporary_branch, "Removing temporary release branch"),
        Task(run_add_to_python_dot_org, "Add files to python.org download page"),
        Task(purge_the_cdn, "Purge the CDN of python.org/downloads"),
        Task(announce_release, "Announce the release"),
    ]
    automata = ReleaseDriver(
        git_repo=args.repo,
        release_tag=release_tag,
        api_key=auth_key,
        ssh_user=args.ssh_user,
        sign_gpg=not no_gpg,
        ssh_key=args.ssh_key,
        tasks=tasks,
    )
    automata.run()


if __name__ == "__main__":
    main()
