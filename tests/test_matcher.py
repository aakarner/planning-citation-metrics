"""Unit tests for the OpenAlex matcher's scoring and decision rules. No network."""

from pipeline.match_openalex import decide, is_stub, jaro_winkler, name_similarity, norm

GUANG = {"display_name": "Guang Tian", "first_name": "Guang", "last_name": "Tian"}


def test_norm_strips_accents_and_punctuation():
    assert norm("Esteban López-Ochoa") == "esteban lopez ochoa"
    assert norm("Huê-Tâm Jamme") == "hue tam jamme"


def test_jaro_winkler_reference_value():
    assert round(jaro_winkler("martha", "marhta"), 3) == 0.961


def test_name_similarity_accepts_initials_and_reordering():
    assert name_similarity(GUANG, {"display_name": "Guang Tian"}) == 1.0
    assert name_similarity(GUANG, {"display_name": "G. Tian"}) == 0.92
    assert name_similarity(GUANG, {"display_name": "Tian Guang", "display_name_alternatives": ["Guang Tian"]}) == 1.0


def test_name_similarity_requires_the_last_name():
    # Shares a first name and a long common prefix; must not look like a match.
    assert name_similarity(GUANG, {"display_name": "Guang Li"}) <= 0.6


def test_stub_detection():
    assert is_stub({"works_count": 1, "cited_by_count": 0})
    assert not is_stub({"works_count": 31, "cited_by_count": 789})


def test_decide_ignores_stub_runner_ups_but_not_real_ones():
    top = {"score": 0.92, "name_sim": 1.0, "inst_match": 1.0, "works_count": 80, "cited_by_count": 1122}
    stub = {"score": 0.80, "name_sim": 1.0, "inst_match": 1.0, "works_count": 1, "cited_by_count": 0}
    real = {"score": 0.87, "name_sim": 1.0, "inst_match": 1.0, "works_count": 31, "cited_by_count": 789}
    assert decide([top, stub]) == "accepted"
    assert decide([top, real]) == "pending"
    assert decide([stub, top]) == "pending"   # a stub never wins outright
    assert decide([]) == "none"


def test_decide_requires_institution_match():
    top = {"score": 0.80, "name_sim": 1.0, "inst_match": 0.0, "works_count": 40, "cited_by_count": 500}
    assert decide([top]) == "pending"


def test_the_primary_id_is_the_accepted_record_with_the_most_works():
    from pipeline.match_openalex import primary_id
    acc = [{"external_id": "small", "works_count": 2, "cited_by_count": 900},
           {"external_id": "big", "works_count": 40, "cited_by_count": 100},
           {"external_id": "mid", "works_count": 40, "cited_by_count": 50}]
    assert primary_id(acc) == "big"                       # most works; ties broken by citations
    assert primary_id([{"external_id": "only", "works_count": None, "cited_by_count": None}]) == "only"



def test_the_record_linked_to_a_persons_orcid_is_accepted_unless_a_person_rejected_it():
    from pipeline.match_openalex import apply_orcid_matches
    person = {"person_id": "9", "display_name": "AJ Kim"}
    cand = {"external_id": "A1", "candidate_name": "A. J. Kim", "score": 0.6, "name_sim": 0.8, "inst_match": 1.0,
            "topic_share": 0.9, "cites_ratio": None, "works_count": 31, "cited_by_count": 660, "h_index": 12,
            "last_known_institution": "San Diego State University", "orcid": "https://orcid.org/0000-0002-0000-0000", "_department": "SDSU"}
    rows = [{"person_id": "9", "external_id": "A1", "source": "openalex", "status": "pending", "reviewed_by": None, "notes": ""}]
    assert apply_orcid_matches(rows, [(person, dict(cand))], "2026-10-15") == 1
    assert rows[0]["status"] == "accepted" and rows[0]["reviewed_by"] == "orcid" and "ORCID" in rows[0]["notes"]
    # a record the matcher had rejected by rule is overruled; one a reviewer rejected is not
    rows = [{"person_id": "9", "external_id": "A1", "source": "openalex", "status": "rejected", "reviewed_by": "matcher", "notes": "x"}]
    assert apply_orcid_matches(rows, [(person, dict(cand))], "2026-10-15") == 1 and rows[0]["status"] == "accepted"
    rows = [{"person_id": "9", "external_id": "A1", "source": "openalex", "status": "rejected", "reviewed_by": "Maddie", "notes": "not them"}]
    assert apply_orcid_matches(rows, [(person, dict(cand))], "2026-10-15") == 0 and rows[0]["status"] == "rejected"
    # a record not yet in the queue is added, accepted, with the department filled in
    rows = []
    assert apply_orcid_matches(rows, [(person, dict(cand))], "2026-10-15") == 1
    assert rows[0]["status"] == "accepted" and rows[0]["department"] == "SDSU" and "_department" not in rows[0]
    # an already-accepted row is left alone
    assert apply_orcid_matches(rows, [(person, dict(cand))], "2026-10-15") == 0



def test_only_an_independently_sourced_orcid_drives_a_lookup():
    from pipeline.match_openalex import orcid_is_independent
    rows = [{"person_id": "1", "source": "openalex", "status": "accepted", "orcid": "https://orcid.org/0000-0002-3201-7339"}]
    copied = {"person_id": "1", "orcid": "0000-0002-3201-7339", "notes": ""}
    assert not orcid_is_independent(copied, rows)                       # came off the accepted record itself
    from_page = {"person_id": "2", "orcid": "0000-0001-6584-3066", "notes": "ORCID from faculty page, rank review 2026-10-01 by Maddie"}
    assert orcid_is_independent(from_page, rows)
    from_person = {"person_id": "3", "orcid": "0000-0002-9306-2141", "notes": "ORCID sent by the scholar 2026-10-15"}
    assert orcid_is_independent(from_person, rows)
    assert not orcid_is_independent({"person_id": "4", "orcid": "", "notes": "ORCID from faculty page"}, rows)
