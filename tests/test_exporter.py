import csv
import os
from datetime import date
from decimal import Decimal

import pytest

from exporter import export_csv
from rebate_engine import DataError, Filters, load_csv, load_csvs


@pytest.mark.parametrize("view", ["id", "day", "daily_id"])
def test_export_matches_exact_filtered_total(make_csv, tmp_path, view):
    dataset = load_csv(make_csv())
    result = dataset.query(Filters(("001", "2", "999"), date(2026, 8, 18), date(2026, 8, 24)))
    path = tmp_path / f"out-{view}.csv"
    count = export_csv(path, dataset, result, view)
    assert path.read_bytes().startswith(b"\xef\xbb\xbf")
    with path.open(encoding="utf-8-sig", newline="") as stream:
        rows = list(csv.DictReader(stream))
    assert count == len(rows)
    assert sum(Decimal(row["返佣收入(USDT)"]) for row in rows) == result.total
    assert sum(int(row["返佣笔数"]) for row in rows) == result.count
    assert rows[0]["好友ID筛选"] == "001 2 999"
    assert all(row["好友ID筛选"] == "" for row in rows[1:])
    assert all(row["开始日期(含)"] == "2026-08-18" for row in rows)
    if view != "day":
        assert any(row["好友ID（现货）"] == "001" for row in rows)
    if view != "id":
        assert all(row["UID状态"] == "" and row["全批次笔数"] == "" for row in rows)


@pytest.mark.parametrize("kind", ["original", "symlink", "hardlink"])
def test_source_overwrite_protection(make_csv, tmp_path, kind):
    source = make_csv()
    original = source.read_bytes()
    target = source
    if kind != "original":
        target = tmp_path / "alias.csv"
        if kind == "symlink":
            target.symlink_to(source)
        else:
            os.link(source, target)
    dataset = load_csv(source)
    with pytest.raises(DataError, match="覆盖源"):
        export_csv(target, dataset, dataset.query())
    assert source.read_bytes() == original


def test_failed_export_preserves_existing_file(make_csv, tmp_path, monkeypatch):
    dataset = load_csv(make_csv())
    target = tmp_path / "existing.csv"
    target.write_text("existing", encoding="utf8")
    def fail(*args):
        raise OSError("test failure")
    monkeypatch.setattr("exporter.os.replace", fail)
    with pytest.raises(OSError):
        export_csv(target, dataset, dataset.query())
    assert target.read_text() == "existing"
    assert not list(tmp_path.glob(".rebate-export-*.tmp"))


def test_formula_injection_in_filename_is_escaped(make_csv, tmp_path):
    dataset = load_csv(make_csv(filename="=1+1.csv"))
    target = tmp_path / "out.csv"
    export_csv(target, dataset, dataset.query())
    with target.open(encoding="utf-8-sig", newline="") as stream:
        row = next(csv.DictReader(stream))
    assert row["来源文件"].startswith("'=")


def test_moved_source_export_still_works_and_source_stays_protected(make_csv, tmp_path):
    original = make_csv()
    dataset = load_csv(original)
    moved = original.rename(tmp_path / "moved.csv")
    existing = tmp_path / "existing.csv"
    existing.write_text("old", encoding="utf8")
    assert export_csv(existing, dataset, dataset.query()) == 6
    assert export_csv(tmp_path / "new.csv", dataset, dataset.query()) == 6
    with pytest.raises(DataError, match="覆盖源"):
        export_csv(moved, dataset, dataset.query())


def test_all_batch_sources_and_aliases_protected(make_csv, tmp_path):
    a, b = make_csv(filename="a.csv"), make_csv(filename="b.csv")
    dataset = load_csvs([a, b])
    alias = tmp_path / "b-alias.csv"
    os.link(b, alias)
    for path in (a, b, alias):
        with pytest.raises(DataError, match="覆盖源"):
            export_csv(path, dataset, dataset.query())


def test_large_id_list_written_only_once(make_csv, tmp_path):
    ids = tuple(str(10000000 + i) for i in range(1000))
    dataset = load_csv(make_csv([["spot", uid, "0", "1", "2026-08-18"] for uid in ids]))
    output = tmp_path / "large.csv"
    export_csv(output, dataset, dataset.query(Filters(ids)))
    assert output.stat().st_size < 180_000
    with output.open(encoding="utf-8-sig", newline="") as stream:
        rows = list(csv.DictReader(stream))
    assert len(rows) == 1000
    assert sum(bool(row["好友ID筛选"]) for row in rows) == 1
    assert rows[0]["好友ID筛选"].split() == list(ids)


def test_status_export_contains_complete_inactive_missing_and_zero(make_csv, tmp_path):
    dataset = load_csv(make_csv())
    result = dataset.query(Filters(("001", "12", "4", "999"), date(2026, 8, 18)))
    output = tmp_path / "status.csv"
    export_csv(output, dataset, result, status="no_rebate")
    with output.open(encoding="utf-8-sig", newline="") as stream:
        rows = list(csv.DictReader(stream))
    assert {row["好友ID（现货）"] for row in rows} == {"12", "4"}
    assert all(row["所选文件中存在"] == "是" for row in rows)
    assert {row["UID状态"] for row in rows} == {"存在 · 当前条件无记录", "有记录 · 净额为 0"}
    export_csv(output, dataset, result, status="missing")
    with output.open(encoding="utf-8-sig", newline="") as stream:
        rows = list(csv.DictReader(stream))
    assert len(rows) == 1 and rows[0]["所选文件中存在"] == "否"
    assert rows[0]["好友ID（现货）"] == "999"
