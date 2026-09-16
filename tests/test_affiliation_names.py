"""Matching a Scholar profile's affiliation text to a department we track.

Every case here is a real profile line from the 2026-09-14/15 collection. Three
of them produced wrong departures on the site: a profile that spells out a name
we abbreviate (Allaire, Lambrou) or misspells its own school (Pendall) looked
like somebody who had left the field.

The rule that recovers those has to stay narrow, because an institution named in
a profile is a *different* institution however few characters separate the
names. The rejection cases below are the ones a similarity score gets wrong:
UC Irvine against USC, Urbana-Champaign against Chicago, UT Austin against UT
Arlington.
"""

from pipeline.find_scholar_profiles import affiliation_match, affiliation_names_dept


def dept(label, **extra):
    return {"university": label, "short_name": label, "name": label, **extra}


D = {name: dept(name) for name in (
    "UC Irvine", "UC Berkeley", "UCLA", "UC San Diego", "USC", "UNC", "MIT",
    "Georgia Tech", "Virginia Tech", "Cal Poly, Pomona", "Cal Poly, San Luis Obispo",
    "University of New Mexico", "University of Colorado, Denver",
    "University of Illinois, Urbana-Champaign", "University of Illinois, Chicago",
    "University of Texas, Austin", "University of Texas, Arlington",
    "California State University, Northridge", "Universite de Montreal",
)}


# --- the legal name, where our label is the field's short form ------------------

def test_spelled_out_name_matches_our_abbreviation():
    for text, want in [
        ("Associate Professor, University of California, Irvine", "UC Irvine"),
        ("Assistant Professor, University of California, Berkeley", "UC Berkeley"),
        ("Professor, University of California, Los Angeles", "UCLA"),
        ("California State Polytechnic University, Pomona", "Cal Poly, Pomona"),
        ("California Polytechnic State University, San Luis Obispo", "Cal Poly, San Luis Obispo"),
        ("Professor of City and Regional Planning, University of North Carolina at Chapel Hill", "UNC"),
        ("Professor, Price School of Public Policy, University of Southern California", "USC"),
        ("Harry West Chair Professor, Georgia Institute of Technology", "Georgia Tech"),
        ("Virginia Polytechnic Institute and State University", "Virginia Tech"),
        ("Professor of Cities and Transportation, Massachusetts Institute of Technology", "MIT"),
    ]:
        assert affiliation_match(text, D[want]) == 1.0, text


def test_spelled_out_name_matches_only_its_own_campus():
    for text, want in [
        ("University of California, Irvine", "UC Irvine"),
        ("University of Southern California", "USC"),
        ("California State Polytechnic University, Pomona", "Cal Poly, Pomona"),
    ]:
        hits = [k for k, v in D.items() if affiliation_match(text, v)]
        assert hits == [want], (text, hits)


# --- one character of slop, and no more ----------------------------------------

def test_a_misspelled_school_still_names_that_school():
    # Rolf Pendall's profile: "Mew" for "New". Read as a departure from planning.
    text = "Department of Community & Regional Planning, University of Mew Mexico"
    assert not affiliation_match(text, D["University of New Mexico"])
    assert affiliation_names_dept(text, D["University of New Mexico"])


def test_a_different_campus_is_never_a_typo():
    for text, other in [
        ("University of Colorado Boulder", "University of Colorado, Denver"),
        ("University of Illinois at Chicago", "University of Illinois, Urbana-Champaign"),
        ("University of Illinois at Urbana-Champaign", "University of Illinois, Chicago"),
        ("Associate Professor, University of Texas at Arlington", "University of Texas, Austin"),
        ("The University of Texas at Austin", "University of Texas, Arlington"),
    ]:
        assert not affiliation_names_dept(text, D[other]), (text, other)


def test_a_different_institution_is_never_a_typo():
    for text, other in [
        ("University of Southern California", "UC Irvine"),
        ("University of California, Berkeley", "UC Irvine"),
        ("University of California San Diego", "UC Irvine"),
        ("University of North Carolina at Chapel Hill", "California State University, Northridge"),
        ("Professor of Urban Planning, Texas A&M University", "University of Texas, Austin"),
        ("California Polytechnic State University, San Luis Obispo", "Cal Poly, Pomona"),
    ]:
        assert not affiliation_names_dept(text, D[other]), (text, other)


def test_sharing_a_city_is_not_naming_the_same_school():
    montreal = D["Universite de Montreal"]
    assert affiliation_names_dept("Associate professor, University of Montreal", montreal)
    for other in ("Professor, University of Quebec in Montreal",
                  "School of Urban Planning, McGill University, Montreal"):
        assert not affiliation_names_dept(other, montreal), other
