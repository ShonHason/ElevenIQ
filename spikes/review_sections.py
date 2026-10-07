"""Shared review files: each spike owns one marked section (user, 2026-10-07).

docs/reviews/m4-validation.md collects the M4g chunks. A spike regenerating its part must
leave every other part untouched, so each part sits between an owner's markers:

    <!-- BEGIN section: <owner> -->
    ...
    <!-- END section: <owner> -->

write_section replaces the owner's block in place, or appends it when the file has none, or
creates the file with a header when it does not exist. Malformed markers (a missing or
repeated BEGIN / END, or END before BEGIN) fail loudly instead of guessing. Never imports
eleveniq.
"""
from __future__ import annotations

from pathlib import Path

HEADER = ("# M4 validation (plan 005 M4g)\n\n"
          "Written section by section as the M4g chunks run; each section is owned by the "
          "spike named in its markers and is regenerated only by it. Report only unless a "
          "section says otherwise.\n")


class SectionError(ValueError):
    pass


def markers(owner: str) -> tuple[str, str]:
    if not owner or "-->" in owner or "\n" in owner:
        raise SectionError(f"bad owner name {owner!r}")
    return f"<!-- BEGIN section: {owner} -->", f"<!-- END section: {owner} -->"


def write_section(path: Path, owner: str, body: str, header: str = HEADER) -> None:
    """Write `body` as `owner`'s section of `path`, leaving the rest of the file as it is."""
    begin, end = markers(owner)
    block = f"{begin}\n{body.rstrip()}\n{end}\n"
    if not path.exists():
        path.write_text(header.rstrip() + "\n\n" + block)
        return
    text = path.read_text()
    nb, ne = text.count(begin), text.count(end)
    if nb == 0 and ne == 0:
        path.write_text(text.rstrip() + "\n\n" + block)
        return
    if nb != 1 or ne != 1:
        raise SectionError(f"{path.name}: {nb} BEGIN / {ne} END markers for {owner!r}")
    i, j = text.index(begin), text.index(end)
    if j < i:
        raise SectionError(f"{path.name}: END before BEGIN for {owner!r}")
    rest = text[j + len(end):]
    # `block` ends with the END marker's newline, so drop the old one.
    path.write_text(text[:i] + block + rest.removeprefix("\n"))
