#!/usr/bin/env python3

"""An automatic engine for Python releases

Original code by Pablo Galindo
"""

import argparse
import asyncio
import builtins
import contextlib
import functools
import getpass
import itertools
import os
import pathlib
import re
import shelve
import shutil
import subprocess
import tempfile
import time
import urllib.request
import sys
from dataclasses import dataclass
from shelve import DbfilenameShelf
from typing import Callable, Iterator, List, Optional

import aiohttp
import gnupg
import paramiko
from alive_progress import alive_bar

import release as release_mod
from buildbotapi import BuildBotAPI

API_KEY_REGEXP = re.compile(r"(?P<major>\w+):(?P<minor>\w+)")


RELEASE_REGEXP = re.compile(
    r"(?P<major>\d+)\.(?P<minor>\d+)\.(?P<patch>\d+)\.?(?P<extra>.*)?"
)

DOWNLOADS_SERVER = "downloads.nyc1.psf.io"
DOCS_SERVER = "docs.nyc1.psf.io"

WHATS_NEW_TEMPLATE = """
****************************
  What's New In Python {version}
****************************

:Release: |release|
:Date: |today|

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

   * It's helpful to add the bug/patch number as a comment:

   XXX Describe the transmogrify() function added to the socket
   module.
   (Contributed by P.Y. Developer in :issue:`12345`.)

   This saves the maintainer the effort of going through the Mercurial log
   when researching a change.

This article explains the new features in Python {version}, compared to 3.10.

For full details, see the :ref:`changelog <changelog>`.

.. note::

   Prerelease users should be aware that this document is currently in draft
   form. It will be updated substantially as Python {version} moves towards release,
   so it's worth checking back even after reading earlier versions.


Summary -- Release highlights
=============================

.. This section singles out the most important changes in Python {version}.
   Brevity is key.


.. PEP-sized items next.



New Features
============



Other Language Changes
======================



New Modules
===========

* None yet.


Improved Modules
================


Optimizations
=============




Build and C API Changes
=======================

* :c:func:`PyFrame_BlockSetup` and :c:func:`PyFrame_BlockPop` have been removed.

Deprecated
==========



Removed
=======



Porting to Python {version}
======================

This section lists previously described changes and other bugfixes
that may require changes to your code.
"""


@dataclass
class Task:
    function: Callable[[DbfilenameShelf], None]
    description: str

    def __call__(self, db: DbfilenameShelf) -> None:
        return getattr(self, "function")(db)


class ReleaseException(Exception):
    """An error happened in the release process"""


class ReleaseDriver:
    def __init__(
        self,
        tasks: List[Task],
        *,
        release_tag: release_mod.Tag,
        git_repo: str,
        api_key: str,
        ssh_user: str,
        first_state: Optional[Task] = None,
    ) -> None:
        self.tasks = tasks
        dbfile = pathlib.Path.home() / ".python_release"
        self.db = shelve.open(str(dbfile), "c")
        if not self.db.get("finished"):
            self.db["finished"] = False
        else:
            self.db.close()
            self.db = shelve.open(str(dbfile), "n")

        self.current_task: Optional[Task] = first_state
        self.completed_tasks = self.db.get("completed_tasks", [])
        self.remaining_tasks = iter(tasks[len(self.completed_tasks) :])
        if self.db.get("gpg_key"):
            os.environ["GPG_KEY_FOR_RELEASE"] = self.db["gpg_key"]
        if not self.db.get("git_repo"):
            self.db["git_repo"] = pathlib.Path(git_repo)
        if not self.db.get("auth_info"):
            self.db["auth_info"] = api_key
        if not self.db.get("ssh_user"):
            self.db["ssh_user"] = ssh_user

        if not self.db.get("release"):
            self.db["release"] = release_tag

        print("Release data: ")
        print(f"- Branch: {release_tag.branch}")
        print(f"- Release tag: {self.db['release']}")
        print(f"- Normalized release tag: {release_tag.normalized()}")
        print(f"- Git repo: {self.db['git_repo']}")
        print(f"- SSH username: {self.db['ssh_user']}")
        print(f"- python.org API key : {self.db['auth_info']}")
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


def ask_question(question: str) -> bool:
    answer = ""
    print(question)
    while answer not in ("yes", "no"):
        answer = input("Enter yes or no: ")
        if answer == "yes":
            return True
        elif answer == "no":
            return False
        else:
            print("Please enter yes or no.")
    return True


