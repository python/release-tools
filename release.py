"An assistant for making Python releases by Benjamin Peterson"
#!/usr/bin/env python
from __future__ import with_statement

import sys
import os
import optparse
import re
import subprocess
import shutil
import tempfile

# Ideas stolen from Mailman's release script, Lib/tokens.py and welease

def error(*msgs):
    print >> sys.stderr, "**ERROR**"
    for msg in msgs:
        print >> sys.stderr, msg
    sys.exit(1)

def run_cmd(args, silent=False):
    cmd = " ".join(args)
    if not silent:
        print "Executing %s" % cmd
    try:
        if silent:
            code = subprocess.call(cmd, shell=True, stdout=PIPE)
        else:
            code = subprocess.call(cmd, shell=True)
    except OSError:
        error("%s failed" % cmd)

def check_env():
    if "EDITOR" not in os.environ:
        error("editor not detected.",
            "Please set your EDITOR enviroment variable")
    if not os.path.exists(".svn"):
        error("CWD is not a Subversion checkout")
        
def get_arg_parser():
    usage = "%prog [options] tagname"
    p = optparse.OptionParser(usage=usage)
    p.add_option("-b", "--bump",
        default=False, action="store_true",
        help="bump the revision number in important files")
    p.add_option("-e", "--export",
        default=False, action="store_true",
        help="Export the SVN tag to a tarball")
    p.add_option("-m", "--branch",
        default=False, action="store_true",
        help="create a maintance branch to go along with the release")
    p.add_option("-t", "--tag",
        default=False, action="store_true",
        help="Tag the release in Subversion")
    return p

def constant_replace(fn, updated_constants, comment_start="/*", comment_end="*/"):
    "Inserts in between --start constant-- and --end constant-- in a file"
    start_tag = comment_start + "--start constants--" + comment_end
    end_tag = comment_start + "--end constants--" + comment_end
    with open(fn) as fp:
        lines = fp.read().splitlines()
    try:
        start = lines.index(start_tag) + 1
        end = lines.index(end_tag)
    except ValueError:
        error("%s doesn't have constant tags" % fn)
    lines[start:end] = [updated_constants]
    with open(fn, "w") as fp:
        fp.write("\n".join(lines))

def bump(tag):
    print "Bumping version to %s" % tag
    
    wanted_file = "Misc/RPM/python-%s.spec" % tag.basic_version
    print "Updating %s" % wanted_file,
    if not os.path.exists(wanted_file):
        specs = os.listdir("Misc/RPM/")
        for file in specs:
            if file.startswith("python-"):
                break
        full_path = os.path.join("Misc/RPM/", file)
        print "\nrenaming %s to %s" % (full_path, wanted_file)
        run_cmd(["svn", "rename", "--force", full_path, wanted_file])
        print "File was renamed; please commit"
        run_cmd(["svn", "commit"])
    new = "%define version " + tag.text + \
        "\n%define libver " + tag.basic_version
    constant_replace(wanted_file, new, "#", "")
    print "done"
    
    print "Updating Include/patchlevel.h...",
    template = """#define PY_MAJOR_VERSION	[major]
#define PY_MINOR_VERSION	[minor]
#define PY_MICRO_VERSION	[patch]
#define PY_RELEASE_LEVEL    [level]
#define PY_RELEASE_SERIAL	[serial]
#define PY_VERSION  \"[text]\""""
    for what in ("major", "minor", "patch", "serial", "text"):
        template = template.replace("[" + what + "]", str(getattr(tag, what)))
    level_defines = {"a" : "PY_RELEASE_LEVEL_ALPHA",
    "b" : "PY_RELEASE_LEVEL_BETA",
    "c" : "PY_RELEASE_LEVEL_GAMMA",
    "f" : "PY_RELEASE_LEVEL_FINAL"}
    template = template.replace("[level]", level_defines[tag.level])
    constant_replace("Include/patchlevel.h", template)
    print "done"
    
    print "Updating Lib/idlelib/idlever.py...",
    with open("Lib/idlelib/idlever.py", "w") as fp:
        new = "IDLE_VERSION = \"%s\"\n" % tag.next_text
        fp.write(new)
    print "done"
    
    print "Updating Lib/distutils/__init__.py...",
    new = "__version__ = \"%s\"" % tag.text
    constant_replace("Lib/distutils/__init__.py", new, "#", "")
    print "done"
    
    other_files = ["README"]
    if tag.patch == 0 and tag.level == "a" and tag.serial == 0:
        other_files += ["Doc/tutorial/interpreter.rst",
            "Doc/tutorial/stdlib.rst", "Doc/tutorial/stdlib2.rst"]
    print "\nManual editing time..."
    for fn in other_files:
        print "Edit %s" % fn
        manual_edit(fn)
    
    print "Bumped revision"
    print "Please commit and use --tag"

