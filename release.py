#!/usr/bin/env python

"""An assistant for making Python releases.

Original code by Benjamin Peterson
Additions by Barry Warsaw and Benjamin Peterson
"""

from __future__ import with_statement

import sys
import os
import optparse
import re
import subprocess
import shutil
import tempfile
import time

from contextlib import nested
from hashlib import md5
from string import Template
from urlparse import urlsplit, urlunsplit

SPACE = ' '
tag_cre = re.compile(r'(\d+)(?:\.(\d+)(?:\.(\d+))?)?(?:([abc])(\d+))?')


# Ideas stolen from Mailman's release script, Lib/tokens.py and welease

def error(*msgs):
    print >> sys.stderr, '**ERROR**'
    for msg in msgs:
        print >> sys.stderr, msg
    sys.exit(1)


def run_cmd(args, silent=False):
    cmd = SPACE.join(args)
    if not silent:
        print 'Executing %s' % cmd
    try:
        if silent:
            code = subprocess.call(cmd, shell=True, stdout=PIPE)
        else:
            code = subprocess.call(cmd, shell=True)
    except OSError:
        error('%s failed' % cmd)


def check_env():
    if 'EDITOR' not in os.environ:
        error('editor not detected.',
              'Please set your EDITOR enviroment variable')
    if not os.path.exists('.svn'):
        error('CWD is not a Subversion checkout')


def get_arg_parser():
    usage = '%prog [options] tagname'
    p = optparse.OptionParser(usage=usage)
    p.add_option('-b', '--bump',
                 default=False, action='store_true',
                 help='bump the revision number in important files')
    p.add_option('-e', '--export',
                 default=False, action='store_true',
                 help='Export the SVN tag to a tarball')
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
    with nested(open(fn), open(fn + '.new', 'w')) as (infile, outfile):
        found_constants = False
        waiting_for_end = False
        for line in infile:
            if line[:-1] == start_tag:
                print >> outfile, start_tag
                print >> outfile, updated_constants
                print >> outfile, end_tag
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
    print 'Updating Include/patchlevel.h...',
    template = Template("""\
#define PY_MAJOR_VERSION\t$major
#define PY_MINOR_VERSION\t$minor
#define PY_MICRO_VERSION\t$patch
#define PY_RELEASE_LEVEL\t$level
#define PY_RELEASE_SERIAL\t$serial

/* Version as a string */
#define PY_VERSION      \t\"$text\"""")
    substitutions = {}
    for what in ('major', 'minor', 'patch', 'serial', 'text'):
        substitutions[what] = getattr(tag, what)
    substitutions['level'] = dict(
        a   = 'PY_RELEASE_LEVEL_ALPHA',
        b   = 'PY_RELEASE_LEVEL_BETA',
        c   = 'PY_RELEASE_LEVEL_GAMMA',
        f   = 'PY_RELEASE_LEVEL_FINAL',
        )[tag.level]
    if done:
        substitutions['text'] += '+'
    new_constants = template.substitute(substitutions)
    constant_replace('Include/patchlevel.h', new_constants)
    print 'done'


def bump(tag):
    print 'Bumping version to %s' % tag

    wanted_file = 'Misc/RPM/python-%s.spec' % tag.basic_version
    print 'Updating %s' % wanted_file,
    if not os.path.exists(wanted_file):
        specs = os.listdir('Misc/RPM/')
        for file in specs:
            if file.startswith('python-'):
                break
        full_path = os.path.join('Misc/RPM/', file)
        print '\nrenaming %s to %s' % (full_path, wanted_file)
        run_cmd(['svn', 'rename', '--force', full_path, wanted_file])
        print 'File was renamed; please commit'
        run_cmd(['svn', 'commit'])
    new = '%define version ' + tag.text + \
        '\n%define libver ' + tag.basic_version
    constant_replace(wanted_file, new, '#', '')
    print 'done'

    tweak_patchlevel(tag)

    print 'Updating Lib/idlelib/idlever.py...',
    with open('Lib/idlelib/idlever.py', 'w') as fp:
        new = 'IDLE_VERSION = "%s"\n' % tag.next_text
        fp.write(new)
    print 'done'

    print 'Updating Lib/distutils/__init__.py...',
    new = '__version__ = "%s"' % tag.text
    constant_replace('Lib/distutils/__init__.py', new, '#', '')
    print 'done'

    other_files = ['README', 'Misc/NEWS']
    if tag.patch == 0 and tag.level == "a" and tag.serial == 0:
        other_files += [
            'Doc/tutorial/interpreter.rst',
            'Doc/tutorial/stdlib.rst',
            'Doc/tutorial/stdlib2.rst',
            'LICENSE',
            'Doc/license.rst',
            ]
    if tag.major == 3:
        other_files.append('RELNOTES')
    print '\nManual editing time...'
    for fn in other_files:
        print 'Edit %s' % fn
        manual_edit(fn)

    print 'Bumped revision'
    print 'Please commit and use --tag'


def manual_edit(fn):
    run_cmd([os.environ["EDITOR"], fn])


