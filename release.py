#!/usr/bin/env python3

"""An assistant for making Python releases.

Original code by Benjamin Peterson
Additions by Barry Warsaw, Georg Brandl and Benjamin Peterson
"""

from __future__ import annotations

import datetime
import glob
import hashlib
import optparse
import os
import re
import readline  # noqa: F401
import shutil
import subprocess
import sys
import tempfile
from collections.abc import Generator
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import (
    Any,
    Callable,
    Literal,
    Protocol,
    Self,
    overload,
)

COMMASPACE = ", "
SPACE = " "
tag_cre = re.compile(r"(\d+)(?:\.(\d+)(?:\.(\d+))?)?(?:([ab]|rc)(\d+))?$")


class ReleaseShelf(Protocol):
    def close(self) -> None: ...

    @overload
    def get(self, key: Literal["finished"], default: bool | None = None) -> bool: ...

    @overload
    def get(
        self, key: Literal["completed_tasks"], default: list[Task] | None = None
    ) -> list[Task]: ...

    @overload
    def get(self, key: Literal["gpg_key"], default: str | None = None) -> str: ...

    @overload
    def get(self, key: Literal["git_repo"], default: Path | None = None) -> Path: ...

    @overload
    def get(self, key: Literal["auth_info"], default: str | None = None) -> str: ...

    @overload
    def get(self, key: Literal["ssh_user"], default: str | None = None) -> str: ...

    @overload
    def get(self, key: Literal["sign_gpg"], default: bool | None = None) -> bool: ...

    @overload
    def get(self, key: Literal["release"], default: Tag | None = None) -> Tag: ...

    @overload
    def __getitem__(self, key: Literal["finished"]) -> bool: ...

    @overload
    def __getitem__(self, key: Literal["completed_tasks"]) -> list[Task]: ...

    @overload
    def __getitem__(self, key: Literal["gpg_key"]) -> str: ...

    @overload
    def __getitem__(self, key: Literal["git_repo"]) -> Path: ...

    @overload
    def __getitem__(self, key: Literal["auth_info"]) -> str: ...

    @overload
    def __getitem__(self, key: Literal["ssh_user"]) -> str: ...

    @overload
    def __getitem__(self, key: Literal["sign_gpg"]) -> bool: ...

    @overload
    def __getitem__(self, key: Literal["release"]) -> Tag: ...

    @overload
    def __setitem__(self, key: Literal["finished"], value: bool) -> None: ...

    @overload
    def __setitem__(
        self, key: Literal["completed_tasks"], value: list[Task]
    ) -> None: ...

    @overload
    def __setitem__(self, key: Literal["gpg_key"], value: str) -> None: ...

    @overload
    def __setitem__(self, key: Literal["git_repo"], value: Path) -> None: ...

    @overload
    def __setitem__(self, key: Literal["auth_info"], value: str) -> None: ...

    @overload
    def __setitem__(self, key: Literal["ssh_user"], value: str) -> None: ...

    @overload
    def __setitem__(self, key: Literal["sign_gpg"], value: bool) -> None: ...

    @overload
    def __setitem__(self, key: Literal["release"], value: Tag) -> None: ...


@dataclass
class Task:
    function: Callable[[ReleaseShelf], None]
    description: str

    def __call__(self, db: ReleaseShelf) -> Any:
        return getattr(self, "function")(db)


