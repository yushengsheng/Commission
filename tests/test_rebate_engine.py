import os
from datetime import date
from decimal import Decimal, localcontext

import pytest

from rebate_engine import DataError, Filters, ImportCancelled, format_money, load_csv, parse_ids, parse_money


def test_all_and_reconciliation(make_csv):
    dataset = load_csv(make_csv())
    result = dataset.query()
    assert result.count == dataset.row_count == 8
    assert result.total == Decimal("3.65")
    assert dataset.min_day == date(2026, 8, 17)
    assert dataset.max_day == date(2026, 8, 25)
    assert len(dataset.ids) == 6
    for grouping in (result.per_id, result.per_day, result.daily_ids):
        assert sum(a.total for a in grouping.values()) == result.total
        assert sum(a.count for a in grouping.values()) == result.count


@pytest.mark.parametrize("copies", [2, 3, 100])
def test_every_identical_record_counts(make_csv, copies):
    row = ["spot", "10", "1000000", "0.10000001", "2026-08-18 00:00:00"]
    result = load_csv(make_csv([row] * copies)).query()
    assert result.total == Decimal("0.10000001") * copies
    assert result.count == copies
    assert result.per_id["10"].count == copies


@pytest.mark.parametrize("start,end,expected,count", [
    (None, None, "3.65", 8),
    (date(2026, 8, 18), date(2026, 8, 24), "2.35", 6),
    (date(2026, 8, 18), None, "2.65", 7),
    (None, date(2026, 8, 24), "3.35", 7),
    (date(2026, 8, 24), date(2026, 8, 24), "0.15", 3),
    (date(2027, 1, 1), None, "0", 0),
])
def test_optional_inclusive_dates(make_csv, start, end, expected, count):
    result = load_csv(make_csv()).query(Filters(start=start, end=end))
    assert result.total == Decimal(expected)
    assert result.count == count


def test_combined_batch_ids_and_dates(make_csv):
    ids = parse_ids("001,2，001\n2 999;888；777、666")
    assert ids == ("001", "2", "999", "888", "777", "666")
    result = load_csv(make_csv()).query(Filters(ids, date(2026, 8, 18), date(2026, 8, 24)))
    assert result.total == Decimal("0.4")
    assert result.count == 3
    assert result.matched_ids == 2
    assert result.missing_ids == ("999", "888", "777", "666")
    assert result.per_id["999"].total == 0


def test_twenty_ids(make_csv):
    rows = [["spot", str(uid), "999", "0.00000001", "2026-08-18 00:00:00"] for uid in range(30)]
    result = load_csv(make_csv(rows)).query(Filters(tuple(map(str, range(20))), date(2026, 8, 18), date(2026, 8, 24)))
    assert result.total == Decimal("0.00000020")
    assert result.count == result.matched_ids == 20


def test_exact_id_not_prefix_and_leading_zeros(make_csv):
    dataset = load_csv(make_csv())
    assert dataset.query(Filters(ids=("12",))).total == 1
    assert dataset.query(Filters(ids=("1",))).count == 0
    assert dataset.query(Filters(ids=("001",))).count == 2
    assert dataset.query(Filters(ids=("001", "001"))).count == 2


def test_inactive_id_different_from_missing(make_csv):
    result = load_csv(make_csv()).query(Filters(("12", "999"), date(2026, 8, 18)))
    assert result.inactive_ids == ("12",)
    assert result.missing_ids == ("999",)


def test_order_filter_does_not_default_to_spot(make_csv):
    dataset = load_csv(make_csv())
    assert dataset.query().count == 8
    assert dataset.query(Filters(order_type="USDT-futures")).total == Decimal("0.2")
    assert dataset.query(Filters(order_type="spot")).count == 7


def test_known_order_types_are_case_normalized_but_unknown_values_are_preserved(make_csv):
    rows = [
        ["spot", "1", "0", "1", "2026-08-18"],
        [" Spot ", "1", "0", "2", "2026-08-18"],
        ["SPOT", "1", "0", "4", "2026-08-18"],
        ["USDT-Futures", "1", "0", "8", "2026-08-18"],
        ["usdt-futures", "1", "0", "16", "2026-08-18"],
        ["Margin", "1", "0", "32", "2026-08-18"],
        ["margin", "1", "0", "64", "2026-08-18"],
    ]
    dataset = load_csv(make_csv(rows))
    assert dataset.order_types == ("Margin", "USDT-futures", "margin", "spot")
    assert dataset.query(Filters(order_type="spot")).total == 7
    assert dataset.query(Filters(order_type="USDT-futures")).total == 24
    assert dataset.query(Filters(order_type="Margin")).total == 32
    assert dataset.query(Filters(order_type="margin")).total == 64


