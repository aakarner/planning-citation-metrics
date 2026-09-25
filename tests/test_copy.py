"""The content file is edited by hand, so the build must fail loudly on a typo
rather than shipping a page with a hole in it."""

import pytest

from pipeline.build_site import COPY, COPY_PATH, load_copy, t


def test_the_content_file_parses_and_has_every_section():
    assert set(COPY) >= {"site", "home", "person", "department", "rankings", "departments", "methods"}


def test_a_passage_fills_in_its_live_values():
    out = t("home", "lede", people_count="1,028", dept_count=120)
    assert "1,028" in out and "120" in out and "{" not in out


def test_line_breaks_in_the_file_are_cosmetic():
    # The file tells editors to wrap wherever it reads well, so wrapping must
    # not leak into the page.
    assert "\n" not in t("home", "beta_body_1")
    assert "  " not in t("methods", "inclusion_body")


def test_an_unknown_placeholder_stops_the_build_and_says_what_is_available():
    with pytest.raises(SystemExit) as err:
        t("home", "lede", people_count="1")          # dept_count omitted
    msg = str(err.value)
    assert "dept_count" in msg and "people_count" in msg and str(COPY_PATH) in msg


def test_an_unknown_key_stops_the_build_and_lists_the_real_ones():
    with pytest.raises(SystemExit) as err:
        t("home", "no_such_key")
    assert "no_such_key" in str(err.value) and "heading" in str(err.value)


def test_a_malformed_file_is_reported_as_such(tmp_path):
    bad = tmp_path / "copy.toml"
    bad.write_text('[home]\nheading = "unterminated\n')
    with pytest.raises(SystemExit) as err:
        load_copy(bad)
    assert "not valid TOML" in str(err.value)


def test_a_missing_file_is_reported_as_such(tmp_path):
    with pytest.raises(SystemExit) as err:
        load_copy(tmp_path / "absent.toml")
    assert "no copy file" in str(err.value)


def test_every_passage_in_the_file_is_reachable_with_the_values_it_documents():
    """Fill every passage with a permissive stand-in for any placeholder it
    names. This catches a malformed brace anywhere in the file, which is the
    failure a copy editor is most likely to introduce."""
    import re
    for section, entries in COPY.items():
        for key, template in entries.items():
            names = set(re.findall(r"\{(\w+)\}", template))
            t(section, key, **{n: "x" for n in names})


def test_a_publish_or_perish_page_does_not_talk_about_openalex():
    from pipeline.build_site import fallback_note
    pop = fallback_note("pop", scholar_help="x", collected_at="2026-02-20")
    oa = fallback_note("openalex", scholar_help="x", collected_at="2026-09-19")
    assert "OpenAlex indexes" not in pop and "Publish or Perish" in pop and "2026-02-20" in pop
    assert "OpenAlex" in oa and "Publish or Perish" not in oa