class Tag:
    def __init__(self, tag_name: str) -> None:
        # if tag is ".", use current directory name as tag
        # e.g. if current directory name is "3.4.6",
        # "release.py --bump 3.4.6" and "release.py --bump ." are the same
        if tag_name == ".":
            tag_name = os.path.basename(os.getcwd())
        result = tag_cre.match(tag_name)
        if result is None:
            error(f"tag {tag_name} is not valid")
        assert result is not None
        data = list(result.groups())
        if data[3] is None:
            # A final release.
            self.is_final = True
            data[3] = "f"
        else:
            self.is_final = False
        # For everything else, None means 0.
        for i, thing in enumerate(data):
            if thing is None:
                data[i] = 0
        self.major = int(data[0])
        self.minor = int(data[1])
        self.patch = int(data[2])
        self.level = data[3]
        self.serial = int(data[4])
        # This has the effect of normalizing the version.
        self.text = self.normalized()
        if self.level != "f":
            assert self.level is not None
            self.text += self.level + str(self.serial)
        self.basic_version = f"{self.major}.{self.minor}"

    def __str__(self) -> str:
        return self.text

    def normalized(self) -> str:
        return f"{self.major}.{self.minor}.{self.patch}"

    @property
    def branch(self) -> str:
        return "main" if self.is_alpha_release else f"{self.major}.{self.minor}"

    @property
    def is_alpha_release(self) -> bool:
        return self.level == "a"

    @property
    def is_release_candidate(self) -> bool:
        return self.level == "rc"

    @property
    def is_feature_freeze_release(self) -> bool:
        return self.level == "b" and self.serial == 1

    @property
    def nickname(self) -> str:
        return self.text.replace(".", "")

    @property
    def gitname(self) -> str:
        return "v" + self.text

    @property
    def long_name(self) -> str:
        if self.is_final:
            return self.text

        level = {
            "a": "alpha",
            "b": "beta",
            "rc": "release candidate",
        }[self.level]
        return f"{self.normalized()} {level} {self.serial}"

    def next_minor_release(self) -> Self:
        return self.__class__(f"{self.major}.{int(self.minor)+1}.0a0")

    def as_tuple(self) -> tuple[int, int, int, str, int]:
        assert isinstance(self.level, str)
        return self.major, self.minor, self.patch, self.level, self.serial

    @property
    def committed_at(self) -> datetime.datetime:
        # Fetch the epoch of the tagged commit for build reproducibility.
        proc = subprocess.run(
            ["git", "log", self.gitname, "-1", "--pretty=%ct"], stdout=subprocess.PIPE
        )
        if proc.returncode != 0:
            error(f"Couldn't fetch the epoch of tag {self.gitname}")
        return datetime.datetime.fromtimestamp(
            int(proc.stdout.decode().strip()), tz=datetime.timezone.utc
        )

    @property
    def includes_docs(self) -> bool:
        """True if docs should be included in the release"""
        return self.is_final or self.is_release_candidate

    @property
    def doc_version(self) -> str:
        """Text used for notes in docs like 'Added in x.y'"""
        # - ignore levels (alpha/beta/rc are preparation for the full release)
        # - use just X.Y for patch 0
        if self.patch == 0:
            return f"{self.major}.{self.minor}"
        else:
            return f"{self.major}.{self.minor}.{self.patch}"


def error(*msgs: str) -> None:
    print("**ERROR**", file=sys.stderr)
    for msg in msgs:
        print(msg, file=sys.stderr)
    sys.exit(1)


def run_cmd(
    cmd: list[str] | str, silent: bool = False, shell: bool = False, **kwargs: Any
) -> None:
    if shell:
        cmd = SPACE.join(cmd)
    if not silent:
        print(f"Executing {cmd}")
    try:
        if silent:
            subprocess.check_call(cmd, shell=shell, stdout=subprocess.PIPE, **kwargs)
        else:
            subprocess.check_call(cmd, shell=shell, **kwargs)
    except subprocess.CalledProcessError:
        error(f"{cmd} failed")


readme_re = re.compile(r"This is Python version 3\.\d").match


def chdir_to_repo_root() -> str:
    # find the root of the local CPython repo
    # note that we can't ask git, because we might
    # be in an exported directory tree!

    # we intentionally start in a (probably nonexistent) subtree
    # the first thing the while loop does is .., basically
    path = os.path.abspath("garglemox")
    while True:
        next_path = os.path.dirname(path)
        if next_path == path:
            sys.exit("You're not inside a CPython repo right now!")
        path = next_path

        os.chdir(path)

        def test_first_line(
            filename: str,
            test: Callable[[str], object],
        ) -> bool:
            if not os.path.exists(filename):
                return False
            with open(filename) as f:
                lines = f.read().split("\n")
                if not (lines and test(lines[0])):
                    return False
            return True

        if not test_first_line("README.rst", readme_re):
            continue
        if not test_first_line("LICENSE", "A. HISTORY OF THE SOFTWARE".__eq__):
            continue
        if not os.path.exists("Include/Python.h"):
            continue
        if not os.path.exists("Python/ceval.c"):
            continue

        break

    root = path
    return root


