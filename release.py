#!/usr/bin/env python3

"""An assistant for making Python releases.

Original code by Benjamin Peterson
Additions by Barry Warsaw, Georg Brandl and Benjamin Peterson
"""

import sys
import os
import hashlib
import optparse
import re
import subprocess
import shutil

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


def run_cmd(args, silent=False):
    cmd = SPACE.join(args)
    if not silent:
        print('Executing %s' % cmd)
    try:
        if silent:
            code = subprocess.call(cmd, shell=True, stdout=subprocess.PIPE)
        else:
            code = subprocess.call(cmd, shell=True)
    except OSError:
        error('%s failed' % cmd)
    else:
        return code


def get_output(args):
    return subprocess.check_output(SPACE.join(args), shell=True)


def check_env():
    if 'EDITOR' not in os.environ:
        error('editor not detected.',
              'Please set your EDITOR enviroment variable')
    if not os.path.exists('.hg'):
        error('CWD is not a Mercurial clone')


def get_arg_parser():
    usage = '%prog [options] tagname'
    p = optparse.OptionParser(usage=usage)
    p.add_option('-b', '--bump',
                 default=False, action='store_true',
                 help='bump the revision number in important files')
    p.add_option('-e', '--export',
                 default=False, action='store_true',
                 help='Export the hg tag to a tarball and build docs')
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
    constant_replace('Include/patchlevel.h', new_constants)
    print('done')


def bump(tag):
    print('Bumping version to %s' % tag)

    wanted_file = 'Misc/RPM/python-%s.spec' % tag.basic_version
    print('Updating %s' % wanted_file, end=' ')
    if not os.path.exists(wanted_file):
        specs = os.listdir('Misc/RPM/')
        for file in specs:
            if file.startswith('python-'):
                break
        full_path = os.path.join('Misc/RPM/', file)
        print('\nrenaming %s to %s' % (full_path, wanted_file))
        run_cmd(['hg', 'rename', '--force', full_path, wanted_file])
        print('File was renamed; please commit')
        run_cmd(['hg', 'commit'])
    new = '%define version ' + tag.text + \
        '\n%define libvers ' + tag.basic_version
    constant_replace(wanted_file, new, '#', '')
    print('done')

    tweak_patchlevel(tag)

    print('Updating Lib/idlelib/idlever.py...', end=' ')
    with open('Lib/idlelib/idlever.py', 'w', encoding="ascii") as fp:
        new = 'IDLE_VERSION = "%s"\n' % tag.text
        fp.write(new)
    print('done')

    print('Updating Lib/distutils/__init__.py...', end=' ')
    new = '__version__ = "%s"' % tag.text
    constant_replace('Lib/distutils/__init__.py', new, '#', '')
    print('done')

    extra_work = False
    other_files = ['README', 'Misc/NEWS']
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
        print('Edit %s' % fn)
        manual_edit(fn)

    print('Bumped revision')
    if extra_work:
        print('configure.ac has change; re-run autotools!')
    print('Please commit and use --tag')


def manual_edit(fn):
    run_cmd([os.environ["EDITOR"], fn])


@contextmanager
def changed_dir(new):
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

def tarball(source):
    """Build tarballs for a directory."""
    print('Making .tgz')
    base = os.path.basename(source)
    tgz = base + '.tgz'
    bz = base + '.tar.bz2'
    xz = base + '.tar.xz'
    run_cmd(['tar cf - %s | gzip -9 > %s' % (source, tgz)])
    print("Making .tar.bz2")
    run_cmd(['tar cf - %s | bzip2 -9 > %s' % (source, bz)])
    print("Making .tar.xz")
    run_cmd(['tar cf - %s | xz > %s' % (source, xz)])
    print('Calculating md5 sums')
    checksum_tgz = hashlib.md5()
    with open(tgz, 'rb') as data:
        checksum_tgz.update(data.read())
    checksum_bz2 = hashlib.md5()
    with open(bz, 'rb') as data:
        checksum_bz2.update(data.read())
    checksum_xz = hashlib.md5()
    with open(xz, 'rb') as data:
        checksum_xz.update(data.read())
    print('  %s  %8s  %s' % (
        checksum_tgz.hexdigest(), int(os.path.getsize(tgz)), tgz))
    print('  %s  %8s  %s' % (
        checksum_bz2.hexdigest(), int(os.path.getsize(bz)), bz))
    print('  %s  %8s  %s' % (
        checksum_xz.hexdigest(), int(os.path.getsize(xz)), xz))
    with open(tgz + '.md5', 'w', encoding="ascii") as fp:
        fp.write(checksum_tgz.hexdigest())
    with open(bz + '.md5', 'w', encoding="ascii") as fp:
        fp.write(checksum_bz2.hexdigest())
    with open(xz + '.md5', 'w', encoding="ascii") as fp:
        fp.write(checksum_xz.hexdigest())

    print('Signing tarballs')
    print('List of available private keys:')
    run_cmd(['gpg -K | grep -A 1 "^sec"'])
    uid = input('Please enter key ID to use for signing: ')
    os.system('gpg -bas -u ' + uid + ' ' + tgz)
    os.system('gpg -bas -u ' + uid + ' ' + bz)
    os.system('gpg -bas -u ' + uid + ' ' + xz)


