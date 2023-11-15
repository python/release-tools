#!/usr/bin/env python3

"""An assistant for making Python releases.

Original code by Benjamin Peterson
Additions by Barry Warsaw, Georg Brandl and Benjamin Peterson
"""

import datetime
import glob
import hashlib
import optparse
import os
import re
import readline  # noqa
import shutil
import subprocess
import sys
import tempfile

from contextlib import contextmanager

COMMASPACE = ', '
SPACE = ' '
tag_cre = re.compile(r'(\d+)(?:\.(\d+)(?:\.(\d+))?)?(?:([ab]|rc)(\d+))?$')


# Ideas stolen from Mailman's release script, Lib/tokens.py and welease

def error(*msgs):
    print('**ERROR**', file=sys.stderr)
    for msg in msgs:
        print(msg, file=sys.stderr)
    sys.exit(1)


def run_cmd(cmd, silent=False, shell=False, **kwargs):
    if shell:
        cmd = SPACE.join(cmd)
    if not silent:
        print('Executing %s' % cmd)
    try:
        if silent:
            subprocess.check_call(cmd, shell=shell, stdout=subprocess.PIPE, **kwargs)
        else:
            subprocess.check_call(cmd, shell=shell, **kwargs)
    except subprocess.CalledProcessError:
        error('%s failed' % cmd)

readme_re = re.compile(r"This is Python version [23]\.\d").match

root = None

def chdir_to_repo_root():
    global root

    # find the root of the local CPython repo
    # note that we can't ask git, because we might
    # be in an exported directory tree!

    # we intentionally start in a (probably nonexistant) subtree
    # the first thing the while loop does is .., basically
    path = os.path.abspath("garglemox")
    while True:
        next_path = os.path.dirname(path)
        if next_path == path:
            sys.exit('You\'re not inside a CPython repo right now!')
        path = next_path

        os.chdir(path)

        def test_first_line(filename, test):
            if not os.path.exists(filename):
                return False
            with open(filename, "rt") as f:
                lines = f.read().split('\n')
                if not (lines and test(lines[0])):
                    return False
            return True

        if not (test_first_line("README", readme_re)
            or test_first_line("README.rst", readme_re)):
            continue

        if not test_first_line("LICENSE",  "A. HISTORY OF THE SOFTWARE".__eq__):
            continue
        if not os.path.exists("Include/Python.h"):
            continue
        if not os.path.exists("Python/ceval.c"):
            continue

        break

    root = path
    return root




def get_output(args):
    return subprocess.check_output(args)


def check_env():
    if 'EDITOR' not in os.environ:
        error('editor not detected.',
              'Please set your EDITOR environment variable')
    if not os.path.exists('.git'):
        error('CWD is not a git clone')

def get_arg_parser():
    usage = '%prog [options] tagname'
    p = optparse.OptionParser(usage=usage)
    p.add_option('-b', '--bump',
                 default=False, action='store_true',
                 help='bump the revision number in important files')
    p.add_option('-e', '--export',
                 default=False, action='store_true',
                 help='Export the git tag to a tarball and build docs')
    p.add_option('-u', '--upload', metavar="username",
                 help='Upload the tarballs and docs to dinsdale')
    p.add_option('-m', '--branch',
                 default=False, action='store_true',
                 help='create a maintance branch to go along with the release')
    p.add_option('-t', '--tag',
                 default=False, action='store_true',
                 help='Tag the release in Subversion')
    p.add_option('-d', '--done',
                 default=False, action='store_true',
                 help='Do post-release cleanups (i.e.  you\'re done!)')
    return p


def constant_replace(fn, updated_constants,
                     comment_start='/*', comment_end='*/'):
    """Inserts in between --start constant-- and --end constant-- in a file
    """
    start_tag = comment_start + '--start constants--' + comment_end
    end_tag = comment_start + '--end constants--' + comment_end
    with open(fn, encoding="ascii") as infile, \
             open(fn + '.new', 'w', encoding="ascii") as outfile:
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
        error('Constant section delimiters not found: %s' % fn)
    os.rename(fn + ".new", fn)


