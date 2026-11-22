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
    # the same numbers for a record that still sits at UNC are fine, given some planning topics
    # (with none it waits since 2026-11-22: see the polymer chemists below)
    assert settles(1.0, 1.0, 3949, 100, 23933, accepted_cites=10766, has_record=True,
                   last_inst_ok=last_institution_ok("University of North Carolina at Chapel Hill", dept), topic_share=0.4)
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


# ---- 2026-11-22: records the numbers pass and the record itself contradicts ----
# Every case below is a real row of data/review/identity_candidates.csv, with the
# Scholar base and the already-accepted OpenAlex sum the audit held on 2026-11-22.
# Tuples: (works, OpenAlex citations, base, accepted sum, last_inst_ok, topic_share).

def _settles(works, oa, base, have, last_ok, topics, *, judged=True):
    """The audit's call for a row matching on name and institution; `judged=False`
    withholds what the record says about itself, which is all the audit knew of
    a first record before this rule."""
    from pipeline.audit_matches import settles
    extra = dict(last_inst_ok=last_ok, topic_share=topics) if judged else {}
    return settles(1.0, 1.0, oa, works, base, accepted_cites=have, has_record=have > 0, **extra)


def test_a_record_with_almost_no_planning_topics_waits_even_at_the_persons_own_university():
    """22 of the 23 accepted records with under a fifth planning topics and 200 or
    more citations were namesakes. The citation test passed every one of these."""
    namesakes = {
        "Rachel Weber, an epidemiologist; no institution named":  (186, 7372, 5093, 0, None, 0.0),
        "Steven Richter, a process chemist at East Carolina":     (17, 327, 375, 0, True, 0.0),
        "Lawrence Frank, an MRI physicist at UC San Diego":       (225, 16781, 62495, 38437, True, 0.0),
        "Yan Song, polymer chemists at UNC":                      (186, 4599, 13237, 10766, True, 0.0),
        "Wei Zhai, computer vision, at UT Arlington":             (84, 1114, 2232, 1269, True, 0.0),
        "Sarah Williams, science news and management, at MIT":    (173, 1529, 2945, 1713, True, 0.133),
        "David Howell, six people of the name":                   (57, 536, 3968, 1472, None, 0.167),
        "Brian Taylor, an inorganic chemist":                     (85, 1434, 9188, 6105, None, 0.118),
    }
    for who, row in namesakes.items():
        assert _settles(*row, judged=False), who        # the numbers alone settle it
        assert not _settles(*row), who                  # the record's topics keep it with a reviewer


def test_a_first_record_at_another_institution_is_judged_as_an_extra_one_always_was():
    """Liming Wang (Portland State), 2026-11-22: with his accepted record rejected,
    the audit would have taken the next same-name record, then the next. Each has
    Portland State for a year or two among 29 to 65 affiliations, which is all
    inst_match 1.0 means, and none had a record beside it to trigger the 2026-09-25 test."""
    from pipeline.audit_matches import held_for_a_reviewer, last_institution_ok
    dept = {"department_id": "39", "university": "Portland State University",
            "short_name": "Portland State University", "name": "Portland State University"}
    base = 1532
    records = [   # works, citations, last known institution, topic share
        (159, 1852, "Shandong University", 0.137),                                                   # mechanical engineering, finance
        (96, 1935, "Chinese Academy of Medical Sciences & Peking Union Medical College", 0.0),      # oncology
        (242, 2284, "Mongolian University of Science and Technology", 0.0),                         # machinery fault diagnosis
    ]
    for works, oa, where, topics in records:
        elsewhere = last_institution_ok(where, dept)
        assert elsewhere is False, where
        assert _settles(works, oa, base, 0, elsewhere, topics, judged=False), where
        assert not _settles(works, oa, base, 0, elsewhere, topics), where
    # The Drosophila neuroscientist at Capital Medical University scores 0.257, above the
    # floor, and was kept out only by holding 2.2x his figure. The institution test must
    # hold that record whatever the base: against a larger Scholar total it would have settled.
    capital = last_institution_ok("Capital Medical University", dept)
    assert capital is False
    assert held_for_a_reviewer(capital, 0.257, 3380, base)
    assert _settles(78, 3380, 5000, 0, capital, 0.257, judged=False)
    assert not _settles(78, 3380, 5000, 0, capital, 0.257)
    # a first record that sits at Portland State with ordinary topics settles as before
    assert _settles(159, 1852, base, 0, last_institution_ok("Portland State University", dept), 0.6)


