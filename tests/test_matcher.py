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