@contextlib.contextmanager
def cd(path: str) -> Iterator[None]:
    current_path = os.getcwd()
    os.chdir(path)
    yield
    os.chdir(current_path)


@contextlib.contextmanager
def supress_print():
    print_func = builtins.print
    builtins.print = lambda *args, **kwargs: None
    yield
    builtins.print = print_func


def check_tool(db: DbfilenameShelf, tool: str) -> None:
    if shutil.which(tool) is None:
        raise ReleaseException(f"{tool} is not available")


check_git = functools.partial(check_tool, tool="git")
check_latexmk = functools.partial(check_tool, tool="latexmk")
check_make = functools.partial(check_tool, tool="make")
check_blurb = functools.partial(check_tool, tool="blurb")
check_autoconf = functools.partial(check_tool, tool="autoconf")
check_docker = functools.partial(check_tool, tool="docker")


def check_gpg_keys(db: DbfilenameShelf) -> None:
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


def check_ssh_connection(db: DbfilenameShelf) -> None:
    client = paramiko.SSHClient()
    client.load_system_host_keys()
    client.set_missing_host_key_policy(paramiko.WarningPolicy)
    client.connect(DOWNLOADS_SERVER, port=22, username=db["ssh_user"])
    client.exec_command("pwd")
    client.connect(DOCS_SERVER, port=22, username=db["ssh_user"])
    client.exec_command("pwd")


def check_buildbots(db: DbfilenameShelf) -> None:
    async def _check():
        async def _get_builder_status(buildbot_api, the_builder):
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


def run_blurb_release(db: DbfilenameShelf) -> None:
    subprocess.check_call(["blurb", "release", str(db["release"])], cwd=db["git_repo"])
    subprocess.check_call(
        ["git", "commit", "-m", f"Python {db['release']}"],
        cwd=db["git_repo"],
    )


def check_docs(db: DbfilenameShelf) -> None:
    subprocess.check_call(["make", "venv"], cwd=db["git_repo"] / "Doc")
    subprocess.check_call(
        ["make", "suspicious"],
        cwd=db["git_repo"] / "Doc",
        env={**os.environ, "SPHINXOPTS": "-j10"},
    )


def check_cpython_repo_is_clean(db: DbfilenameShelf) -> None:
    if subprocess.check_output(["git", "status", "--porcelain"], cwd=db["git_repo"]):
        raise ReleaseException("Git repository is not clean")


def preapre_temporary_branch(db: DbfilenameShelf) -> None:
    subprocess.check_call(
        ["git", "checkout", "-b", f"branch-{db['release']}"], cwd=db["git_repo"]
    )


def remove_temporary_branch(db: DbfilenameShelf) -> None:
    subprocess.check_call(
        ["git", "branch", "-D", f"branch-{db['release']}"], cwd=db["git_repo"]
    )


def prepare_pydoc_topics(db: DbfilenameShelf) -> None:
    subprocess.check_call(["make", "pydoc-topics"], cwd=db["git_repo"] / "Doc")
    shutil.copy2(
        db["git_repo"] / "Doc" / "build" / "pydoc-topics" / "topics.py",
        db["git_repo"] / "Lib" / "pydoc_data" / "topics.py",
    )
    subprocess.check_call(
        ["git", "commit", "-a", "--amend", "--no-edit"], cwd=db["git_repo"]
    )


def run_autoconf(db: DbfilenameShelf) -> None:
    subprocess.check_call(
        [
            "docker",
            "run",
            "--rm",
            "--pull=always",
            f"-v{db['git_repo']}:/src",
            "quay.io/tiran/cpython_autoconf:latest",
        ],
        cwd=db["git_repo"],
    )
    subprocess.check_call(["docker", "rmi", "quay.io/tiran/cpython_autoconf", "-f"])
    subprocess.check_call(
        ["git", "commit", "-a", "--amend", "--no-edit"], cwd=db["git_repo"]
    )


def check_pyspecific(db):
    with open(
        db["git_repo"] / "Doc" / "tools" / "extensions" / "pyspecific.py", "r"
    ) as pyspecific:
        for line in pyspecific:
            if "SOURCE_URI =" in line:
                break
    expected_branch = db["release"].branch
    expected = (
        f"SOURCE_URI = 'https://github.com/python/cpython/tree/{expected_branch}/%s'"
    )
    if expected != line.strip():
        raise ReleaseException("SOURCE_URI is incorrect")


