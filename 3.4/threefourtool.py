#!/usr/bin/env python3

#
# todo:
#
# change status page
#  * get rid of "merged" vs "unmerged" lists
#    instead, just have one list, with each revision marked merged or unmerged.
#    make it obvious, like have unmerged indented or something.

import atexit
import collections
import datetime
from dryparse import dryparse
import glob
import os
import pprint
import pickle
import shutil
import subprocess
import sys
import tempfile
import time


outgoing = "/home/larry/src/python/34outgoing"


def yes_no():
    while True:
        s = input("Are you sure? (y/n) >")
        if s.strip() in 'yn':
            return s


def now():
    s = str(datetime.datetime.now())
    for c in "- :":
        s = s.replace(c, '.')
    fields = s.split('.')
    s = '.'.join(fields[:-1])
    return s

def line_to_rev(line):
    if line.startswith("changeset:"):
        line = line[len("changeset:"):].strip()
    local, _, rev = line.strip().partition(':')
    assert _ == ':'
    rev = rev.strip()
    assert rev.isalnum()
    assert local.isdigit()
    # print(repr(line), rev)
    return local, rev


changesets = collections.OrderedDict()
user_date_to_revs = {} # "user date" == "{user} {date}", maps to (rev, branch)
default_to_34 = {} # maps rev in default to rev in 3.4
default_from_34 = {} # maps rev in 3.4 to rev in default
revs = []
branches = {}

def reset_changesets():
    changesets.clear()
    user_date_to_revs.clear()
    default_to_34.clear()
    default_from_34.clear()
    revs.clear()
    branches.clear()

def line_iterator(pipe, encoding='utf-8'):
    text = b''
    while True:
        got = pipe.read(128)
        if not got:
            break
        # print("GOT", repr(got))
        text += got
        fields = text.split(b'\n')
        text = fields.pop()
        for line in fields:
            # print("LINE YIELD", repr(line))
            yield line.decode(encoding)
    if text:
        # print("LINE YIELD LAST", repr(line))
        yield text.decode(encoding)

def changeset_iterator(pipe):
    i = line_iterator(pipe)
    lines = []

    for line in i:
        if line.startswith("changeset: "):
            break
    else:
        return

    lines.append(line)
    for line in i:
        if line.startswith("changeset: "):
            # print("CS YIELD", lines)
            yield "\n".join(lines)
            lines = [line]
            continue
        lines.append(line)
    assert lines
    # print("CS YIELD LAST", lines)
    yield "\n".join(lines)

def get_user_date_to_revs(fields):
    return user_date_to_revs[fields['user'] + ' ' + fields['date']]

def read_changesets(earliest = '6343bdbb7085', force=False):
    if changesets and not force:
        return

    reset_changesets()

    p = None
    current_directory = os.getcwd()
    try:
        os.chdir("/home/larry/src/python/3.4")
        p = subprocess.Popen(["/usr/bin/hg", "log"], stdout=subprocess.PIPE)
        # with open("/home/larry/src/python/logtxt", "rt", encoding="utf-8") as f:
            # output = f.read()

        fixup_parent = False
        for c in changeset_iterator(p.stdout):
            lines = c.split('\n')
            # print("REV", end='')
            local, rev = line_to_rev(lines[0])
            if fixup_parent:
                # parents is still set to the previous rev's parents list!
                parents.append(rev)
                fixup_parent = False
            in_description = False
            description = []
            parents = []
            fields = {'description': description, 'parents': parents, 'local': local}
            for line in lines[1:]:
                if in_description:
                    description.append(line.strip())
                    continue
                line = line.strip()
                if not line:
                    continue
                field, _, text = line.partition(':')
                assert _ == ':', "no colon in line: " + repr(line)
                field = field.strip()
                text = text.strip()
                if field == 'parent':
                    # print("PARENT", end='')
                    parent = line_to_rev(text)[1]
                    parents.append(parent)
                elif field == 'description':
                    in_description = True
                    if text:
                        description.append(text)
                else:
                    fields[field] = text
            # clean up description
            fields['description'] = '\n'.join(description).strip().split('\n')
            branch = fields.get('branch')
            user = fields['user']
            date = fields['date']

            changesets[rev] = fields
            user_date = "{} {}".format(user, date)
            if user_date not in user_date_to_revs:
                user_date_to_revs[user_date] = set()
            user_date_to_revs[user_date].add((rev, branch))
            revs.append(rev)
            if not branch in branches:
                branches[branch] = []
            assert rev not in branches[branch], "Adding rev " + rev + " twice to branch " + repr(branch) + "!"
            branches[branch].append(rev)
            if rev == earliest:
                break
            if not parents:
                fixup_parent = True

    finally:
        os.chdir(current_directory)
        if p:
            p.stdout.close()
        for r in user_date_to_revs.values():
            d = dict((branch, rev) for rev, branch in r)
            if '3.4' in d and None in d:
                default_to_34[d[None]] = d["3.4"]
                default_from_34[d['3.4']] = d[None]