def tweak_patchlevel(tag, done=False):
    print('Updating Include/patchlevel.h...', end=' ')
    template = '''
#define PY_MAJOR_VERSION\t{tag.major}
#define PY_MINOR_VERSION\t{tag.minor}
#define PY_MICRO_VERSION\t{tag.patch}
#define PY_RELEASE_LEVEL\t{level_def}
#define PY_RELEASE_SERIAL\t{tag.serial}

/* Version as a string */
#define PY_VERSION      \t\"{tag.text}{plus}"'''.strip()
    level_def = dict(
        a   = 'PY_RELEASE_LEVEL_ALPHA',
        b   = 'PY_RELEASE_LEVEL_BETA',
        rc  = 'PY_RELEASE_LEVEL_GAMMA',
        f   = 'PY_RELEASE_LEVEL_FINAL',
        )[tag.level]
    new_constants = template.format(tag=tag, level_def=level_def,
                                    plus=done and '+' or '')
    if tag.as_tuple() >= (3, 7, 0, 'a', 3):
        new_constants = new_constants.expandtabs()
    constant_replace('Include/patchlevel.h', new_constants)
    print('done')


def bump(tag):
    print('Bumping version to %s' % tag)

    tweak_patchlevel(tag)

    extra_work = False
    # Older releases have a plain text README,
    # newer releases have README.rst.
    other_files = ['README.rst', 'README', 'Misc/NEWS']
    if tag.patch == 0 and tag.level == "a" and tag.serial == 0:
        extra_work = True
        other_files += [
            'configure.ac',
            'Doc/tutorial/interpreter.rst',
            'Doc/tutorial/stdlib.rst',
            'Doc/tutorial/stdlib2.rst',
            'LICENSE',
            'Doc/license.rst',
            ]
    print('\nManual editing time...')
    for fn in other_files:
        if os.path.exists(fn):
            print('Edit %s' % fn)
            manual_edit(fn)
        else:
            print('Skipping %s' % fn)

    print('Bumped revision')
    if extra_work:
        print('configure.ac has change; re-run autotools!')
    print('Please commit and use --tag')


def manual_edit(fn):
    run_cmd([os.environ["EDITOR"], fn])


@contextmanager
def pushd(new):
    print('chdir\'ing to %s' % new)
    old = os.getcwd()
    os.chdir(new)
    try:
        yield
    finally:
        os.chdir(old)

def make_dist(name):
    try:
        os.mkdir(name)
    except OSError:
        if os.path.isdir(name):
            print('WARNING: dist dir %s already exists' % name, file=sys.stderr)
        else:
            error('%s/ is not a directory' % name)
    else:
        print('created dist directory %s' % name)

def tarball(source, clamp_mtime):
    """Build tarballs for a directory."""
    print('Making .tgz')
    base = os.path.basename(source)
    tgz = os.path.join('src', base + '.tgz')
    xz = os.path.join('src', base + '.tar.xz')
    # Recommended options for creating reproducible tarballs from:
    # https://www.gnu.org/software/tar/manual/html_node/Reproducibility.html#Reproducibility
    # and https://reproducible-builds.org/docs/archives/
    repro_options = [
        # Sorts the entries in the tarball by name.
        "--sort=name",
        # Sets a maximum 'modified time' of entries in tarball.
        "--mtime=%s" % (clamp_mtime,), "--clamp-mtime",
        # Sets the owner uid and gid to 0.
        "--owner=0", "--group=0", "--numeric-owner",
        # Omits process ID, file access, and status change times.
        "--pax-option=exthdr.name=%d/PaxHeaders/%f,delete=atime,delete=ctime",
        # Omit irrelevant info about file permissions.
        "--mode=go+u,go-w"
    ]
    run_cmd(["tar", "cf", tgz, *repro_options, "--use-compress-program",
             "gzip --no-name -9", source])
    print("Making .tar.xz")
    run_cmd(["tar", "cJf", xz, *repro_options, source])
    print('Calculating md5 sums')
    checksum_tgz = hashlib.md5()
    with open(tgz, 'rb') as data:
        checksum_tgz.update(data.read())
    checksum_xz = hashlib.md5()
    with open(xz, 'rb') as data:
        checksum_xz.update(data.read())
    print('  %s  %8s  %s' % (
        checksum_tgz.hexdigest(), int(os.path.getsize(tgz)), tgz))
    print('  %s  %8s  %s' % (
        checksum_xz.hexdigest(), int(os.path.getsize(xz)), xz))

    print('Signing tarballs with GPG')
    uid = os.environ.get("GPG_KEY_FOR_RELEASE")
    if not uid:
        print('List of available private keys:')
        run_cmd(['gpg -K | grep -A 1 "^sec"'], shell=True)
        uid = input('Please enter key ID to use for signing: ')
    run_cmd(['gpg', '-bas', '-u', uid, tgz])
    run_cmd(['gpg', '-bas', '-u', uid, xz])

    print('Signing tarballs with Sigstore')
    run_cmd(['python3', '-m', 'sigstore', 'sign',
             '--oidc-disable-ambient-providers', tgz, xz], shell=False)