def test_a_first_record_elsewhere_with_more_citations_than_our_figure_waits_whatever_its_topics():
    """Ming Zhang (UT Austin): a carbon-emissions economist at China Three Gorges
    University scores 1.0 on topics, because 'energy', 'environmental' and
    'economics' are planning words, and at 7,543 against 5,049 sits inside the
    1.5x bound. Nothing but its institution and size told it from him."""
    from pipeline.audit_matches import held_for_a_reviewer
    assert _settles(254, 7543, 5049, 0, False, 1.0, judged=False)
    assert not _settles(254, 7543, 5049, 0, False, 1.0)
    # the same record at his own institution, or naming none, is not held on size
    assert _settles(254, 7543, 5049, 0, True, 1.0)
    assert _settles(254, 7543, 5049, 0, None, 1.0)
    # an extra record is bounded by the sum rule already, so this test is for first records only
    assert held_for_a_reviewer(False, 1.0, 900, 800, has_record=True) is None


def test_records_people_accepted_still_settle():
    """The rule must not start refusing what a named reviewer took as the person.
    Of the 67 rows accepted by AK, Maddie or the rank reviewer, the audit's numbers
    settle five, two of them fragments; the same five settle under the new rule."""
    accepted_by_a_person = {
        # the Landscape Institute is not Calgary, but the topics are hers and the record is small
        "Rebecca Pedersen, accepted by AK 2026-10-09":      (13, 126, 4943, 0, False, 0.7),
        # rows added by the alias pass and ORCID search carry no topic share or institution
        "Monica Teets Farris, accepted by AK 2026-10-17":   (25, 28, 118, 0, None, None),
        "Hun Kim, accepted by AK 2026-10-17":               (5, 67, 160, 0, None, None),
    }
    for who, row in accepted_by_a_person.items():
        assert _settles(*row, judged=False) and _settles(*row), who
    # a fragment settles on name and institution alone, whatever it says about itself
    assert _settles(2, 0, 2427, 741, True, 0.0) and _settles(3, 40, 1532, 0, False, 0.0)
    # the matcher writes 0.5 for a record with no topics at all: neutral, not foreign
    assert _settles(40, 300, 500, 0, True, 0.5)
    # an ordinary planner who moved: elsewhere, planning topics, under our own figure
    assert _settles(60, 900, 1500, 0, False, 0.8)


def test_a_low_share_holds_a_record_back_and_never_rejects_it():
    """Betsy Donald (Queen's) is the one real record of the 23: economic geography
    scores 0.185. Under this rule hers would wait for a person too, which is the
    price of the other 22, so the rule may only ever leave a row pending."""
    from pipeline.audit_matches import FOREIGN_TOPIC, held_for_a_reviewer, implausible
    assert not _settles(36, 741, 2427, 0, True, 0.185)
    assert held_for_a_reviewer(True, 0.185, 741, 2427)
    assert implausible(741, 2427, high_only=True) is None       # nothing here rejects her record
    assert held_for_a_reviewer(True, FOREIGN_TOPIC, 741, 2427) is None      # the floor is not inclusive
    assert held_for_a_reviewer(True, None, 741, 2427) is None               # unknown topics hold nothing
    src = (ROOT / "pipeline" / "audit_matches.py").read_text()
    settling = src[src.index("pending candidates the evidence already settles"):src.index("if accepted_now:")]
    assert "held_for_a_reviewer" in settling and '"rejected"' not in settling
    # standing records are not sent back under the new rule; that block keeps the 2026-09-25 test
    unsettling = src[src.index("unsettled: dict[int, str] = {}"):src.index("if unsettled:")]
    assert "sibling_agrees(ok, ts)" in unsettling and "held_for_a_reviewer(" not in unsettling