def test_reversed_range(make_csv):
    with pytest.raises(DataError, match="开始日期"):
        load_csv(make_csv()).query(Filters(start=date(2026, 8, 25), end=date(2026, 8, 18)))


def test_high_precision_is_independent_of_external_context(make_csv):
    rows = [["spot", "1", "0", "999999999999999999999999999999.999999999999999999", "2026-08-18"]] * 2
    with localcontext() as context:
        context.prec = 6
        result = load_csv(make_csv(rows)).query()
    assert str(result.total) == "1999999999999999999999999999999.999999999999999998"
    assert format_money(Decimal("0.0000000001")) == "0.0000000001"


@pytest.mark.parametrize("value", ["NaN", "Infinity", "", "1e-19", "1e30", "1e999", "1,000", "1.0000000000000000001"])
def test_reject_invalid_money(value):
    with pytest.raises(DataError):
        parse_money(value)


@pytest.mark.parametrize("text,expected", [("0E-8", "0.00000000"), ("1e-8", "0.00000001"),
                                           ("1.23456789E-10", "0.000000000123456789"), ("-2E+2", "-200")])
def test_scientific_amounts_exact_not_float(text, expected):
    assert parse_money(text) == Decimal(expected)


@pytest.mark.parametrize("text", ["123abc", "1.2", "1e9", "=1+1", "１２３"])
def test_reject_ambiguous_ids(text):
    with pytest.raises(DataError):
        parse_ids(text)


@pytest.mark.parametrize("bad_date", ["2026-02-30", "2026-08-18 24:00:00", "2026-08-18T00:00:00Z", ""])
def test_invalid_dates_fail_entire_import(make_csv, bad_date):
    with pytest.raises(DataError, match="第 2 行"):
        load_csv(make_csv([["spot", "1", "0", "1", bad_date]]))


def test_missing_usdt_not_substituted_by_raw_income(make_csv):
    with pytest.raises(DataError, match="返佣收入"):
        load_csv(make_csv([], headers=["好友ID（现货）", "返佣收入", "返佣时间"]))


def test_duplicate_required_header(make_csv):
    with pytest.raises(DataError, match="重复"):
        load_csv(make_csv([], headers=["好友ID（现货）", "好友ID(现货)", "返佣时间", "返佣收入(USDT)"]))


def test_header_normalization_reordering_bom_and_quotes(make_csv):
    headers = ["备注", " 返佣收入（usdt） ", "好友ID(现货)", "返佣时间"]
    result = load_csv(make_csv([["a,b\nsecond line", "1.25", "001", "2026/08/18 00:00:00"]], headers)).query()
    assert result.total == Decimal("1.25")
    assert result.per_id["001"].count == 1


def test_empty_header_only_and_malformed(make_csv, tmp_path):
    assert load_csv(make_csv([])).query().count == 0
    with pytest.raises(DataError, match="列数"):
        load_csv(make_csv([["spot", "1"]]))
    empty = tmp_path / "empty.csv"
    empty.touch()
    with pytest.raises(DataError, match="空"):
        load_csv(empty)


def test_encoding_explicit(make_csv):
    path = make_csv(encoding="gb18030")
    with pytest.raises(DataError, match="编码"):
        load_csv(path)
    assert load_csv(path, encoding="gb18030").row_count == 8


def test_cancellation_and_progress(make_csv):
    path = make_csv([["spot", "1", "0", "1", "2026-08-18"]] * 5000)
    updates = []
    with pytest.raises(ImportCancelled):
        load_csv(path, lambda percent, count: updates.append((percent, count)), lambda: len(updates) > 1)
    assert updates[0] == (0, 0)
    assert updates[-1][1] == 2048
    assert updates[-1][0] < 100


def test_changed_source_rejected(make_csv):
    path = make_csv()
    old = path.stat()
    def progress(percent, count):
        if percent == 0:
            os.utime(path, ns=(old.st_atime_ns, old.st_mtime_ns + 1_000_000))
    with pytest.raises(DataError, match="发生变化"):
        load_csv(path, progress)


def test_each_load_is_independent(make_csv):
    path = make_csv()
    first, second = load_csv(path), load_csv(path)
    assert first.query().total == second.query().total == Decimal("3.65")
    assert first.buckets is not second.buckets