def get_output(args: list[str]) -> bytes:
    return subprocess.check_output(args)


def check_env() -> None:
    if "EDITOR" not in os.environ:
        error("editor not detected.", "Please set your EDITOR environment variable")
    if not os.path.exists(".git"):
        error("CWD is not a git clone")


def get_arg_parser() -> optparse.OptionParser:
    usage = "%prog [options] tagname"
    p = optparse.OptionParser(usage=usage)
    p.add_option(
        "-b",
        "--bump",
        default=False,
        action="store_true",
        help="bump the revision number in important files",
    )
    p.add_option(
        "-e",
        "--export",
        default=False,
        action="store_true",
        help="Export the git tag to a tarball and build docs",
    )
    p.add_option(
        "-u",
        "--upload",
        metavar="username",
        help="Upload the tarballs and docs to dinsdale",
    )
    p.add_option(
        "-m",
        "--branch",
        default=False,
        action="store_true",
        help="Create a maintenance branch to go along with the release",
    )
    p.add_option(
        "-t",
        "--tag",
        default=False,
        action="store_true",
        help="Tag the release in Subversion",
    )
    p.add_option(
        "-d",
        "--done",
        default=False,
        action="store_true",
        help="Do post-release cleanups (i.e.  you're done!)",
    )
    p.add_option(
        "--skip-docs",
        default=False,
        action="store_true",
        help="Skip building the documentation during export",
    )
    return p


def constant_replace(
    filename: str,
    updated_constants: str,
    comment_start: str = "/*",
    comment_end: str = "*/",
) -> None:
    """Inserts in between --start constant-- and --end constant-- in a file"""
    start_tag = comment_start + "--start constants--" + comment_end
    end_tag = comment_start + "--end constants--" + comment_end
    with open(filename, encoding="ascii") as infile, open(
        filename + ".new", "w", encoding="ascii"
    ) as outfile:
        found_constants = False
        waiting_for_end = False
        for line in infile:
            if line[:-1] == start_tag:
                print(start_tag, file=outfile)
                print(updated_constants, file=outfile)
                print(end_tag, file=outfile)
                waiting_for_end = True
                found_constants = True
            elif line[:-1] == end_tag:
                waiting_for_end = False
            elif waiting_for_end:
                pass
            else:
                outfile.write(line)
    if not found_constants:
        error(f"Constant section delimiters not found: {filename}")
    os.rename(filename + ".new", filename)


def tweak_patchlevel(
    tag: Tag, filename: str = "Include/patchlevel.h", done: bool = False
) -> None:
    print(f"Updating {filename}...", end=" ")
    template = '''
#define PY_MAJOR_VERSION\t{tag.major}
#define PY_MINOR_VERSION\t{tag.minor}
#define PY_MICRO_VERSION\t{tag.patch}
#define PY_RELEASE_LEVEL\t{level_def}
#define PY_RELEASE_SERIAL\t{tag.serial}

/* Version as a string */
#define PY_VERSION      \t\"{tag.text}{plus}"'''.strip()
    assert isinstance(tag.level, str)
    level_def = {
        "a": "PY_RELEASE_LEVEL_ALPHA",
        "b": "PY_RELEASE_LEVEL_BETA",
        "rc": "PY_RELEASE_LEVEL_GAMMA",
        "f": "PY_RELEASE_LEVEL_FINAL",
    }[tag.level]
    new_constants = template.format(
        tag=tag, level_def=level_def, plus=done and "+" or ""
    )
    if tag.as_tuple() >= (3, 7, 0, "a", 3):
        new_constants = new_constants.expandtabs()
    constant_replace(filename, new_constants)
    print("done")