def export(tag, silent=False):
    make_dist(tag.text)
    print('Exporting tag:', tag.text)
    archivename = 'Python-%s' % tag.text
    # I have not figured out how to get git to directly produce an
    # archive directory like hg can, so use git to produce a temporary
    # tarball then expand it with tar.
    archivetempfile = '%s.tar' % archivename
    run_cmd(['git', 'archive', '--format=tar',
             '--prefix=%s/' % archivename,
             '-o', archivetempfile, tag.gitname], silent=silent)
    with pushd(tag.text):
        archivetempfile = '../%s' % archivetempfile
        run_cmd(['tar', '-xf', archivetempfile], silent=silent)
        os.unlink(archivetempfile)
        with pushd(archivename):
            # Touch a few files that get generated so they're up-to-date in
            # the tarball.
            #
            # Note, with the demise of "make touch" and the hg touch
            # extension, touches should not be needed anymore,
            # but keep it for now as a reminder.
            maybe_touchables = ['Include/Python-ast.h',
                                'Include/internal/pycore_ast.h',
                                'Include/internal/pycore_ast_state.h',
                                'Python/Python-ast.c',
                                'Python/opcode_targets.h']
            touchables = [file for file in maybe_touchables if os.path.exists(file)]
            print('Touching:', COMMASPACE.join(name.rsplit('/', 1)[-1]
                                               for name in touchables))
            for name in touchables:
                os.utime(name, None)

            # build docs *before* we do "blurb export"
            # because docs now depend on Misc/NEWS.d
            # and we remove Misc/NEWS.d as part of cleanup for export
            if tag.is_final or tag.level == 'rc':
                docdist = build_docs()

            print('Using blurb to build Misc/NEWS')
            run_cmd(["blurb", "merge"], silent=silent)

            # Remove files we don't want to ship in tarballs.
            print('Removing VCS .*ignore, .git*, Misc/NEWS.d, et al')
            for name in ('.gitattributes', '.gitignore',
                         '.hgignore', '.hgeol', '.hgtags', '.hgtouch',
                         '.bzrignore', '.codecov.yml',
                         '.mention-bot', '.travis.yml', ):
                try:
                    os.unlink(name)
                except OSError:
                    pass

            # Remove directories we don't want to ship in tarballs.
            run_cmd(["blurb", "export"], silent=silent)
            for name in ('.azure-pipelines', '.git', '.github', '.hg'):
                shutil.rmtree(name, ignore_errors=True)

        if tag.is_final or tag.level == 'rc':
            shutil.copytree(docdist, 'docs')

        with pushd(os.path.join(archivename, 'Doc')):
            print('Removing doc build artifacts')
            shutil.rmtree('venv', ignore_errors=True)
            shutil.rmtree('build', ignore_errors=True)
            shutil.rmtree('dist', ignore_errors=True)
            shutil.rmtree('tools/docutils', ignore_errors=True)
            shutil.rmtree('tools/jinja2', ignore_errors=True)
            shutil.rmtree('tools/pygments', ignore_errors=True)
            shutil.rmtree('tools/sphinx', ignore_errors=True)

        with pushd(archivename):
            print('Zapping pycs')
            run_cmd(['find', '.', '-depth', '-name', '__pycache__',
                     '-exec', 'rm', '-rf', '{}', ';'], silent=silent)
            run_cmd(['find', '.', '-name', '*.py[co]',
                     '-exec', 'rm', '-f', '{}', ';'], silent=silent)

        os.mkdir('src')
        tarball(archivename, tag.committed_at.strftime("%Y-%m-%d %H:%M:%SZ"))
    print()
    print('**Now extract the archives in %s/src and run the tests**' % tag.text)
    print('**You may also want to run make install and re-test**')


def build_docs():
    """Build and tarball the documentation"""
    print("Building docs")
    with tempfile.TemporaryDirectory() as venv:
        run_cmd(['python3', '-m', 'venv', venv])
        pip = os.path.join(venv, 'bin', 'pip')
        run_cmd([pip, 'install', '-r' 'Doc/requirements.txt'])
        sphinx_build = os.path.join(venv, 'bin', 'sphinx-build')
        blurb = os.path.join(venv, 'bin', 'blurb')
        with pushd('Doc'):
            run_cmd(['make', 'dist', 'SPHINXBUILD=' + sphinx_build,
                     'BLURB=' + blurb],
                    env={**os.environ, "SPHINXOPTS": "-j10"})
            return os.path.abspath('dist')