def bump_version(db: DbfilenameShelf) -> None:
    with cd(db["git_repo"]):
        release_mod.bump(db["release"])
    subprocess.check_call(
        ["git", "commit", "-a", "--amend", "--no-edit"], cwd=db["git_repo"]
    )


def create_tag(db: DbfilenameShelf) -> None:
    with cd(db["git_repo"]):
        if not release_mod.make_tag(db["release"]):
            raise ReleaseException("Error when creating tag")
    subprocess.check_call(
        ["git", "commit", "-a", "--amend", "--no-edit"], cwd=db["git_repo"]
    )


def build_release_artifacts(db: DbfilenameShelf) -> None:
    with cd(db["git_repo"]):
        release_mod.export(db["release"])


def test_release_artifacts(db: DbfilenameShelf) -> None:
    with tempfile.TemporaryDirectory() as tmppath:
        the_dir = pathlib.Path(tmppath)
        the_dir.mkdir(exist_ok=True)
        filename = f"Python-{db['release']}"
        tarball = f"Python-{db['release']}.tgz"
        shutil.copy2(
            db["git_repo"] / str(db["release"]) / "src" / tarball,
            the_dir / tarball,
        )
        subprocess.check_call(["tar", "xvf", tarball], cwd=the_dir)
        subprocess.check_call(
            ["./configure", "--prefix", str(the_dir / "installation")],
            cwd=the_dir / filename,
        )
        subprocess.check_call(["make", "-j"], cwd=the_dir / filename)
        subprocess.check_call(["make", "install", "-j"], cwd=the_dir / filename)
        process = subprocess.run(
            ["./bin/python3", "-m", "test"],
            cwd=str(the_dir / "installation"),
            text=True,
        )

    if process.returncode == 0:
        return

    if not ask_question("Some test_failed! Do you want to continue?"):
        raise ReleaseException("Test failed!")


class MySFTPClient(paramiko.SFTPClient):
    def put_dir(self, source, target, progress=None):
        for item in os.listdir(source):
            if os.path.isfile(os.path.join(source, item)):
                progress.text(item)
                self.put(os.path.join(source, item), "%s/%s" % (target, item))
                progress()
            else:
                self.mkdir("%s/%s" % (target, item), ignore_existing=True)
                self.put_dir(
                    os.path.join(source, item),
                    "%s/%s" % (target, item),
                    progress=progress,
                )

    def mkdir(self, path, mode=511, ignore_existing=False):
        try:
            super().mkdir(path, mode)
        except IOError:
            if ignore_existing:
                pass
            else:
                raise


def upload_files_to_server(db: DbfilenameShelf) -> None:
    client = paramiko.SSHClient()
    client.load_system_host_keys()
    client.set_missing_host_key_policy(paramiko.WarningPolicy)
    client.connect(DOWNLOADS_SERVER, port=22, username=db["ssh_user"])

    destination = pathlib.Path(f"/home/psf-users/pablogsal/{db['release']}")
    ftp_client = MySFTPClient.from_transport(client.get_transport())

    client.exec_command(f"rm -rf {destination}")

    with contextlib.suppress(OSError):
        ftp_client.mkdir(str(destination))

    artifacts_path = pathlib.Path(db["git_repo"] / str(db["release"]))

    shutil.rmtree(artifacts_path / f"Python-{db['release']}", ignore_errors=True)

    def upload_subdir(subdir):
        with contextlib.suppress(OSError):
            ftp_client.mkdir(str(destination / subdir))
        with alive_bar(len(tuple((artifacts_path / subdir).glob("**/*")))) as progress:
            ftp_client.put_dir(
                artifacts_path / subdir,
                str(destination / subdir),
                progress=progress,
            )

    upload_subdir("src")
    if (artifacts_path / "docs").exists():
        upload_subdir("docs")
    ftp_client.close()