def tweak_readme(tag: Tag, filename: str = "README.rst") -> None:
    print(f"Updating {filename}...", end=" ")
    readme = Path(filename)

    # Update first line: "This is Python version 3.14.0 alpha 7"
    # and update length of underline in second line to match.
    lines = readme.read_text().splitlines()
    this_is = f"This is Python version {tag.long_name}"
    underline = "=" * len(this_is)
    lines[0] = this_is
    lines[1] = underline

    readme.write_text("\n".join(lines))
    print("done")


def bump(tag: Tag) -> None:
    print(f"Bumping version to {tag}")

    tweak_patchlevel(tag)
    tweak_readme(tag)

    extra_work = False
    other_files = []
    if tag.patch == 0 and tag.level == "a" and tag.serial == 0:
        extra_work = True
        other_files += [
            "configure.ac",
            "Doc/tutorial/interpreter.rst",
            "Doc/tutorial/stdlib.rst",
            "Doc/tutorial/stdlib2.rst",
            "PC/pyconfig.h.in",
            "PCbuild/rt.bat",
            ".github/ISSUE_TEMPLATE/bug.yml",
            ".github/ISSUE_TEMPLATE/crash.yml",
        ]
    print("\nManual editing time...")
    for filename in other_files:
        if os.path.exists(filename):
            print(f"Edit {filename}")
            manual_edit(filename)
        else:
            print(f"Skipping {filename}")

    print("Bumped revision")
    if extra_work:
        print("configure.ac has changed; re-run autotools!")
    print("Please commit and use --tag")


def manual_edit(filename: str) -> None:
    editor = os.environ["EDITOR"].split()
    run_cmd([*editor, filename])


@contextmanager
def pushd(new: str) -> Generator[None, None, None]:
    print(f"chdir'ing to {new}")
    old = os.getcwd()
    os.chdir(new)
    try:
        yield
    finally:
        os.chdir(old)


def make_dist(name: str) -> None:
    try:
        os.mkdir(name)
    except OSError:
        if os.path.isdir(name):
            print(f"WARNING: dist dir {name} already exists", file=sys.stderr)
        else:
            error(f"{name}/ is not a directory")
    else:
        print(f"created dist directory {name}")


def tarball(source: str, clamp_mtime: str) -> None:
    """Build tarballs for a directory."""
    print("Making .tgz")
    base = os.path.basename(source)
    tgz = os.path.join("src", base + ".tgz")
    xz = os.path.join("src", base + ".tar.xz")
    # Recommended options for creating reproducible tarballs from:
    # https://www.gnu.org/software/tar/manual/html_node/Reproducibility.html#Reproducibility
    # and https://reproducible-builds.org/docs/archives/
    repro_options = [
        # Sorts the entries in the tarball by name.
        "--sort=name",
        # Sets a maximum 'modified time' of entries in tarball.
        f"--mtime={clamp_mtime}",
        "--clamp-mtime",
        # Sets the owner uid and gid to 0.
        "--owner=0",
        "--group=0",
        "--numeric-owner",
        # Omits process ID, file access, and status change times.
        "--pax-option=exthdr.name=%d/PaxHeaders/%f,delete=atime,delete=ctime",
        # Omit irrelevant info about file permissions.
        "--mode=go+u,go-w",
    ]
    run_cmd(
        [
            "tar",
            "cf",
            tgz,
            *repro_options,
            "--use-compress-program",
            "gzip --no-name -9",
            source,
        ]
    )
    print("Making .tar.xz")
    run_cmd(["tar", "cJf", xz, *repro_options, source])
    print("Calculating md5 sums")
    checksum_tgz = hashlib.md5()
    with open(tgz, "rb") as data:
        checksum_tgz.update(data.read())
    checksum_xz = hashlib.md5()
    with open(xz, "rb") as data:
        checksum_xz.update(data.read())
    print(f"  {checksum_tgz.hexdigest()}  {os.path.getsize(tgz):8}  {tgz}")
    print(f"  {checksum_xz.hexdigest()}  {os.path.getsize(xz):8}  {xz}")


