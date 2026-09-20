"""The citation-ratio audit: which OpenAlex matches the numbers rule out.

OpenAlex is journal-centric and normally lands under the Scholar or Publish or
Perish figure we already hold, so a candidate claiming many times that number
is a different person with the same name. Every figure below is real, from the
2026-09-15 collection.

The asymmetry is the point. A candidate with far *more* citations than the base
is a namesake, and accepting one publishes a stranger's count -- that is how the
Stanford economist Nicholas Bloom reached the top of the homepage. A candidate
with far *fewer* is usually OpenAlex holding a fragment of the right person, so
pending rows are audited on the high side only.
"""

from pathlib import Path

from pipeline.audit_matches import MAX_RATIO, MIN_BASE, implausible

ROOT = Path(__file__).resolve().parent.parent


def test_a_namesake_with_far_more_citations_is_ruled_out():
    # Guadalupe Garcia-Tsao, a hepatologist, against a planning academic's 63.
    assert implausible(52281, 63) is not None
    assert implausible(6031, 25) is not None          # James Smither
    assert implausible(14351, 105) is not None        # John Park
    assert implausible(87154, 1568) is not None       # Yang Zhang


def test_a_plausible_count_survives():
    for oa, base in [(314, 407), (3496, 4790), (1970, 4209), (733, 1381), (100, 69)]:
        assert implausible(oa, base) is None, (oa, base)


def test_a_sparse_record_is_ruled_out_only_when_both_directions_count():
    # Reid Ewing's OpenAlex fragments: one work each, against Scholar's 51,777.
    # An accepted id showing this is wrong; a pending candidate is just a fragment.
    for oa in (11, 6, 1):
        assert implausible(oa, 51777) is not None
        assert implausible(oa, 51777, high_only=True) is None


def test_a_namesake_is_ruled_out_in_either_mode():
    assert implausible(52281, 63, high_only=True) is not None
    assert implausible(52281, 63) is not None


def test_a_small_base_is_too_noisy_to_judge():
    assert implausible(4580, MIN_BASE - 1) is None    # would be 191x, but the base is noise
    assert implausible(0, 1) is None
    assert implausible(500, 0) is None
    assert implausible(500, None) is None


def test_a_missing_openalex_count_decides_nothing():
    assert implausible(None, 5000) is None


def test_the_threshold_is_not_inclusive():
    base = 1000
    assert implausible(base * MAX_RATIO, base) is None
    assert implausible(base * MAX_RATIO + 1, base) is not None


def test_scholar_is_preferred_over_publish_or_perish_as_the_base():
    """A person can hold both. Scholar is the stronger baseline, and the older
    query let a join return two rows and decide by row order."""
    import sqlite3

    from pipeline.audit_matches import base_counts

    con = sqlite3.connect(":memory:")
    con.row_factory = sqlite3.Row
    con.execute("CREATE TABLE v_latest_metrics (person_id, source, total_citations)")
    con.executemany("INSERT INTO v_latest_metrics VALUES (?,?,?)", [
        (1, "pop", 400), (1, "google_scholar", 1500),      # both, either order
        (2, "google_scholar", 900), (2, "pop", 120),
        (3, "pop", 250),                                    # PoP only
        (4, "google_scholar", None),                        # no figure at all
    ])
    base = base_counts(con)
    assert base["1"] == ("google_scholar", 1500)
    assert base["2"] == ("google_scholar", 900)
    assert base["3"] == ("pop", 250)
    assert "4" not in base


def test_un_accepting_a_match_also_drops_the_orcid_copied_from_it():
    """The matcher copies an accepted record's ORCID onto the roster. When the
    audit later rejects that record as a namesake, the ORCID is the
    namesake's too -- nine pages linked to strangers' ORCIDs before this."""
    import re
    src = (ROOT / "pipeline" / "audit_matches.py").read_text()
    block = src[src.index('if p["person_id"] in bad:'):src.index("write_csv(ppath")]
    assert 'p["openalex_author_id"] = None' in block and 'p["orcid"] = None' in block