def export(tag):
    if not os.path.exists('dist'):
        print 'creating dist directory'
        os.mkdir('dist')
    if not os.path.isdir('dist'):
        error('dist/ is not a directory')
    tgz = 'dist/Python-%s.tgz' % tag.text
    bz = 'dist/Python-%s.tar.bz2' % tag.text
    old_cur = os.getcwd()
    print 'chdir\'ing to dist'
    os.chdir('dist')
    try:
        print 'Exporting tag:', tag.text
        python = 'Python-%s' % tag.text
        run_cmd(['svn', 'export',
                 'http://svn.python.org/projects/python/tags/r%s'
                 % tag.nickname, python])
        print 'Removing .hgignore and .bzrignore'
        for name in ('.hgignore', '.bzrignore'):
            try:
                os.unlink(os.path.join('dist', name))
            except OSError: pass
        print 'Making .tgz'
        run_cmd(['tar cf - %s | gzip -9 > %s.tgz' % (python, python)])
        print "Making .tar.bz2"
        run_cmd(['tar cf - %s | bzip2 -9 > %s.tar.bz2' %
                 (python, python)])
    finally:
        os.chdir(old_cur)
    print 'Moving files to dist'
    print 'Calculating md5 sums'
    md5sum_tgz = md5()
    with open(tgz) as source:
        md5sum_tgz.update(source.read())
    md5sum_bz2 = md5()
    with open(bz) as source:
        md5sum_bz2.update(source.read())
    print '  %s  %8s  %s' % (
        md5sum_tgz.hexdigest(), int(os.path.getsize(tgz)), tgz)
    print '  %s  %8s  %s' % (
        md5sum_bz2.hexdigest(), int(os.path.getsize(bz)), bz)
    with open(tgz + '.md5', 'w') as md5file:
        print >> md5file, md5sum_tgz.hexdigest()
    with open(bz + '.md5', 'w') as md5file:
        print >> md5file, md5sum_bz2.hexdigest()

    print 'Signing tarballs'
    os.system('gpg -bas ' + tgz)
    os.system('gpg -bas ' + bz)
    print '\n**Now extract the archives and run the tests**'
    print '**You may also want to run make install and re-test**'


class Tag(object):

    def __init__(self, tag_name):
        result = tag_cre.search(tag_name)
        if result is None:
            error('tag %s is not valid' % tag)
        data = list(result.groups())
        # fix None level
        if data[3] is None:
            data[3] = "f"
        # For everything else, None means 0.
        for i, thing in enumerate(data):
            if thing is None:
                data[i] = 0
        self.text = tag_name
        self.next_text = tag_name
        self.major = int(data[0])
        self.minor = int(data[1])
        self.patch = int(data[2])
        self.level = data[3]
        self.serial = int(data[4])
        self.basic_version = '%s.%s' % (self.major, self.minor)

    def __str__(self):
        return self.text

    @property
    def nickname(self):
        return self.text.replace('.', '')


def branch(tag):
    if tag.minor > 0 or tag.patch > 0 or tag.level != "f":
        print 'It doesn\'t look like you\'re making a final release.'
        if raw_input('Are you sure you want to branch?') != "y":
            return
    run_cmd(['svn', 'copy', get_current_location(),
        'svn+ssh://svn.python.org/projects/python/branches/'
            'release%s-maint' % (tag.major + tag.minor)])


def get_current_location():
    proc = subprocess.Popen('svn info', shell=True, stdout=subprocess.PIPE)
    data = proc.stdout.read().splitlines()
    for line in data:
        if line.startswith('URL: '):
            return line.lstrip('URL: ')


def make_tag(tag):
    url = urlsplit(get_current_location())
    new_path = 'python/tags/r' + tag.nickname
    tag_url = urlunsplit((url.scheme, url.netloc, new_path,
                          url.query, url.fragment))
    run_cmd(['svn', 'copy', get_current_location(), tag_url])


NEWS_TEMPLATE = """
What's New in Python {XXX PUT NEXT VERSION HERE XXX}?
================================

*Release date: %s*

Core and Builtins
-----------------

Library
-------

"""

def update_news():
    print "Updating Misc/NEWS"
    with open('Misc/NEWS') as fp:
        lines = fp.readlines()
    for i, line in enumerate(lines):
        if line.startswith("(editors"):
            start = i
        if line.startswith("What's"):
            end = i
            break
    release_date = time.strftime("%d-%b-%Y")
    insert = NEWS_TEMPLATE % release_date
    with open('Misc/NEWS', 'w') as fp:
         fp.writelines(lines[:start+1])
         fp.write(insert)
         fp.writelines(lines[end-1:])
    print "Please fill in the the name of the next version."
    manual_edit('Misc/NEWS')


def done(tag):
    tweak_patchlevel(tag, done=True)
    update_news()


def main(argv):
    parser = get_arg_parser()
    options, args = parser.parse_args(argv)
    if len(args) != 2:
        parser.print_usage()
        sys.exit(1)
    tag = Tag(args[1])
    if not options.export:
        check_env()
    if options.bump:
        bump(tag)
    if options.tag:
        make_tag(tag)
    if options.branch:
        branch(tag)
    if options.export:
        export(tag)
    if options.done:
        done(tag)


if __name__ == '__main__':
    main(sys.argv)
