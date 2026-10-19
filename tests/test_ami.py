from housing_watch import ami
from housing_watch.criteria import Profile, unit_verdict
from housing_watch.models import Band, Unit

P = Profile(household=1, income=150_000, sizes=frozenset({"studio", "1br"}))


def test_cap_rounds_like_hud():
    assert ami.cap(130, 1) == 154440 and ami.cap(165, 1) == 196020 and ami.cap(125, 1) == 148500
    assert ami.cap(130, 1, 2025) == 147420 and ami.cap(130, 9) is None and ami.cap(None, 1) is None


def test_match_prefers_printed_household_and_ami():
    assert ami.match(168480) == (2025, 2, 130)
    assert ami.match(154440, 1, 130) == (2026, 1, 130)
    assert ami.match(227500, 4, 130) == (2025, 5, 130)  # Pronto's SVEN waitlist: printed 1-4, the cap is 5's
    assert ami.match(90000) is None
    assert ami.match(168480, sizes=[1]) is None and ami.match(168480, years=[2026]) is None


def test_bands_one_range_over_sizes():
    bs, year = ami.bands([1, 2], 77303, 168480, 130)
    assert year == 2025 and bs == [Band(1, 77303, 147420), Band(2, 77303, 168480)]
    bs, year = ami.bands([1, 2, 3], 95452, 198510, 130)
    assert year == 2026 and bs[0] == Band(1, 95452, 154440)


def test_bands_top_cap_ends_the_range_and_min_that_is_a_cap():
    bs, year = ami.bands([], None, 189540)  # no households printed: the matched 3-person cap ends it
    assert year == 2025 and [b.income_max for b in bs] == [147420, 168480, 189540]
    bs, _ = ami.bands([1, 2, 3], 45360, 58320)  # 40% 2025 from 1 person's cap to 3 people's
    assert bs[0] == Band(1, None, 45360)


def test_bands_unmatched_scales_by_current_chart():
    bs, year = ami.bands([1, 2, 3], 90000, 200000)
    assert year is None and bs[-1].income_max == 200000 and bs[0].income_max == round(200000 * 118800 / 152700, -1)
    assert ami.bands([1, 2], 50000, None) == ([Band(1, 50000, None), Band(2, 50000, None)], None)


def test_rows_restamps_a_repeated_top_cap():
    stamped = [Band(1, 77303, 168480), Band(2, 77303, 168480)]
    assert ami.rows(stamped, 130) == ([Band(1, 77303, 147420), Band(2, 77303, 168480)], 2025)
    real = [Band(1, 98590, 154440), Band(2, 98590, 176410)]
    assert ami.rows(real, 130) == (real, 2026)
    assert ami.year_of([Band(1, None, 154440), Band(2, None, 170000)]) is None


def test_older_chart_judged_on_current_limits():
    old = Unit("studio", ami=130, bands=[Band(1, 77303, 147420)], chart_year=2025)
    assert unit_verdict(old, P) == ("eligible", "eligible on 2026 limits (ad prints 2025 limits: max $147,420)")
    assert unit_verdict(old, Profile(household=1, income=155_000, sizes=P.sizes))[0] == "excluded"
    assert unit_verdict(Unit("studio", ami=130, bands=[Band(1, 77303, 147420)]), P) == (
        "excluded",
        "over income (max $147,420)",
    )  # no chart recorded: the printed cap stands
    poor = Unit("studio", ami=130, bands=[Band(1, 151000, 147420)], chart_year=2025)
    assert unit_verdict(poor, P) == ("excluded", "under income (min $151,000)")
    unknown = Unit("unknown", bands=[Band(1, 77303, 147420)], chart_year=2025)  # AMI from the cap itself
    assert unit_verdict(unknown, P)[1].startswith("income fits on 2026 limits")
