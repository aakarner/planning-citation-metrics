"""PhD year and institution from an ORCID education record."""

from pipeline.fill_phd_from_orcid import doctorate


def edu(title, org, year, dept=None):
    return {"education-summary": {"role-title": title, "department-name": dept,
                                  "organization": {"name": org},
                                  "end-date": {"year": {"value": str(year)}} if year else None}}


def record(*entries):
    return {"affiliation-group": [{"summaries": [e]} for e in entries]}


def test_the_doctorate_is_picked_out_of_a_full_education_history():
    r = record(edu("PhD", "University of Pennsylvania", 1993, "Regional Science"),
               edu("MS", "Columbia University", 1987, "Urban and Regional Planning"),
               edu("BS", "New York University", 1984, "Marketing"))
    assert doctorate(r) == ("1993", "University of Pennsylvania", "Regional Science")


def test_titles_are_read_the_ways_people_write_them():
    for title in ("Ph.D.", "PhD", "Doctor of Philosophy", "DPhil", "Doctorate", "PhD in City Planning"):
        assert doctorate(record(edu(title, "MIT", 2025))) == ("2025", "MIT", "")
    for title in ("Master in Architecture", "MCP", "Bachelor of Arts", "Postdoctoral fellow"):
        assert doctorate(record(edu(title, "MIT", 2025))) is None


def test_an_entry_without_a_title_or_year_is_not_a_guess():
    r = record(edu(None, "University of Virginia", None),
               edu("Ph.D.", "Eastern Michigan University", 2019, "Educational Studies"))
    assert doctorate(r) == ("2019", "Eastern Michigan University", "Educational Studies")
    assert doctorate(record(edu("PhD", "Somewhere", None))) is None
    assert doctorate({}) is None and doctorate({"affiliation-group": []}) is None


def test_two_doctorates_give_the_more_recent():
    r = record(edu("PhD", "A", 2001), edu("Doctor of Design", "B", 2010))
    assert doctorate(r) == ("2010", "B", "")
