"""The roster rules: when a Scholar profile moves, promotes or departs someone.

Every case is a real profile line from the 2026-09-14/15 collection. The three
marked "wrong departure" were applied to the live site and had to be undone:
each one names a program we track, but our department labels did not recognise
the name as written, so rule 4 read "institution we don't track" and closed the
appointment.

The policy these lock in: a departure needs the profile to name an institution
that is genuinely not ours. A name we merely failed to recognise is not evidence
that somebody left.
"""

import pytest

from pipeline.apply_changes import decide


def dept(did, label, domain):
    return {"department_id": did, "university": label, "short_name": label,
            "name": label, "url": f"https://planning.{domain}/"}


DEPTS = {
    "1": dept("1", "Cal Poly, Pomona", "cpp.edu"),
    "2": dept("2", "UC Irvine", "uci.edu"),
    "3": dept("3", "Arizona State University", "asu.edu"),
    "4": dept("4", "University of New Mexico", "unm.edu"),
    "5": dept("5", "University of Illinois, Urbana-Champaign", "illinois.edu"),
    "6": dept("6", "USC", "usc.edu"),
}
HOME = {"1": {"cpp.edu"}, "2": {"uci.edu"}, "3": {"asu.edu"},
        "4": {"unm.edu"}, "5": {"illinois.edu"}, "6": {"usc.edu"}}
OWNER = {"cpp.edu": "1", "uci.edu": "2", "asu.edu": "3",
         "unm.edu": "4", "illinois.edu": "5", "usc.edu": "6"}


def run(text, email, rank, cur):
    return decide(text, email, rank, DEPTS[cur], DEPTS, HOME[cur], OWNER)


# --- the three wrong departures ------------------------------------------------

def test_a_consulting_email_does_not_depart_someone_whose_profile_says_they_stayed():
    # Nicole Lambrou, Cal Poly Pomona. Her profile names her own program; only
    # the personal email domain looked like a move.
    kind, target = run("California State Polytechnic University, Pomona",
                       "tinkercraft.com", "assistant", cur="1")
    assert (kind, target) == (None, None)


def test_the_legal_name_of_another_program_is_a_move_not_a_departure():
    # Maura Allaire, ASU -> UC Irvine.
    kind, target = run("Associate Professor, University of California, Irvine",
                       "uci.edu", "associate", cur="3")
    assert kind == "move"
    assert target["short_name"] == "UC Irvine"


def test_a_misspelled_destination_with_a_matching_email_is_a_move():
    # Rolf Pendall, Illinois -> New Mexico. His profile reads "Mew Mexico".
    kind, target = run("Department of Community & Regional Planning, University of Mew Mexico",
                       "unm.edu", "full", cur="5")
    assert kind == "move"
    assert target["short_name"] == "University of New Mexico"


# --- departures that are real --------------------------------------------------

def test_an_untracked_institution_with_a_foreign_email_is_a_departure():
    for text, email in [("London School of Economics and Political Science", "lse.ac.uk"),
                        ("Professor, Wuhan University", "whu.edu.cn"),
                        ("Assistant Professor, Incheon National University", "inu.ac.kr")]:
        kind, _ = run(text, email, "full", cur="5")
        assert kind == "depart", text


def test_an_untracked_campus_of_a_tracked_university_is_a_departure():
    kind, _ = run("Assistant Professor at University of Washington, Tacoma",
                  "uw.edu", "assistant", cur="3")
    assert kind == "depart"


# --- the quiet cases ----------------------------------------------------------

def test_staying_put_is_no_change():
    kind, target = run("Professor of Urban Planning, Arizona State University",
                       "asu.edu", "full", cur="3")
    assert (kind, target) == (None, None)


def test_a_promotion_is_read_off_the_profile():
    kind, target = run("Professor, University of California Irvine", "uci.edu",
                       "associate", cur="2")
    assert kind == "promote"
    assert target["short_name"] == "UC Irvine"


def test_a_foreign_field_is_reported_not_applied():
    kind, target = run("Senior Lecturer in Computational Biochemistry, University of Edinburgh",
                       "ed.ac.uk", "associate", cur="2")
    assert (kind, target) == ("wrong", None)


def test_a_blank_profile_changes_nothing():
    assert run("", None, "full", cur="3") == (None, None)
    assert run("", "gmail.com", "full", cur="3") == (None, None)


@pytest.mark.parametrize("text", [
    "PhD candidate, Arizona State University",
    "Postdoctoral researcher, Harvard University",
])
def test_a_pre_faculty_profile_changes_nothing(text):
    assert run(text, "harvard.edu", "assistant", cur="3") == (None, None)