def manual_edit(fn):
    run_cmd([os.environ["EDITOR"], fn])

def export(tag):
    temp_dir = tempfile.mkdtemp("pyrelease")
    if not os.path.exists("dist") and not os.path.isdir("dist"):
        print "creating dist directory"
        os.mkdir("dist")
    tgz = "dist/Python-%s.tgz" % tag.text
    bz = "dist/Python-%s.tar.bz2" % tag.text
    old_cur = os.getcwd()
    os.chdir(temp_dir)
    try:
        try:
            print "Exporting tag"
            run_cmd(["svn", "export",
                "http://svn.python.org/projects/python/tags/r%s"
                % tag.text.replace(".", ""), "release"])
            print "Making .tgz"
            run_cmd(["tar cf - release | gzip -9 > release.tgz"])
            print "Making .tar.bz2"
            run_cmd(["tar cf - release "
                "| bzip2 -9 > release.tar.bz2"])
        finally:
            os.chdir(old_cur)
        print "Moving files to dist"
        os.rename(os.path.join(temp_dir, "release.tgz"), tgz)
        os.rename(os.path.join(temp_dir, "release.tar.bz2"), bz)
    finally:
        print "Cleaning up"
        shutil.rmtree(temp_dir)
    print "Calculating md5sums"
    run_cmd(["md5sum", tgz, ">", tgz + ".md5"])
    run_cmd(["md5sum", bz, ">", bz + ".md5"])
    print "**Now extract the archives and run the tests**"

class Tag:
    def __init__(self, text, major, minor, patch, level, serial):
        self.text = text
        self.next_text = self.text
        self.major = major
        self.minor = minor
        self.patch = patch
        self.level = level
        self.serial = serial
        self.basic_version = major + "." + minor
    
    def __str__(self):
        return self.text

def break_up_tag(tag):
    exp = re.compile(r"(\d+)(?:\.(\d+)(?:\.(\d+))?)?(?:([abc])(\d+))?")
    result = exp.search(tag)
    if result is None:
        error("tag %s is not valid" % tag)
    data = list(result.groups())
    # fix None level
    if data[3] is None:
        data[3] = "f"
    # None Everythign else should be 0
    for i, thing in enumerate(data):
        if thing is None:
            data[i] = 0
    return Tag(tag, *data)

def branch(tag):
    if tag.minor > 0 or tag.patch > 0 or tag.level != "f":
        print "It doesn't look like your making a final release."
        if raw_input("Are you sure you want to branch?") != "y":
            return
    run_cmd(["svn", "copy", get_current_location(),
        "svn+ssh://svn.python.org/projects/python/branches/" 
            "release%s-maint" % (tag.major + tag.minor)])

def get_current_location():
    data = subprocess.Popen("svn info", shell=True,
        stdout=subprocess.PIPE).stdout.read().splitlines()
    for line in data:
        if line.startswith("URL: "):
            return line.lstrip("URL: ")

def make_tag(tag):
    run_cmd(["svn", "copy", get_current_location(),
        "svn+ssh://svn.python.org/projects/python/tags/r"
        + tag.text.replace(".", "")])

def main(argv):
    parser = get_arg_parser()
    options, args = parser.parse_args(argv)
    if len(args) != 2:
        parser.print_usage()
        sys.exit(1)
    tag = break_up_tag(args[1])
    if not options.export:
        check_env()
    if options.bump:
        bump(tag)
    elif options.tag:
        make_tag(tag)
    elif options.branch:
        branch(tag)
    elif options.export:
        export(tag)

if __name__ == "__main__":
    main(sys.argv)