def header(category, printer):
    # print(category)
    printer("""
<h2>{}</h2>
<table border=0 cellpadding=5px>
""".format(category))

def footer(printer):
    printer("""
</td></tr></table>
""")

def print_rev(rev, printer):
    fields = changesets[rev]
    d = dict(fields)
    description = fields['description']
    d['rev'] = rev
    d['description0'] = description[0]
    d['description1+'] = "<br/>\n".join(description[1:])
    # print("    r", rev)
    printer("""
<tr>

<td bgcolor=#e0e0e>
<tt><font size=+1><b>
<a href=http://hg.python.org/cpython/rev/{rev}>{rev}</a>
</b></font></tt>
</td>

<td bgcolor=#f0f0f0>
{user}
</td>

<td bgcolor=#e0e0e><tt>
{date}
</tt></td>

<td bgcolor=#f0f0f0>
<a href="javascript:toggle('{rev}');">{description0}</a>
<div id="{rev}" style="display: none">
{description1+}
</div>
</td></tr>
""".format_map(d))


def print_revs(print_test, lineage_test, printer):
    seen = set()

    for rev, fields in changesets.items():
        # print("  :", rev)
        rev_to_print = print_test(rev)
        if rev_to_print:
            break
    else:
        return

    print_rev(rev_to_print, printer)
    seen.add(rev_to_print)

    hopper = []
    def refill_hopper(parents):
        hopper.extend([(revs.index(r), r) for r in parents])
        hopper.sort()

    parents = fields['parents']
    # print("  r", rev, "p", parents)
    refill_hopper(parents)
    while hopper:
        _, rev = hopper.pop(0)
        # print("  ?", rev)
        if not lineage_test(rev):
            continue
        fields = changesets[rev]
        parents = fields['parents']
        refill_hopper(parents)

        rev_to_print = print_test(rev)
        if not rev_to_print:
            continue
        # print("  r", rev, "p", parents, "rev to print", rev_to_print)
        if rev_to_print in seen:
            continue
        seen.add(rev_to_print)
        print_rev(rev_to_print, printer)

def is_default(rev):
    fields = changesets[rev]
    branch = fields.get('branch')
    if branch:
        return False
    return rev


def is_default_and_not_34(rev):
    if not is_default(rev):
        return False
    fields = changesets[rev]
    for rev, branch in get_user_date_to_revs(fields):
        if is_34(rev):
            return False
    return rev

def is_34(rev):
    fields = changesets[rev]
    branch = fields.get('branch')
    if branch != '3.4':
        return False
    revs = get_user_date_to_revs(fields)
    assert (rev, branch) in revs
    # print("is 34?", rev, revs)
    for r2, branch in revs:
        if not branch:
            # print(rev, "->", r2)
            return r2
    return False