def place_files_in_download_folder(db: DbfilenameShelf) -> None:
    client = paramiko.SSHClient()
    client.load_system_host_keys()
    client.set_missing_host_key_policy(paramiko.WarningPolicy)
    client.connect(DOWNLOADS_SERVER, port=22, username=db["ssh_user"])

    # Sources

    source = f"/home/psf-users/pablogsal/{db['release']}"
    destination = f"/srv/www.python.org/ftp/python/{db['release'].normalized()}"

    def execute_command(command):
        channel = client.get_transport().open_session()
        channel.exec_command(command)
        if channel.recv_exit_status() != 0:
            raise ReleaseException(channel.recv_stderr(1000))

    execute_command(f"mkdir -p {destination}")
    execute_command(f"cp {source}/src/* {destination}")
    execute_command(f"chgrp downloads {destination}")
    execute_command(f"chmod 775 {destination}")
    execute_command(f"find {destination} -type f -exec chmod 664 {{}} \\;")

    # Docs

    release_tag: release_mod.Tag = db["release"]
    if release_tag.is_final or release_tag.is_release_candiate:
        source = f"/home/psf-users/pablogsal/{db['release']}"
        destination = f"/srv/www.python.org/ftp/python/doc/{release_tag}"

        def execute_command(command):
            channel = client.get_transport().open_session()
            channel.exec_command(command)
            if channel.recv_exit_status() != 0:
                raise ReleaseException(channel.recv_stderr(1000))

        execute_command(f"mkdir -p {destination}")
        execute_command(f"cp {source}/docs/* {destination}")
        execute_command(f"chgrp downloads {destination}")
        execute_command(f"chmod 775 {destination}")
        execute_command(f"find {destination} -type f -exec chmod 664 {{}} \\;")


def upload_docs_to_the_docs_server(db: DbfilenameShelf) -> None:
    release_tag: release_mod.Tag = db["release"]
    if not (release_tag.is_final or release_tag.is_release_candiate):
        return

    client = paramiko.SSHClient()
    client.load_system_host_keys()
    client.set_missing_host_key_policy(paramiko.WarningPolicy)
    client.connect(DOCS_SERVER, port=22, username=db["ssh_user"])

    destination = pathlib.Path(f"/home/psf-users/pablogsal/{db['release']}")
    ftp_client = MySFTPClient.from_transport(client.get_transport())

    client.exec_command(f"rm -rf {destination}")

    with contextlib.suppress(OSError):
        ftp_client.mkdir(str(destination))

    artifacts_path = pathlib.Path(db["git_repo"] / str(db["release"]))

    shutil.rmtree(artifacts_path / f"Python-{db['release']}", ignore_errors=True)

    def upload_subdir(subdir):
        with contextlib.suppress(OSError):
            ftp_client.mkdir(str(destination / subdir))
        with alive_bar(len(tuple((artifacts_path / subdir).glob("**/*")))) as progress:
            ftp_client.put_dir(
                artifacts_path / subdir,
                str(destination / subdir),
                progress=progress,
            )

    upload_subdir("docs")
    ftp_client.close()


def unpack_docs_in_the_docs_server(db: DbfilenameShelf) -> None:
    release_tag: release_mod.Tag = db["release"]
    if not (release_tag.is_final or release_tag.is_release_candiate):
        return

    client = paramiko.SSHClient()
    client.load_system_host_keys()
    client.set_missing_host_key_policy(paramiko.WarningPolicy)
    client.connect(DOCS_SERVER, port=22, username=db["ssh_user"])

    # Sources

    source = f"/home/psf-users/pablogsal/{db['release']}"
    destination = f"/srv/docs.python.org/release/{release_tag}"

    def execute_command(command):
        channel = client.get_transport().open_session()
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


def send_email_to_platform_release_managers(db: DbfilenameShelf) -> None:
    if not ask_question(
        "Have you notified the platform release managers about the availability of artifacts?"
    ):
        raise ReleaseException("Platform release managers muy be notified")


def create_release_object_in_db(db: DbfilenameShelf) -> None:
    print(
        "Go to https://www.python.org/admin/downloads/release/add/ and create a new release"
    )
    if not ask_question(f"Have you already created a new release for {db['release']}"):
        raise ReleaseException("The django release object has not been created")


def wait_util_all_files_are_in_folder(db: DbfilenameShelf) -> None:
    client = paramiko.SSHClient()
    client.load_system_host_keys()
    client.set_missing_host_key_policy(paramiko.WarningPolicy)
    client.connect(DOWNLOADS_SERVER, port=22, username=db["ssh_user"])
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
        are_all_files_there = (
            are_linux_files_there and are_windows_files_there and are_macos_files_there
        )
        if not are_all_files_there:
            linux_tick = "✅" if are_linux_files_there else "❎"
            windows_tick = "✅" if are_windows_files_there else "❎"
            macos_tick = "✅" if are_macos_files_there else "❎"
            print(
                f"\rWaiting for files: Linux {linux_tick} Windows {windows_tick} Mac {macos_tick}",
                flush=True,
                end="",
            )
            time.sleep(1)
    print()