def export(tag: Tag, silent: bool = False, skip_docs: bool = False) -> None:
    make_dist(tag.text)
    print("Exporting tag:", tag.text)
    archivename = f"Python-{tag.text}"
    # I have not figured out how to get git to directly produce an
    # archive directory like hg can, so use git to produce a temporary
    # tarball then expand it with tar.
    archivetempfile = f"{archivename}.tar"
    run_cmd(
        [
            "git",
            "archive",
            "--format=tar",
            f"--prefix={archivename}/",
            "-o",
            archivetempfile,
            tag.gitname,
        ],
        silent=silent,
    )
    with pushd(tag.text):
        archivetempfile = f"../{archivetempfile}"
        run_cmd(["tar", "-xf", archivetempfile], silent=silent)
        os.unlink(archivetempfile)
        with pushd(archivename):
            # Touch a few files that get generated so they're up-to-date in
            # the tarball.
            #
            # Note, with the demise of "make touch" and the hg touch
            # extension, touches should not be needed anymore,
            # but keep it for now as a reminder.
            maybe_touchables = [
                "Include/Python-ast.h",
                "Include/internal/pycore_ast.h",
                "Include/internal/pycore_ast_state.h",
                "Python/Python-ast.c",
                "Python/opcode_targets.h",
            ]
            touchables = [file for file in maybe_touchables if os.path.exists(file)]
            print(
                "Touching:",
                COMMASPACE.join(name.rsplit("/", 1)[-1] for name in touchables),
            )
            for name in touchables:
                os.utime(name, None)

            # build docs *before* we do "blurb export"
            # because docs now depend on Misc/NEWS.d
            # and we remove Misc/NEWS.d as part of cleanup for export
            #
            # If --skip-docs is provided we don't build and docs.
            if not skip_docs and (tag.is_final or tag.level == "rc"):
                docdist = build_docs()

            print("Using blurb to build Misc/NEWS")
            run_cmd(["blurb", "merge"], silent=silent)

            # Remove files we don't want to ship in tarballs.
            print("Removing VCS .*ignore, .git*, Misc/NEWS.d, et al")
            for name in (
                ".gitattributes",
                ".gitignore",
                ".hgignore",
                ".hgeol",
                ".hgtags",
                ".hgtouch",
                ".bzrignore",
                ".codecov.yml",
                ".mention-bot",
                ".travis.yml",
            ):
                try:
                    os.unlink(name)
                except OSError:
                    pass

            # Remove directories we don't want to ship in tarballs.
            run_cmd(["blurb", "export"], silent=silent)
            for name in (".azure-pipelines", ".git", ".github", ".hg", "Misc/mypy"):
                shutil.rmtree(name, ignore_errors=True)

        if not skip_docs and (tag.is_final or tag.level == "rc"):
            shutil.copytree(docdist, "docs")

        with pushd(os.path.join(archivename, "Doc")):
            print("Removing doc build artifacts")
            shutil.rmtree("venv", ignore_errors=True)
            shutil.rmtree("build", ignore_errors=True)
            shutil.rmtree("dist", ignore_errors=True)
            shutil.rmtree("tools/docutils", ignore_errors=True)
            shutil.rmtree("tools/jinja2", ignore_errors=True)
            shutil.rmtree("tools/pygments", ignore_errors=True)
            shutil.rmtree("tools/sphinx", ignore_errors=True)

        with pushd(archivename):
            print("Zapping pycs")
            run_cmd(
                [
                    "find",
                    ".",
                    "-depth",
                    "-name",
                    "__pycache__",
                    "-exec",
                    "rm",
                    "-rf",
                    "{}",
                    ";",
                ],
                silent=silent,
            )
            run_cmd(
                ["find", ".", "-name", "*.py[co]", "-exec", "rm", "-f", "{}", ";"],
                silent=silent,
            )

        os.mkdir("src")
        tarball(archivename, tag.committed_at.strftime("%Y-%m-%d %H:%M:%SZ"))
    print()
    print(f"**Now extract the archives in {tag.text}/src and run the tests**")
    print("**You may also want to run make install and re-test**")


