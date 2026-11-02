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


def test_a_first_record_needs_a_base_and_only_a_high_bound():
    from pipeline.audit_matches import settles
    assert settles(1.0, 1.0, 54, 8, 78)                    # Andrew Jacobs: 0.7x PoP 78
    assert settles(1.0, 1.0, 110, 30, 78)                  # 1.4x: within the accept bound
    assert not settles(1.0, 1.0, 160, 30, 78)              # 2x our figure: too high to accept unseen
    assert settles(1.0, 1.0, 4, 3, 93)                     # John Shapiro's 4 citations at Pratt: his, and his figure
    assert settles(1.0, 1.0, 1, 2, 736)                    # a fragment settles on the match alone
    assert settles(1.0, 1.0, 2, 1, None)                   # even without a base
    assert not settles(1.0, 1.0, 5394, 228, None)          # Jenny Liu's gerontologist: large, no base
    assert not settles(1.0, 1.0, 135, 38, None)            # her real record waits too; a person decides
    assert not settles(0.9, 1.0, 54, 8, 78)                # name too far
    assert not settles(1.0, 0.5, 54, 8, 78)                # institution not matched


def test_a_sibling_fragment_settles_and_a_large_sibling_is_judged_on_the_sum():
    from pipeline.audit_matches import settles
    assert settles(1.0, 1.0, 0, 1, 736, accepted_cites=700, has_record=True)
    assert not settles(1.0, 1.0, 16190, 932, 11093, accepted_cites=9517, has_record=True)   # Robert Brown's namesake
    assert settles(1.0, 1.0, 900, 40, 11093, accepted_cites=9517, has_record=True)


def test_an_extra_record_at_a_hospital_with_no_planning_topics_waits_for_a_reviewer():
    """Yan Song, 2026-09-25: five same-name records settled as hers because their sum
    stayed under 1.2x her Scholar total; three sat at Chinese universities with no
    planning topics. The sum rule alone cannot tell one prolific person from several."""
    from pipeline.audit_matches import settles, sibling_agrees, last_institution_ok
    dept = {"department_id": "1", "university": "UNC", "short_name": "UNC", "name": "City and Regional Planning"}
    # the extra record: 100 works, 3,949 citations at Xidian, topics 0.0, beside an accepted 10,766 record
    assert not settles(1.0, 1.0, 3949, 100, 23933, accepted_cites=10766, has_record=True,
                       last_inst_ok=last_institution_ok("Xidian University", dept), topic_share=0.0)
    # the same numbers for a record that still sits at UNC are fine
    assert settles(1.0, 1.0, 3949, 100, 23933, accepted_cites=10766, has_record=True,
                   last_inst_ok=last_institution_ok("University of North Carolina at Chapel Hill", dept), topic_share=0.0)
    # or one elsewhere whose topics are planning (a previous post)
    assert settles(1.0, 1.0, 3949, 100, 23933, accepted_cites=10766, has_record=True,
                   last_inst_ok=False, topic_share=0.9)
    # a record naming no institution is judged on topics alone, and unknown topics do not block it
    assert last_institution_ok("", dept) is None and sibling_agrees(None, None)
    # hospitals never pass the institution test, whatever the alias table says
    assert last_institution_ok("UNC Hospitals", dept) is False
    # a fragment still settles on name and institution alone
    assert settles(1.0, 1.0, 12, 2, 23933, accepted_cites=10766, has_record=True, last_inst_ok=False, topic_share=0.0)


def test_a_match_a_person_accepted_or_a_sourced_orcid_settled_stands_whatever_the_ratio():
    """2026-11-01: a snapshot bug inflated seven people's OpenAlex sums past 3x and
    the audit un-accepted them all, among them Jeff Casello (accepted by AK with
    his ORCID) and Hassaan Furqan Khan (ORCID from his faculty page, carried by
    the record), clearing their ORCIDs too. The ratio is weak evidence next to
    either; the matcher's own copies of an ORCID are not."""
    from pipeline.audit_matches import decided_by_a_person, independently_sourced_orcid, settled_matches
    people = [
        {"person_id": "1", "orcid": "0000-0001-9059-8779", "notes": "ORCID from faculty page, rank review 2026-10-07"},
        {"person_id": "2", "orcid": "0000-0002-0942-4016", "notes": None},          # copied by the matcher
        {"person_id": "3", "orcid": None, "notes": None},
        {"person_id": "4", "orcid": None, "notes": None},
    ]
    def row(pid, ext, status, by, orcid=None):
        return {"person_id": pid, "source": "openalex", "status": status, "external_id": ext,
                "orcid": orcid, "reviewed_by": by, "reviewed_at": "2026-10-01"}
    review = [
        row("1", "A1", "accepted", "matcher", "https://orcid.org/0000-0001-9059-8779"),
        row("2", "A2", "accepted", "matcher", "https://orcid.org/0000-0002-0942-4016"),
        row("3", "A3", "accepted", "AK"),
        row("4", "A4", "accepted", "audit_matches"),
        row("4", "A5", "pending", "reviewer"),                  # a pending row settles nothing
    ]
    stands = settled_matches(people, review)
    assert set(stands) == {"1", "3"}
    assert "ORCID" in stands["1"] and "AK" in stands["3"]
    assert decided_by_a_person("AK") and decided_by_a_person("maddie") and decided_by_a_person("reviewer")
    assert not (decided_by_a_person("matcher") or decided_by_a_person("audit_matches") or decided_by_a_person(None))
    assert independently_sourced_orcid(people[0]) and not independently_sourced_orcid(people[1])


def test_un_accepting_keeps_an_orcid_sourced_from_the_person_and_the_ratio_defers_to_people():
    src = (ROOT / "pipeline" / "audit_matches.py").read_text()
    clearing = src[src.index('if p["person_id"] in bad:'):src.index("write_csv(ppath")]
    assert "if not independently_sourced_orcid(p):" in clearing
    judging = src[src.index("bad = {}"):src.index("accepted matches contradicted")]
    assert "in stands" in judging
    # Yang Zhang, 2026-11-01: a two-work fragment Alex had held back as pending was auto-accepted
    settling = src[src.index("pending candidates the evidence already settles"):src.index("if accepted_now:")]
    assert 'decided_by_a_person(r.get("reviewed_by"))' in settling