def export(tag):
    make_dist(tag.text)
    with changed_dir(tag.text):
        print('Exporting tag:', tag.text)
        archivename = 'Python-%s' % tag.text
        run_cmd(['hg', 'archive', '--config', 'ui.archivemeta=off',
                 '-r', tag.hgname, archivename])
        with changed_dir(archivename):
            print('Removing VCS .*ignore and .hg*')
            for name in ('.hgignore', '.hgeol', '.hgtags', '.hgtouch',
                         '.bzrignore', '.gitignore'):
                try:
                    os.unlink(name)
                except OSError:
                    pass
            # Touch a few files that get generated so they're up-to-date in
            # the tarball.
            touchables = ['Include/Python-ast.h', 'Python/Python-ast.c']
            if os.path.exists('Python/opcode_targets.h'):
                # This file isn't in Python < 3.1
                touchables.append('Python/opcode_targets.h')
            print('Touching:', COMMASPACE.join(name.rsplit('/', 1)[-1]
                                               for name in touchables))
            for name in touchables:
                os.utime(name, None)

            if tag.is_final:
                docdist = build_docs()
        if tag.is_final:
            shutil.copytree(docdist, 'docs')

        with changed_dir(os.path.join(archivename, 'Doc')):
            print('Removing doc build artifacts')
            shutil.rmtree('build', ignore_errors=True)
            shutil.rmtree('dist', ignore_errors=True)
            shutil.rmtree('tools/docutils', ignore_errors=True)
            shutil.rmtree('tools/jinja2', ignore_errors=True)
            shutil.rmtree('tools/pygments', ignore_errors=True)
            shutil.rmtree('tools/sphinx', ignore_errors=True)
            for dirpath, dirnames, filenames in os.walk('.'):
                for filename in filenames:
                    if filename.endswith('.pyc'):
                        os.remove(os.path.join(dirpath, filename))

        os.mkdir('src')
        with changed_dir('src'):
            tarball(os.path.join("..", archivename))
    print()
    print('**Now extract the archives in %s/src and run the tests**' % tag.text)
    print('**You may also want to run make install and re-test**')


def build_docs():
    """Build and tarball the documentation"""
    print("Building docs")
    with changed_dir('Doc'):
        run_cmd(['make', 'dist'])
        return os.path.abspath('dist')

def upload(tag, username):
    """scp everything to dinsdale"""
    address ='"%s@dinsdale.python.org:' % username
    def scp(from_loc, to_loc):
        run_cmd(['scp %s %s' % (from_loc, address + to_loc)])
    with changed_dir(tag.text):
        print("Uploading source tarballs")
        scp('src', '/data/python-releases/%s' % tag.nickname)
        print("Upload doc tarballs")
        scp('docs', '/data/python-releases/doc/%s' % tag.nickname)
        print("* Now change the permissions on the tarballs so they are " \
            "writable by the webmaster group. *")


class Tag(object):

    def __init__(self, tag_name):
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

    @property
    def nickname(self):
        return self.text.replace('.', '')

    @property
    def hgname(self):
        return 'v' + self.text


def make_tag(tag):
    # make sure we're on the correct branch
    if tag.patch > 0:
        if get_output(['hg', 'branch']).strip().decode() != tag.basic_version:
            print('It doesn\'t look like you\'re on the correct branch.')
            if input('Are you sure you want to tag?') != "y":
                return
    run_cmd(['hg', 'tag', tag.hgname])


NEWS_TEMPLATE = """
What's New in Python {XXX PUT NEXT VERSION HERE XXX}?
================================

*Release date: XXXX-XX-XX*

Core and Builtins
-----------------

Library
-------

"""

def update_news():
    print("Updating Misc/NEWS")
    with open('Misc/NEWS', encoding="utf-8") as fp:
        lines = fp.readlines()
    for i, line in enumerate(lines):
        if line.startswith("Python News"):
            start = i
        if line.startswith("What's"):
            end = i
            break
    with open('Misc/NEWS', 'w', encoding="utf-8") as fp:
         fp.writelines(lines[:start+2])
         fp.write(NEWS_TEMPLATE)
         fp.writelines(lines[end-1:])
    print("Please fill in the the name of the next version.")
    manual_edit('Misc/NEWS')


def done(tag):
    tweak_patchlevel(tag, done=True)
    update_news()


def main(argv):
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