def build_docs() -> str:
    """Build and tarball the documentation"""
    print("Building docs")
    with tempfile.TemporaryDirectory() as venv:
        run_cmd(["python3", "-m", "venv", venv])
        pip = os.path.join(venv, "bin", "pip")
        run_cmd([pip, "install", "-r", "Doc/requirements.txt"])
        sphinx_build = os.path.join(venv, "bin", "sphinx-build")
        blurb = os.path.join(venv, "bin", "blurb")
        with pushd("Doc"):
            run_cmd(
                ["make", "dist", "SPHINXBUILD=" + sphinx_build, "BLURB=" + blurb],
                env={**os.environ, "SPHINXOPTS": "-j10"},
            )
            return os.path.abspath("dist")


def upload(tag: Tag, username: str) -> None:
    """scp everything to dinsdale"""
    address = f'"{username}@dinsdale.python.org:'

    def scp(from_loc: str, to_loc: str) -> None:
        run_cmd(["scp", from_loc, address + to_loc])

    with pushd(tag.text):
        print("Uploading source tarballs")
        scp("src", f"/data/python-releases/{tag.nickname}")
        print("Upload doc tarballs")
        scp("docs", f"/data/python-releases/doc/{tag.nickname}")
        print(
            "* Now change the permissions on the tarballs so they are "
            "writable by the webmaster group. *"
        )


def make_tag(tag: Tag, *, sign_gpg: bool = True) -> bool:
    # make sure we've run blurb export
    good_files = glob.glob("Misc/NEWS.d/" + str(tag) + ".rst")
    bad_files = list(glob.glob("Misc/NEWS.d/next/*/0*.rst"))
    bad_files.extend(glob.glob("Misc/NEWS.d/next/*/2*.rst"))
    if bad_files or not good_files:
        print('It doesn\'t look like you ran "blurb release" yet.')
        if bad_files:
            print("There are still reST files in NEWS.d/next/...")
        if not good_files:
            print(f"There is no Misc/NEWS.d/{tag}.rst file.")
        if input("Are you sure you want to tag? (y/n) > ") not in ("y", "yes"):
            print("Aborting.")
            return False

    # make sure we're on the correct branch
    if tag.patch > 0:
        if (
            get_output(["git", "name-rev", "--name-only", "HEAD"]).strip().decode()
            != tag.basic_version
        ):
            print("It doesn't look like you're on the correct branch.")
            if input("Are you sure you want to tag? (y/n) > ").lower() not in (
                "y",
                "yes",
            ):
                print("Aborting.")
                return False

    if sign_gpg:
        print("Signing tag")
        uid = os.environ.get("GPG_KEY_FOR_RELEASE")
        if not uid:
            print("List of available private keys:")
            run_cmd(['gpg -K | grep -A 1 "^sec"'], shell=True)
            uid = input("Please enter key ID to use for signing: ")
        run_cmd(
            ["git", "tag", "-s", "-u", uid, tag.gitname, "-m", "Python " + str(tag)]
        )
    else:
        print("Creating tag")
        run_cmd(["git", "tag", tag.gitname, "-m", "Python " + str(tag)])

    return True


def done(tag: Tag) -> None:
    tweak_patchlevel(tag, done=True)


def main(argv: Any) -> None:
    chdir_to_repo_root()
    parser = get_arg_parser()
    options, args = parser.parse_args(argv)
    if options.skip_docs and not options.export:
        error("--skip-docs option has no effect without --export")
    if len(args) != 2:
        if "RELEASE_TAG" not in os.environ:
            parser.print_usage()
            sys.exit(1)
        tagname = os.environ["RELEASE_TAG"]
    else:
        tagname = args[1]
    tag = Tag(tagname)
    if not (options.export or options.upload):
        check_env()
    if options.bump:
        bump(tag)
    if options.tag:
        make_tag(tag)
    if options.export:
        export(tag, skip_docs=options.skip_docs)
    if options.upload:
        upload(tag, options.upload)
    if options.done:
        done(tag)


if __name__ == "__main__":
    main(sys.argv)
