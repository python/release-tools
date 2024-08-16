"""Tests for the update_version_next tool."""

from pathlib import Path
import unittest

from test.support import os_helper

import update_version_next

TO_CHANGE = """
Directives to change
--------------------

Here, all occurences of NEXT (lowercase) should be changed:

.. versionadded:: next

.. versionchanged:: next

.. deprecated:: next

.. deprecated-removed:: next 4.0

whitespace:

..   versionchanged:: next

.. versionchanged  :: next

    .. versionadded:: next

arguments:

.. versionadded:: next
    Foo bar

.. versionadded:: next as ``previousname``
"""

UNCHANGED = """
Unchanged
---------

Here, the word "next" should NOT be changed:

.. versionchanged:: NEXT

..versionchanged:: NEXT

... versionchanged:: next

foo .. versionchanged:: next

.. otherdirective:: next

.. VERSIONCHANGED: next

.. deprecated-removed: 3.0 next
"""

EXPECTED_CHANGED = TO_CHANGE.replace('next', 'VER')


class TestVersionNext(unittest.TestCase):
    maxDiff = len(TO_CHANGE + UNCHANGED) * 10

    def test_freeze_simple_script(self):
        with os_helper.temp_dir() as testdir:
            path = Path(testdir)
            path.joinpath('source.rst').write_text(TO_CHANGE + UNCHANGED)
            path.joinpath('subdir').mkdir()
            path.joinpath('subdir/change.rst').write_text(
                '.. versionadded:: next')
            path.joinpath('subdir/keep.not-rst').write_text(
                '.. versionadded:: next')
            path.joinpath('subdir/keep.rst').write_text(
                'nothing to see here')
            args = ['VER', testdir]
            update_version_next.main(args)
            self.assertEqual(path.joinpath('source.rst').read_text(),
                             EXPECTED_CHANGED + UNCHANGED)
            self.assertEqual(path.joinpath('subdir/change.rst').read_text(),
                             '.. versionadded:: VER')
            self.assertEqual(path.joinpath('subdir/keep.not-rst').read_text(),
                             '.. versionadded:: next')
            self.assertEqual(path.joinpath('subdir/keep.rst').read_text(),
                             'nothing to see here')
