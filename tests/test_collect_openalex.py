"""Adding a person's split OpenAlex records together.

OpenAlex assigns each authorship to exactly one author id, so a person's own
records never share a work: citations, works and i10 add. The h-index does
not, so the largest record's stands.
"""

from pipeline.collect_openalex import accepted_ids, aggregate


def rec(_id, cites, works, h, years=(), name="A. Person", inst=None):
    return {"_id": _id, "display_name": name, "cited_by_count": cites, "works_count": works,
            "summary_stats": {"h_index": h, "i10_index": works // 2},
            "counts_by_year": [{"year": y, "cited_by_count": c} for y, c in years],
            "last_known_institutions": [{"display_name": inst}] if inst else []}


def test_records_are_summed_and_the_h_index_is_the_largest():
    out = aggregate([rec("A1", 54, 8, 4, [(2025, 10), (2020, 5)]),
                     rec("A2", 60, 6, 5, [(2025, 12)]),
                     rec("A3", 36, 3, 3, [(2019, 30)])], year=2026)
    assert out["total_citations"] == 150 and out["works_count"] == 17
    assert out["h_index"] == 5                         # max, not sum
    assert out["i10_index"] == 4 + 3 + 1
    assert out["citations_5yr"] == 22                  # 2025 only; 2020 and 2019 are outside five years


def test_the_record_with_most_works_is_primary():
    import json
    out = aggregate([rec("small", 500, 2, 9, inst="Elsewhere"), rec("big", 100, 40, 7, inst="Somewhere U")], year=2026)
    raw = json.loads(out["raw_json"])
    assert raw["openalex_author_id"] == "big"
    assert raw["openalex_author_ids"] == ["big", "small"]
    assert raw["last_known_institutions"][0]["display_name"] == "Somewhere U"
    assert [r["id"] for r in raw["records"]] == ["big", "small"]


def test_a_single_record_is_unchanged_and_carries_no_records_list():
    import json
    out = aggregate([rec("only", 135, 38, 6)], year=2026)
    assert out["total_citations"] == 135 and out["h_index"] == 6 and out["works_count"] == 38
    assert "records" not in json.loads(out["raw_json"])


def test_accepted_ids_puts_the_roster_id_first_and_adds_accepted_rows_once():
    people = [{"person_id": "1", "openalex_author_id": "P"}, {"person_id": "2", "openalex_author_id": ""}]
    rows = [{"person_id": "1", "source": "openalex", "status": "accepted", "external_id": "F1"},
            {"person_id": "1", "source": "openalex", "status": "accepted", "external_id": "P"},      # duplicate of roster id
            {"person_id": "1", "source": "openalex", "status": "pending", "external_id": "X"},
            {"person_id": "2", "source": "openalex", "status": "accepted", "external_id": "Q"},
            {"person_id": "3", "source": "google_scholar", "status": "accepted", "external_id": "S"}]
    assert accepted_ids(people, rows) == {"1": ["P", "F1"], "2": ["Q"]}



def test_a_record_whose_totals_lag_its_works_is_recomputed_from_the_works():
    from pipeline.collect_openalex import reconcile_with_works, figures_from_works, aggregate
    record = {"_id": "A1", "works_count": 12, "cited_by_count": 51, "summary_stats": {"h_index": 3, "i10_index": 2},
              "counts_by_year": [{"year": 2026, "cited_by_count": 5}]}
    works = [{"cited_by_count": c, "publication_year": 2019, "counts_by_year": [{"year": 2026, "cited_by_count": c // 10}]}
             for c in (238, 107, 63, 35, 31) + (12,) * 10 + (1,) * 16]       # 31 works, 610 citations
    fixed = reconcile_with_works(record, index_count=31, works=works)
    assert fixed["cited_by_count"] == 610 and fixed["works_count"] == 31
    assert fixed["summary_stats"]["h_index"] == 12 and fixed["summary_stats"]["i10_index"] == 15
    assert fixed["_reconciled"]["author_record_said"] == {"works_count": 12, "cited_by_count": 51}
    assert fixed["counts_by_year"][0] == {"year": 2026, "cited_by_count": sum(c // 10 for c in (238, 107, 63, 35, 31) + (12,) * 10)}
    row = aggregate([fixed], 2026)
    assert row["total_citations"] == 610 and "reconciled_from_works" in row["raw_json"]
    # a short fetch (paging failed, index moved) must not replace the figures with a partial sum
    assert reconcile_with_works(record, index_count=31, works=works[:20]) is record
    # a record the index agrees with is left alone, as is one off by only a little
    assert reconcile_with_works(record, index_count=12) is record
    assert reconcile_with_works(record, index_count=14) is record
    assert figures_from_works([]) == {"cited_by_count": 0, "works_count": 0, "summary_stats": {"h_index": 0, "i10_index": 0}, "counts_by_year": []}
