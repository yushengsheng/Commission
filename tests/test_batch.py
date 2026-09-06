from datetime import date
from decimal import Decimal, localcontext
import os

import pytest

from rebate_engine import DataError, Filters, ImportCancelled, load_csvs


def test_split_files_all_rows_add_and_sources_reconcile(make_csv):
    row = ["spot", "001", "999", "0.123456789123456789", "2026-08-18"]
    a = make_csv([row, row], filename="a.csv")
    b = make_csv([row, row, row], filename="b.csv")
    with localcontext() as ctx:
        ctx.prec = 6
        dataset = load_csvs([a, b])
        result = dataset.query()
    assert result.count == 5
    assert result.total == Decimal("0.617283945617283945")
    assert [source.row_count for source in dataset.sources] == [2, 3]
    assert dataset.sources[0].total == Decimal("0.246913578246913578")
    assert dataset.uid_bucket_keys == {"001": [(date(2026, 8, 18), "001", "spot")]}
    assert load_csvs([b, a]).query().total == result.total


@pytest.mark.parametrize("alias", ["same", "symlink", "hardlink"])
def test_same_physical_file_rejected_not_silent_double_import(make_csv, tmp_path, alias):
    source = make_csv()
    other = source
    if alias != "same":
        other = tmp_path / "alias.csv"
        if alias == "symlink":
            other.symlink_to(source)
        else:
            os.link(source, other)
    with pytest.raises(DataError, match="重复选择"):
        load_csvs([source, other])


def test_bad_second_file_aborts_entire_batch_with_filename(make_csv):
    a = make_csv(filename="a.csv")
    b = make_csv([["spot", "1", "0", "bad", "2026-08-18"]], filename="bad.csv")
    with pytest.raises(DataError, match="bad.csv.*第 2 行"):
        load_csvs([a, b])


def test_cancel_between_files_and_monotonic_progress(make_csv):
    a, b = make_csv(filename="a.csv"), make_csv(filename="b.csv")
    updates = []
    with pytest.raises(ImportCancelled):
        load_csvs([a, b], lambda p, n: updates.append((p, n)), lambda: bool(updates and updates[-1][1] >= 8))
    updates.clear()
    load_csvs([a, b], lambda p, n: updates.append((p, n)))
    assert updates[-1] == (100, 16)
    assert updates == sorted(updates)


def test_first_source_changed_during_second_file_rejected(make_csv):
    a, b = make_csv(filename="a.csv"), make_csv(filename="b.csv")
    old = a.stat()
    def progress(percent, count):
        if count > 8:
            os.utime(a, ns=(old.st_atime_ns, old.st_mtime_ns + 1_000_000))
    with pytest.raises(DataError, match="发生变化"):
        load_csvs([a, b], progress)


@pytest.mark.parametrize("mutation", ["rewrite", "replace", "hardlink"])
def test_later_source_changed_during_first_file_rejected(make_csv, mutation):
    a, b = make_csv(filename="a.csv"), make_csv(filename="b.csv")
    replacement = make_csv([["spot", "9", "0", "999", "2026-08-18"]], filename="replacement.csv")
    changed = False
    def progress(percent, count):
        nonlocal changed
        if changed:
            return
        changed = True
        if mutation == "rewrite":
            make_csv([["spot", "9", "0", "12345", "2026-08-18"]], filename="b.csv")
        elif mutation == "replace":
            os.replace(replacement, b)
        else:
            b.unlink()
            os.link(a, b)
    with pytest.raises(DataError, match="b.csv.*发生变化"):
        load_csvs([a, b], progress)


def test_uid_statuses_include_missing_inactive_zero_negative_positive(make_csv):
    rows = [
        ["spot", "001", "0", "1", "2026-08-18 00:00:00"],
        ["spot", "2", "0", "2", "2026-08-24 23:59:59"],
        ["spot", "3", "0", "0", "2026-08-20"],
        ["spot", "4", "0", "-3", "2026-08-20"],
        ["spot", "5", "0", "1", "2026-08-25"],
        ["spot", "6", "0", "1", "2026-08-20"],
        ["spot", "6", "0", "-1", "2026-08-20"],
    ]
    dataset = load_csvs([make_csv(rows[:3], filename="a.csv"), make_csv(rows[3:], filename="b.csv")])
    result = dataset.query(Filters(("001", "2", "3", "4", "5", "6", "999"), date(2026, 8, 18), date(2026, 8, 24)))
    assert result.statuses == {"001": "positive", "2": "positive", "3": "zero", "4": "negative", "5": "inactive", "6": "zero", "999": "missing"}
    assert result.count == 6
    assert result.total == 0
    assert result.per_id["6"].count == 2
    assert len(result.per_id) == 7
    assert result.visible_ids("no_rebate") == ["3", "5", "6"]
    unrequested = dataset.query(Filters(start=date(2027, 1, 1)))
    assert len(unrequested.per_id) == len(dataset.ids)
    assert set(unrequested.statuses.values()) == {"inactive"}


def test_empty_batch_rejected():
    with pytest.raises(DataError, match="至少"):
        load_csvs([])
