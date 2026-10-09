"""
Shared review files: a spike rewrites only its own marked section (spikes/review_sections.py;
user, 2026-10-07: docs/reviews/m4-validation.md collects several M4g chunks).
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "spikes"))
import review_sections as rs  # noqa: E402

FOREIGN = ("<!-- BEGIN section: m4g_reliability -->\n## M4g2 — reliability\n\n"
           "| a | b |\n| --- | --- |\n| 1 | 2 |\n<!-- END section: m4g_reliability -->\n")


def _file(tmp_path: Path, text: str) -> Path:
    p = tmp_path / "review.md"
    p.write_text(text)
    return p


def test_creates_the_file_with_a_header(tmp_path):
    p = tmp_path / "new.md"
    rs.write_section(p, "m4g_style_map", "## M4g1\n\nbody")
    text = p.read_text()
    assert text.startswith("# M4 validation")
    assert "<!-- BEGIN section: m4g_style_map -->\n## M4g1\n\nbody\n" in text
    assert text.endswith("<!-- END section: m4g_style_map -->\n")


def test_a_foreign_section_survives_regeneration(tmp_path):
    head = "# M4 validation\n\nintro written by hand\n\n"
    p = _file(tmp_path, head + FOREIGN)
    rs.write_section(p, "m4g_style_map", "## M4g1\n\nfirst")
    rs.write_section(p, "m4g_style_map", "## M4g1\n\nsecond")
    text = p.read_text()
    assert text.startswith(head + FOREIGN), "the foreign section or the intro changed"
    assert "second" in text and "first" not in text
    assert text.count("BEGIN section: m4g_style_map") == 1


def test_a_section_before_a_foreign_one_is_replaced_in_place(tmp_path):
    own = "<!-- BEGIN section: m4g_style_map -->\nold\n<!-- END section: m4g_style_map -->\n"
    p = _file(tmp_path, "# T\n\n" + own + "\n" + FOREIGN + "\ntrailing note\n")
    rs.write_section(p, "m4g_style_map", "new")
    assert p.read_text() == ("# T\n\n<!-- BEGIN section: m4g_style_map -->\nnew\n"
                             "<!-- END section: m4g_style_map -->\n\n" + FOREIGN
                             + "\ntrailing note\n")


def test_regeneration_is_idempotent(tmp_path):
    p = _file(tmp_path, "# T\n\n" + FOREIGN)
    rs.write_section(p, "m4g_style_map", "body")
    once = p.read_text()
    rs.write_section(p, "m4g_style_map", "body")
    assert p.read_text() == once


@pytest.mark.parametrize("text", [
    "<!-- BEGIN section: x -->\nno end\n",
    "no begin\n<!-- END section: x -->\n",
    "<!-- END section: x -->\n<!-- BEGIN section: x -->\n",
    "<!-- BEGIN section: x -->\n<!-- END section: x -->\n<!-- BEGIN section: x -->\n"
    "<!-- END section: x -->\n",
])
def test_malformed_markers_fail_and_leave_the_file_alone(tmp_path, text):
    p = _file(tmp_path, text)
    with pytest.raises(rs.SectionError):
        rs.write_section(p, "x", "body")
    assert p.read_text() == text


def test_owner_names_cannot_break_the_markers():
    for bad in ("", "a-->b", "a\nb"):
        with pytest.raises(rs.SectionError):
            rs.markers(bad)
