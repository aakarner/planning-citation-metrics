"""Which roster Scholar ids still need their first fetch.

Discovery writes an id and stops; without this rule the person keeps their
Publish or Perish number until the quarterly collection while the homepage
announces the find. Arnab Chakraborty sat like that for a week.
"""

import json

from pipeline.collect_scholar import ids_missing_readings


def snap(pid, sid):
    return {"person_id": pid, "raw_json": json.dumps({"scholar_id": sid}) if sid != "legacy" else "{}"}


PEOPLE = [{"person_id": "1", "google_scholar_id": "NEW1"},     # found, never fetched
          {"person_id": "2", "google_scholar_id": "OLD2"},     # fetched under this id
          {"person_id": "3", "google_scholar_id": "ANY3"},     # only a legacy row with no id stored
          {"person_id": "4", "google_scholar_id": "REAL4"},    # id changed: rows exist for the namesake only
          {"person_id": "5", "google_scholar_id": ""}]         # no id at all


def test_only_ids_without_a_reading_are_selected():
    rows = [snap("2", "OLD2"), snap("3", "legacy"), snap("4", "WRONG4")]
    assert ids_missing_readings(PEOPLE, rows) == {"NEW1", "REAL4"}


def test_a_legacy_row_without_an_id_counts_for_whatever_id_is_held():
    assert ids_missing_readings([PEOPLE[2]], [snap("3", "legacy")]) == set()


def test_nothing_to_fetch_when_every_id_has_a_row():
    assert ids_missing_readings([PEOPLE[1]], [snap("2", "OLD2")]) == set()


def test_the_weekly_budget_goes_to_the_oldest_readings_of_current_people():
    from pipeline.collect_scholar import oldest_first
    people = [{"person_id": str(i), "google_scholar_id": f"id{i}"} for i in range(1, 6)]
    snaps = [{"person_id": "1", "collected_at": "2026-03-01", "total_citations": "10"},
             {"person_id": "1", "collected_at": "2026-09-15", "total_citations": "12"},
             {"person_id": "2", "collected_at": "2026-03-01", "total_citations": "10"},
             {"person_id": "3", "collected_at": "2026-10-10", "total_citations": "10"},
             {"person_id": "4", "collected_at": "2026-10-10", "total_citations": ""}]    # a failed fetch is no reading
    picked = oldest_first(people, snaps, current={"1", "2", "3", "4"}, budget=3)
    assert [p["person_id"] for p in picked] == ["4", "2", "1"]     # never read, then March, then September; 5 has left



def test_a_workbook_row_counts_only_for_the_profile_its_url_names():
    # Yan Song, 2026-10-12: the workbook row pointed at a namesake's id; once the roster held
    # her real profile, that row must not count as a reading of it.
    import json
    person = {"person_id": "516", "google_scholar_id": "VOpvlkkAAAAJ"}
    workbook = {"person_id": "516", "raw_json": json.dumps({"workbook_row": {"GoogleScholar": "https://scholar.google.com/citations?hl=en&user=ky5sX-0AAAAJ"}})}
    assert ids_missing_readings([person], [workbook]) == {"VOpvlkkAAAAJ"}
    read = {"person_id": "516", "raw_json": json.dumps({"scholar_id": "VOpvlkkAAAAJ"})}
    assert ids_missing_readings([person], [workbook, read]) == set()
