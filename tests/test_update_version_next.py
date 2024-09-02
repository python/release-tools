"""Tests for the update_version_next tool."""

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

EXPECTED_CHANGED = TO_CHANGE.replace("next", "VER")


def test_freeze_simple_script(tmp_path) -> None:
    p = tmp_path.joinpath

    p("source.rst").write_text(TO_CHANGE + UNCHANGED)
    p("subdir").mkdir()
    p("subdir/change.rst").write_text(".. versionadded:: next")
    p("subdir/keep.not-rst").write_text(".. versionadded:: next")
    p("subdir/keep.rst").write_text("nothing to see here")
    args = ["VER", str(tmp_path)]
    update_version_next.main(args)
    assert p("source.rst").read_text() == EXPECTED_CHANGED + UNCHANGED
    assert p("subdir/change.rst").read_text() == ".. versionadded:: VER"
    assert p("subdir/keep.not-rst").read_text() == ".. versionadded:: next"
    assert p("subdir/keep.rst").read_text() == "nothing to see here"