def run_add_to_python_dot_org(db: DbfilenameShelf) -> None:
    client = paramiko.SSHClient()
    client.load_system_host_keys()
    client.set_missing_host_key_policy(paramiko.WarningPolicy)
    client.connect(DOWNLOADS_SERVER, port=22, username="pablogsal")
    auth_info = db["auth_info"]
    assert auth_info is not None
    stdin, stdout, stderr = client.exec_command(
        f"AUTH_INFO={auth_info} python add-to-pydotorg.py {db['release']}"
    )
    stderr_text = stderr.read().decode()
    if stderr_text:
        raise paramiko.SSHException(f"Failed to execute the command: {stderr_text}")
    stdout_text = stdout.read().decode()
    print("-- Command output --")
    print(stdout_text)
    print("-- End of command output --")


def purge_the_cdn(db: DbfilenameShelf) -> None:
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows; U; Windows NT 6.1; en-US; rv:1.9.1.6) Gecko/20091201 Firefox/3.5.6"
    }
    normalized_release = db["release"].normalized()
    urls = [
        f"https://www.python.org/downloads/release/python-{str(db['release']).replace('.', '')}/",
        f"https://docs.python.org/release/{db['release']}/",
        f"https://www.python.org/ftp/python/{normalized_release}/",
        f"https://www.python.org/ftp/python/{normalized_release}/Python-{db['release']}.tgz",
        f"https://www.python.org/ftp/python/{normalized_release}/Python-{db['release']}.tgz.asc",
        f"https://www.python.org/ftp/python/{normalized_release}/Python-{db['release']}.tar.xz",
        f"https://www.python.org/ftp/python/{normalized_release}/Python-{db['release']}.tar.xz.asc",
        f"https://docs.python.org/release/{normalized_release}/",
    ]
    for url in urls:
        req = urllib.request.Request(url=url, headers=headers, method="PURGE")
        # try:
        response = urllib.request.urlopen(req)
        if response.code != 200:
            raise RuntimeError("Failed to purge the python.org/downloads CDN")


def modify_the_release_to_the_prerelease_pages(db: DbfilenameShelf) -> None:
    pre_release_tags = {"rc", "b", "a"}
    if any(tag in str(db["release"]) for tag in pre_release_tags):
        if not ask_question(
            "Have you already added the release to https://www.python.org/download/pre-releases/"
        ):
            raise ReleaseException(
                "The release has not been added to the pre-release page"
            )
    else:
        if ask_question(
            "Have you already removed the release from https://www.python.org/download/pre-releases/"
        ):
            raise ReleaseException(
                "The release has not been removed from the pre-release page"
            )


def post_release_merge(db: DbfilenameShelf) -> None:
    release_tag: release_mod.Tag = db["release"]

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


def post_release_tagging(db: DbfilenameShelf) -> None:
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


def maybe_prepare_new_master_branch(db: DbfilenameShelf) -> None:
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

    new_branch = f"{release_tag.major}.{int(release_tag.minor)+1}"
    whatsnew_file = f"Doc/whatsnew/{new_branch}"
    with cd(db["git_repo"]), open(whatsnew_file, "w") as f:
        f.write(WHATS_NEW_TEMPLATE.format(version=new_branch))

    subprocess.check_call(
        ["git", "add", whatsnew_file],
        cwd=db["git_repo"],
    )

    subprocess.check_call(
        ["git", "commit", "-a", "-m", f"Python {new_release}"],
        cwd=db["git_repo"],
    )


def branch_new_versions(db: DbfilenameShelf) -> None:
    release_tag: release_mod.Tag = db["release"]

    if not release_tag.is_feature_freeze_release:
        return

    subprocess.check_call(["git", "checkout", "main"], cwd=db["git_repo"])

    subprocess.check_call(
        ["git", "checkout", "-b", release_tag.branch],
        cwd=db["git_repo"],
    )


def push_to_local_fork(db: DbfilenameShelf) -> None:
    def _push_to_local(dry_run=False):
        git_command = ["git", "push"]
        if dry_run:
            git_command.append("--dry-run")

        subprocess.check_call(
            git_command + ["origin", "HEAD", "--tags"],
            cwd=db["git_repo"],
        )

    _push_to_local(dry_run=True)
    if not ask_question(
        "Does these operations look reasonable? ⚠️⚠️⚠️ Answering 'yes' will push to your origin remote ⚠️⚠️⚠️"
    ):
        raise ReleaseException("Something is wrong - Push to remote aborted")
    _push_to_local(dry_run=False)


