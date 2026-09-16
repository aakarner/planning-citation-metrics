"""The Scholar discovery rules: what gets accepted, rejected, or left to a person.

Discovery searches Scholar by name alone, so most hits are strangers sharing
it. These cases are the real ones from the 2026-09-15 pass.
"""

from pipeline.find_scholar_profiles import classify, decide

DEPTS = {
    "1": {"university": "Florida State University", "short_name": "Florida State University",
          "name": "Florida State University"},
    "2": {"university": "Georgia Tech", "short_name": "Georgia Tech", "name": "Georgia Tech"},
    "3": {"university": "Hunter College", "short_name": "Hunter College", "name": "Hunter College"},
}


def cand(inst_match, where, score=0.9, name_sim=1.0):
    return {"inst_match": inst_match, "last_known_institution": where,
            "score": score, "name_sim": name_sim}


def test_accepts_a_profile_naming_the_persons_own_department():
    assert classify(cand(1.0, "Florida State University"), DEPTS) == "accepted"


def test_rejects_a_namesake_who_names_some_other_institution():
    for where in ("Stanford", "Rutgers School of Public Health", "Professor of Pediatrics",
                  "Aow Thai Marine Ecology Center", "Cleveland Clinic Lerner College of Medicine",
                  "Seton Hall University", "Estudiante de doctorado", "Charlottesville, Virginia"):
        assert classify(cand(0.0, where), DEPTS) == "rejected", where


def test_keeps_an_unreadable_affiliation_for_a_person_to_judge():
    assert classify(cand(0.0, ""), DEPTS) == "pending"
    assert classify(cand(0.0, None), DEPTS) == "pending"


def test_keeps_a_profile_naming_a_different_tracked_department():
    # A real find plus a move: worth a person's attention, not a rejection.
    assert classify(cand(0.0, "Georgia Tech"), DEPTS) == "pending"


def test_a_single_clear_winner_is_accepted():
    status, per = decide([cand(1.0, "Florida State University"), cand(0.0, "Stanford")], DEPTS)
    assert status == "accepted"
    assert per == ["accepted", "rejected"]


def test_two_plausible_profiles_go_to_a_person():
    status, per = decide([cand(1.0, "Florida State University", score=0.9),
                          cand(1.0, "Florida State University", score=0.88)], DEPTS)
    assert status == "pending"
    assert per == ["pending", "pending"]


def test_all_namesakes_settles_the_person_with_no_review():
    status, per = decide([cand(0.0, "Stanford"), cand(0.0, "Professor of Pediatrics")], DEPTS)
    assert status == "rejected"
    assert per == ["rejected", "rejected"]


def test_no_candidates_at_all():
    assert decide([], DEPTS) == ("none", [])


def test_an_affiliation_naming_no_institution_is_not_evidence_of_a_namesake():
    # Real case: MIT's Karilyn Crockett, whose profile names her field, not her
    # school. Unhelpful, but it does not say the profile belongs to someone else.
    assert classify(cand(0.0, "Professor or urban history, public policy & planning"), DEPTS) == "pending"


def test_placeholder_affiliations_are_treated_as_blank():
    for where in ("Unknown affiliation", "unknown", "none", "Independent researcher", "Retired"):
        assert classify(cand(0.0, where), DEPTS) == "pending", where


def test_rejects_a_field_no_planning_academic_works_in():
    for where in ("Professor of Pediatrics", "Ärztin Radioonkologie",
                  "Former Professor of Electrical Engineering, IIT Kharagpur",
                  "Associate Professor of Computer Science"):
        assert classify(cand(0.0, where), DEPTS) == "rejected", where


def test_rejects_a_pre_faculty_profile_in_any_language_we_cover():
    for where in ("Estudiante de doctorado", "PhD candidate, Somewhere", "Postdoctoral fellow"):
        assert classify(cand(0.0, where), DEPTS) == "rejected", where


def test_a_named_institution_that_is_not_theirs_is_still_rejected():
    assert classify(cand(0.0, "University of Science and Technology of China"), DEPTS) == "rejected"
    assert classify(cand(0.0, "Selçuk Üniversitesi"), DEPTS) == "rejected"
