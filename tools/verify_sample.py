"""Read-only acceptance of the completed engine against the supplied sample.

Print aggregate assertions only; never dump private financial records.
"""
import argparse
from datetime import date
from decimal import Decimal
from pathlib import Path
import sys
import time

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from rebate_engine import Filters, format_money, load_csv


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("csv", type=Path)
    args = parser.parse_args()
    started = time.monotonic()
    dataset = load_csv(args.csv)
    total = dataset.query()
    week = dataset.query(Filters(start=date(2026, 8, 18), end=date(2026, 8, 24)))
    assert dataset.row_count == 680546, dataset.row_count
    assert len(dataset.ids) == 397, len(dataset.ids)
    assert total.total == Decimal("12436.00400100"), total.total
    assert week.count == 222854, week.count
    assert week.total == Decimal("4946.71171800"), week.total
    for result in (total, week):
        assert sum(a.total for a in result.per_id.values()) == result.total
        assert sum(a.total for a in result.per_day.values()) == result.total
        assert sum(a.count for a in result.per_id.values()) == result.count
    print(f"PASS rows={dataset.row_count} ids={len(dataset.ids)} buckets={len(dataset.buckets)}")
    print(f"PASS full={format_money(total.total)} week={format_money(week.total)} USDT")
    print(f"Elapsed: {time.monotonic() - started:.2f}s")


if __name__ == "__main__":
    main()