class Tool:
    unfinished_filename = "/home/larry/.34unfinished"

    def __init__(self):
        self.unfinished = None
        self._load()

    def _load(self):
        if not os.path.isfile(self.unfinished_filename):
            return
        try:
            with open(self.unfinished_filename, "rb") as f:
                self.unfinished = pickle.load(f)
        except EOFError:
            self.unfinished = None

    def _save(self):
        with open(self.unfinished_filename, "wb") as f:
            pickle.dump(self.unfinished, f)

    def _abandon(self):
        self.unfinished = None
        try:
            os.unlink(self.unfinished_filename)
        except OSError:
            pass

    def status(self):
        """
        Regenerate the status webpage.
        """
        f = open(outgoing + "/merge.status.html", "wt")

        read_changesets()

        def printer(*a):
            print(*a, file=f)

        printer("""
<html>
<head>
<meta charset="UTF-8"/>
<title>Python 3.4 rc2 merge status</title>

<style type="text/css">
a { color: black; text-decoration:none; }
</style>

<script language="javascript">
function toggle(element) {
    document.getElementById(element).style.display = (document.getElementById(element).style.display == "none") ? "" : "none";
}
</script>

</head>
<body>
<p>
<font size=6>
<img src=http://www.python.org/images/python-logo.gif align=center>
Python 3.4 rc2 merge status
</font></p>
""")

        # printer("<div style='background-color:#ffc0c0;'><font size=7 color=#800000>Notice: this is <b>BETA</b> (don't take it seriously yet)</font></div>")
        header("Merged", printer)
        print_revs(is_34, is_34, printer)
        footer(printer)

        header("Unmerged", printer)
        print_revs(is_default_and_not_34, is_default, printer)
        footer(printer)

        for rev in changesets:
            if is_default(rev):
                break

        printer("""
<hr/>
<p><tt>Generated {now}<br/>Current head of default branch is <a href=http://hg.python.org/cpython/rev/{rev}>{rev}</a></tt></p>
""".format(now=datetime.datetime.now(), rev=rev))

        printer("</body></html>")
        f.close()


    def _run_command(self, commands, u):
        commands_run = u['commands run']

        while True:
            print("Commands:")
            commands_set = set('h.dq')
            for i, (text, cmd) in enumerate(commands):
                label = str(i+1)
                commands_set.add(label)
                label = ' ' + label + ' '
                if text in commands_run:
                    label = label.replace(' ', '*')
                print("   ", label, text)
                if isinstance(cmd, str):
                    cmd = cmd.format_map(u)
                else:
                    cmd = "(python fn " + cmd.__name__ + ")"
                print("       ", cmd)

            print("     h  Simple help on commands")
            s = ''
            while s not in commands_set:
                s = input("[> ").strip()
            if s == '.':
                continue
            if s == 'd':
                pprint.pprint(u)
                print()
                continue
            if s == 'q':
                return
            if s == 'h':
                print("""
Simple help

. - repeat the command menu
h - print this help message
d - print the internal "unfinished" dict
q - quit
""")
                continue
            break

        i = int(s) - 1
        text, cmd = commands[i]
        commands_run.add(text)
        if isinstance(cmd, str):
            print()
            cmd = cmd.format_map(u)
            result = os.system(cmd)
            print()
            if result != 0:
                print("*" * 79)
                print("*" * 79)
                print("** Process return code:", result )
                print("*" * 79)
                print("*" * 79)
        else:
            cmd()

    def pick(self, picked_revision, *picked_revisions):
        """
        Cherry-pick a revision from default to 3.4.
        """
        pr = [picked_revision]
        pr.extend(picked_revisions)
        picked_revisions = pr

        read_changesets()

        # sort earliest first
        for rev in picked_revisions:
            if rev not in branches[None]:
                sys.exit("Can't pick revision " + rev + ", it's not in default.")

        def to_default_index(rev):
            # revisions are sorted latest first,
            # we want the opposite, so we negate
            return -branches[None].index(rev)
        picked_revisions.sort(key=to_default_index)

        if self.unfinished:
            if self.unfinished['original picked revisions'] == picked_revisions:
                return self.finish()
            sys.exit("You have unfinished business!\n\nUse the 'finish' command to finish it,\nor the 'abandon' command to abandon it.")

        self.unfinished = {
            'function': '_pick',
            'picked revisions': picked_revisions,
            'original picked revisions': list(picked_revisions),
            }
        self.finish()


    def _analyze_picked_revision(self):
        read_changesets(force=True)
        picked_revisions = self.unfinished['picked revisions']
        # print("picked_revisions", picked_revisions)
        while picked_revisions:
            # changesets = collections.OrderedDict()
            # user_date_to_revs = {} # "user date" == "{user} {date}", maps to (rev, branch)
            # revs = []
            # branches = {}
            picked_revision = picked_revisions[0]
            try:
                index = branches[None].index(picked_revision)
            except ValueError:
                sys.exit("{} is not a revision in Python trunk.".format(picked_revision))
            r_34_head = branches['3.4'][0]
            r_34_first_revision = branches['3.4'][-1]
            # print("index", index)
            # print("3.4 branch", branches['3.4'])
            # print("default_from_34", default_from_34)
            r_previous = None
            # find where it should go in 3.4
            i = None
            for r in branches['3.4']:
                # print("r", r)
                if r == r_34_first_revision:
                    break
                r_default = default_from_34[r]
                i = branches[None].index(r_default)
                if i >= index:
                    break
                r_previous = r
            else:
                sys.exit("Unexpected non-termination when searching for right spot to graft to!")

            if i == index:
                print("{} already in 3.4!".format(picked_revision))
                picked_revisions.pop(0)
                continue

            if r == r_34_head:
                r_rebase_from = None
            else:
                r_rebase_from = r_previous

            # print("chose r", r, "r_34_head", r_34_head, "r_rebase_from", r_rebase_from)


            # figure out previous revision in default, in case we need to make a patch
            diff_from = branches[None][index + 1]

            fields = changesets[picked_revision]

            u2 = dict(self.unfinished)

            if 'done' in u2:
                del u2['done']

            u2.update({
                'threefour rebase from': r_rebase_from,
                'threefour graft here': r,
                'default picked revision': picked_revision,
                'user': fields['user'],
                'date': fields['date'],
                'description': '\n'.join(fields['description']),
                'default diff from': diff_from,
                'threefour picked revision': 'UNKNOWN (detect via "Detect new revision")',
                'commands run': set(),
                })
            picked_revisions.pop(0)
            self.unfinished = u2
            break

    def _pick(self):
        read_changesets()
        os.chdir("/home/larry/src/python/3.4")
        print("Updating to 3.4 branch:")
        os.system("hg update -r 3.4")
        print()
        u = self.unfinished.get
        print("Picking revisions ", u('picked revisions'))
        while True:
            picked_revisions = u('picked revisions')
            if not picked_revisions:
                break
            if 'default picked revision' not in self.unfinished:
                self._analyze_picked_revision()
            if 'default picked revision' in self.unfinished:
                self._pick_revision()
        if not u('picked revisions'):
            self._abandon()

    def _pick_revision(self):
        u = self.unfinished

        u['EDITOR'] = os.getenv('EDITOR')

        patch_path = "/tmp/patch.{default diff from}.to.{default picked revision}.diff".format_map(u)
        u['patch path'] = patch_path

        f, commit_message_path = tempfile.mkstemp(suffix='txt')
        os.close(f)
        with open(commit_message_path, 'wt') as f:
            f.write(u['description'])
        u['commit message path'] = commit_message_path

        def delete_files(*a):
            for path in a:
                try:
                    os.unlink(path)
                except OSError:
                    pass

        atexit.register(delete_files, patch_path, commit_message_path)

        def detect_new_revision():
            output = subprocess.check_output(['/usr/bin/hg', 'summary']).decode('utf-8').split('\n')
            line = output[0]
            assert line.startswith('parent:')
            line = line[len('parent:'):]
            local, colon, r = line.partition(':')
            assert colon == ':'
            r = r.split()[0].strip()
            u['threefour picked revision'] = r

        show_patch = False
        def toggle_patch():
            nonlocal show_patch
            show_patch = not show_patch

        def mark_as_picked():
            del self.unfinished['default picked revision']
            print()
            print("_" * 79)
            print("_" * 79)
            print()

        while u.get('default picked revision'):

            commands = []
            commands.append(("Update to appropriate revision in 3.4 branch", "hg update -r {threefour graft here}"))
            commands.append(("Graft revision", "hg graft {default picked revision}"))
            commands.append(("Patch revision (only if graft fails)", toggle_patch))

            if show_patch:
                commands.append(("[graft failed step 1] Generate patch", "/usr/bin/hg diff -r {default diff from} -r {default picked revision} > {patch path}"))
                commands.append(("[graft failed step 2] Inspect patch", "{EDITOR} {patch path}"))
                commands.append(("[graft failed step 3] Apply patch", "/usr/bin/patch -p1 < {patch path}"))
                commands.append(("[graft failed step 4] Check in patch", "/usr/bin/hg ci --user '{user}' --date '{date}' --logfile '{commit message path}'"))

            if u.get('threefour rebase from'):
                commands.append(("Detect new revision", detect_new_revision))
                c = "hg rebase --source {threefour rebase from} --dest {threefour picked revision}"
                commands.append(("Rebase subsequent revisions after grafted revision", c))
                commands.append(("Update to head of 3.4 branch", "hg update -r 3.4"))

            commands.append(("Mark revision as picked", mark_as_picked))

            commands.append(("Print details of picked revision", "hg log -r {default picked revision}"))

            print()
            total = len(u['original picked revisions'])
            current = len(u['picked revisions'])
            print("Picking revision {default picked revision}".format_map(u) + " ({}/{}):".format(total-current, total))
            self._run_command(commands, u)

    def finish(self):
        try:
            if not self.unfinished:
                sys.exit("No unfinished business!")
            function = self.unfinished.get('function')
            if not function:
                sys.exit("Unfinished state is invalid!")
            f = getattr(self, function, None)
            if not f:
                sys.exit("Unfinished state is invalid!")
            f()
        except KeyboardInterrupt:
            self._save()

    def abandon(self, *, force:('-f',)=False):
        if not self.unfinished:
            sys.exit("No unfinished business!")
        if force or yes_no() == 'y':
            self._abandon()

    def recreate(self, *, force:('-f',)=False):
        """
        Recreate the 3.4 branch from scratch.
        """
        if (not force) and (yes_no() == 'n'):
            sys.exit()
        os.chdir("/home/larry/src/python")
        while True:
            if os.path.isdir("/home/larry/src/python/bad3.4"):
                shutil.rmtree("/home/larry/src/python/bad3.4")
                continue
            if os.path.isdir("/home/larry/src/python/3.4"):
                os.rename("/home/larry/src/python/3.4", "/home/larry/src/python/bad3.4")
                continue
            break
        os.system("hg clone trunk 3.4")
        os.chdir("/home/larry/src/python/3.4")
        os.system("hg update -r e64ae8b82672")
        os.system("hg branch 3.4")
        os.system("hg commit -m 'Created release branch for 3.4.'")

    def tar(self):
        time = now()
        tardir = "/home/larry/src/python/python-" + time
        def remove_dir(dir):
            if os.path.isdir(dir):
                shutil.rmtree(dir)
                if os.path.isdir(dir):
                    sys.exit("Couldn't remove directory" + repr(dir))
        remove_dir(tardir)

        os.chdir("/home/larry/src/python")
        os.system("hg clone 3.4 " + tardir)

        os.chdir(tardir)
        remove_dir(".hg")
        for prefix in ('.hg', '.bzr', '.git'):
            for filename in glob.glob(prefix + '*'):
                os.unlink(filename)
        os.chdir("/home/larry/src/python")
        os.system("tar cvfz " + outgoing + "/python.3.4.{}.tgz python-{}".format(time, time))

        remove_dir(tardir)

    def rsync(self):
        os.chdir(outgoing)
        os.system("rsync -av * midwinter.com:public_html/3.4.status")


    def asyncio(self):
        for dir in ("Lib/asyncio", "Lib/test/test_asyncio"):
            os.chdir("/home/larry/src/python/3.4/" + dir)
            os.system("diff . /home/larry/src/python/trunk/" + dir)
        os.chdir("/home/larry/src/python/3.4/Doc/library")
        for file in glob.glob("asyncio*"):
            f1 = open(file, "rt").read()
            f2 = open("/home/larry/src/python/trunk/Doc/library/" + file, "rt").read()
            if f1 != f2:
                cmd = "diff {file} /home/larry/src/python/trunk/Doc/library/{file}".format(file=file)
                print(cmd)
                os.system(cmd)



t = Tool()

dp = dryparse.DryParse()
dp.update(t)
dp.main()