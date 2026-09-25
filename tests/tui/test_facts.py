from __future__ import annotations

from cuanta.tui.widgets.facts import wrapped_facts


def test_long_values_wrap_inside_their_own_column() -> None:
    evidence = "tsc + eslint + build, no test runner found in package.json scripts"
    text = str(wrapped_facts([("Name", "sample-landing"), ("Evidence", evidence)], 12, 40))
    lines = text.splitlines()
    assert lines[0].startswith("Name        sample-landing")
    assert lines[1].startswith("Evidence    ")
    assert len(lines) > 2
    assert all(line.startswith(" " * 12) for line in lines[2:])
    assert all(len(line) <= 40 for line in lines)


def test_without_a_width_values_stay_on_one_line() -> None:
    text = str(wrapped_facts([("Name", "shop")], 12, 0))
    assert text == "Name        shop"