def upload(tag, username):
    """scp everything to dinsdale"""
    address ='"%s@dinsdale.python.org:' % username
    def scp(from_loc, to_loc):
        run_cmd(['scp', from_loc, address + to_loc])
    with pushd(tag.text):
        print("Uploading source tarballs")
        scp('src', '/data/python-releases/%s' % tag.nickname)
        print("Upload doc tarballs")
        scp('docs', '/data/python-releases/doc/%s' % tag.nickname)
        print("* Now change the permissions on the tarballs so they are " \
            "writable by the webmaster group. *")


class Tag(object):

    def __init__(self, tag_name):
        # if tag is ".", use current directory name as tag
        # e.g. if current directory name is "3.4.6",
        # "release.py --bump 3.4.6" and "release.py --bump ." are the same
        if tag_name == ".":
            tag_name = os.path.basename(os.getcwd())
        result = tag_cre.match(tag_name)
        if result is None:
            error('tag %s is not valid' % tag_name)
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
        self.text = "{}.{}.{}".format(self.major, self.minor, self.patch)
        if self.level != "f":
            self.text += self.level + str(self.serial)
        self.basic_version = '%s.%s' % (self.major, self.minor)

    def __str__(self):
        return self.text

    def normalized(self):
        return "{}.{}.{}".format(self.major, self.minor, self.patch)

    @property
    def branch(self):
        return "main" if self.is_alpha_release else f"{self.major}.{self.minor}"

    @property
    def is_alpha_release(self):
        return self.level == "a"

    @property
    def is_release_candidate(self):
        return self.level == "rc"

    @property
    def is_feature_freeze_release(self):
        return self.level == "b" and self.serial == 1

    @property
    def nickname(self):
        return self.text.replace('.', '')

    @property
    def gitname(self):
        return 'v' + self.text

    def next_minor_release(self):
        return self.__class__(f"{self.major}.{int(self.minor)+1}.0a0")

    def as_tuple(self):
        return (self.major, self.minor, self.patch, self.level, self.serial)

    @property
    def committed_at(self):
        # Fetch the epoch of the tagged commit for build reproducibility.
        proc = subprocess.run(["git", "log", self.gitname, "-1", "--pretty=%ct"], stdout=subprocess.PIPE)
        if proc.returncode != 0:
            error("Couldn't fetch the epoch of tag %s" % (self.gitname,))
        return datetime.datetime.fromtimestamp(int(proc.stdout.decode().strip()), tz=datetime.timezone.utc)


def make_tag(tag):
    # make sure we've run blurb export
    good_files = glob.glob("Misc/NEWS.d/" + str(tag) + ".rst")
    bad_files = list(glob.glob("Misc/NEWS.d/next/*/0*.rst"))
    bad_files.extend(glob.glob("Misc/NEWS.d/next/*/2*.rst"))
    if bad_files or not good_files:
        print('It doesn\'t look like you ran "blurb release" yet.')
        if bad_files:
            print('There are still reST files in NEWS.d/next/...')
        if not good_files:
            print(f'There is no Misc/NEWS.d/{tag}.rst file.')
        if input('Are you sure you want to tag? (y/n) > ') not in ("y", "yes"):
            print("Aborting.")
            return False

    # make sure we're on the correct branch
    if tag.patch > 0:
        if get_output(
                ['git', 'name-rev', '--name-only', 'HEAD']
                     ).strip().decode() != tag.basic_version:
            print('It doesn\'t look like you\'re on the correct branch.')
            if input('Are you sure you want to tag? (y/n) > ').lower() not in ("y", "yes"):
                print("Aborting.")
                return False
    print('Signing tag')
    uid = os.environ.get("GPG_KEY_FOR_RELEASE")
    if not uid:
        print('List of available private keys:')
        run_cmd(['gpg -K | grep -A 1 "^sec"'], shell=True)
        uid = input('Please enter key ID to use for signing: ')
    run_cmd(['git', 'tag', '-s', '-u', uid, tag.gitname, '-m',
            'Python ' + str(tag)])
    return True


def done(tag):
    tweak_patchlevel(tag, done=True)


def main(argv):
    chdir_to_repo_root()
    parser = get_arg_parser()
    options, args = parser.parse_args(argv)
    if len(args) != 2:
        if 'RELEASE_TAG' not in os.environ:
            parser.print_usage()
            sys.exit(1)
        tagname = os.environ['RELEASE_TAG']
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
        export(tag)
    if options.upload:
        upload(tag, options.upload)
    if options.done:
        done(tag)


if __name__ == '__main__':
    main(sys.argv)