def push_to_upstream(db: DbfilenameShelf) -> None:
    release_tag: release_mod.Tag = db["release"]

    def _push_to_upstream(dry_run=False):
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
        "Does these operations look reasonable? ⚠️⚠️⚠️ Answering 'yes' will push to the upstream repository ⚠️⚠️⚠️"
    ):
        raise ReleaseException("Something is wrong - Push to upstream aborted")
    if not ask_question("Is the target branch unprotected for your user?"):
        raise ReleaseException("The target branch is not unprotected for your user")
    _push_to_upstream(dry_run=False)


def main() -> None:

    if "linux" not in sys.platform:
        print("""\
WARNING! This script has not been tested on a platform other than Linux.

Although it should work correctly as long as you have all the dependencies,
some things may not work as expected. As a release manager, you should try to
fix these things in this script so it also support your platform.
""")
        if not ask_question("Do you want to continue?"):
            raise ReleaseException("This release script is not compatible with the running platform")

    parser = argparse.ArgumentParser(description="Process some integers.")

    def _release_type(release):
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
        help="Location of the cpython repository",
        required=True,
        type=str,
    )

    def _api_key(api_key):
        if not API_KEY_REGEXP.match(api_key):
            raise argparse.ArgumentTypeError(
                "Invalid api key format. It must be on the form USER:API_KEY"
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
    args = parser.parse_args()
    auth_key = args.auth_key or os.getenv("AUTH_INFO")
    tasks = [
        Task(check_git, "Checking git is available"),
        Task(check_latexmk, "Checking latexmk is available"),
        Task(check_make, "Checking make is available"),
        Task(check_blurb, "Checking blurb is available"),
        Task(check_docker, "Checking docker is available"),
        Task(check_autoconf, "Checking autoconf is available"),
        Task(check_gpg_keys, "Checking GPG keys"),
        Task(check_ssh_connection, f"Validating ssh connection to {DOWNLOADS_SERVER}"),
        Task(check_buildbots, "Check buildbots are good"),
        Task(check_cpython_repo_is_clean, "Checking git repository is clean"),
        Task(preapre_temporary_branch, "Checking out a temporary release branch"),
        Task(run_blurb_release, "Run blurb release"),
        Task(check_docs, "Check documentation"),
        Task(check_cpython_repo_is_clean, "Checking git repository is clean"),
        Task(prepare_pydoc_topics, "Preparing pydoc topics"),
        Task(run_autoconf, "Running autoconf"),
        Task(check_cpython_repo_is_clean, "Checking git repository is clean"),
        Task(check_pyspecific, "Chech pyspecific"),
        Task(bump_version, "Bump version"),
        Task(check_cpython_repo_is_clean, "Checking git repository is clean"),
        Task(create_tag, "Create tag"),
        Task(build_release_artifacts, "Building release artifacts"),
        Task(test_release_artifacts, "Test release artifacts"),
        Task(upload_files_to_server, "Upload files to the PSF server"),
        Task(place_files_in_download_folder, "Place files in the download folder"),
        Task(upload_docs_to_the_docs_server, "Upload docs to the PSF docs server"),
        Task(unpack_docs_in_the_docs_server, "Place docs files in the docs folder"),
        Task(push_to_local_fork, "Push new tags and branches to private fork"),
        Task(
            send_email_to_platform_release_managers,
            "Platform release managers have been notified of the release artifacts",
        ),
        Task(create_release_object_in_db, "The django release object has been created"),
        Task(post_release_merge, "Merge the tag into the release branch"),
        Task(branch_new_versions, "Branch out new versions and prepare main branch"),
        Task(post_release_tagging, "Final touches for the release"),
        Task(
            maybe_prepare_new_master_branch,
            "prepare new master branch for feature freeze",
        ),
        Task(push_to_upstream, "Push new tags and branches to upstream"),
        Task(remove_temporary_branch, "Removing temporary release branch"),
        Task(wait_util_all_files_are_in_folder, "Wait until all files are ready"),
        Task(run_add_to_python_dot_org, "Add files to python.org download page"),
        Task(purge_the_cdn, "Purge the CDN of python.org/downloads"),
        Task(modify_the_release_to_the_prerelease_pages, "Modify the pre-release page"),
    ]
    automata = ReleaseDriver(
        git_repo=args.repo,
        release_tag=release_mod.Tag(args.release),
        api_key=auth_key,
        ssh_user=args.ssh_user,
        tasks=tasks,
    )
    automata.run()


if __name__ == "__main__":
    main()
