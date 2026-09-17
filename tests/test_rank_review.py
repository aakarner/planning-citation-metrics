"""The rank-review flag: who is due a manual check for a missed promotion.

Promotions are applied only when the Scholar profile text states a rank, and
many profiles never do. This flag is the safety net. It has to be strict about
what it treats as evidence, because the inherited roster has no rank start
dates and the tempting proxy -- years since the PhD -- is only meaningful for
assistant professors.
"""

from datetime import date

from pipeline.detect_changes import RANK_REVIEW_YEARS, rank_review

TODAY = date(2026, 9, 17)


def test_an_assistant_long_past_the_phd_is_flagged_when_no_start_is_known():
    yrs, why = rank_review("assistant", None, 2019, TODAY)
    assert yrs == 7 and "PhD" in why
    assert rank_review("assistant", None, 1994, TODAY)[0] == 32     # Joyce Pressley


def test_an_assistant_recently_past_the_phd_is_not():
    assert rank_review("assistant", None, 2020, TODAY) is None


def test_the_phd_proxy_is_never_used_for_associates():
    # Most associates are decades past the PhD; this would flag 250 people.
    assert rank_review("associate", None, 1990, TODAY) is None


def test_a_start_date_we_recorded_is_used_for_either_rank():
    assert rank_review("associate", "2018-06-01", 2005, TODAY)[0] == 8
    assert rank_review("assistant", "2019-08-15", 2015, TODAY)[0] == 7
    assert rank_review("associate", "2021-01-01", 1990, TODAY) is None


def test_a_promotion_we_just_applied_resets_the_clock():
    # Known, recent start date wins over an old PhD year.
    assert rank_review("assistant", "2026-09-15", 2005, TODAY) is None


def test_full_professors_are_never_flagged():
    assert rank_review("full", None, 1980, TODAY) is None
    assert rank_review("full", "2000-01-01", 1980, TODAY) is None


def test_missing_phd_year_decides_nothing():
    assert rank_review("assistant", None, None, TODAY) is None


def test_the_threshold_is_the_named_constant():
    edge = TODAY.year - RANK_REVIEW_YEARS
    assert rank_review("assistant", None, edge, TODAY) is not None
    assert rank_review("assistant", None, edge + 1, TODAY) is None


def test_a_recent_verification_silences_the_flag_and_an_old_one_does_not():
    from pipeline.detect_changes import verified_on
    assert rank_review("assistant", None, 1994, TODAY, verified=date(2026, 9, 17)) is None
    assert rank_review("assistant", None, 1994, TODAY, verified=date(2018, 1, 1)) is not None
    src = "workbook-2026 | rank verified 2024-03-01; x | rank verified 2026-09-17; y"
    assert verified_on(src) == date(2026, 9, 17)          # the latest note wins
    assert verified_on("workbook-2026") is None
    assert verified_on(None) is None
