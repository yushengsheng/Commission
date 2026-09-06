"""Read-only acceptance: independent integer sums vs production Decimal aggregation.

Usage: python tools/verify_batch.py part1.csv part2.csv [--report report.json]
"""
import argparse
from collections import Counter, defaultdict
import csv
from datetime import date
from decimal import Decimal
import hashlib
import json
from pathlib import Path
import random
import sys
import unicodedata

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from rebate_engine import Filters, load_csvs, normalize_order_type

SCALE = 10**18


def units(text):
    text = text.strip()
    sign = -1 if text.startswith("-") else 1
    text = text.lstrip("+-")
    coefficient, separator, exponent = text.lower().partition("e")
    integer, _, fraction = coefficient.partition(".")
    shift = 18 + (int(exponent) if separator else 0) - len(fraction)
    assert 0 <= shift <= 66
    return sign * int(integer + fraction) * 10**shift


def money(value):
    sign = "-" if value < 0 else ""
    whole, fraction = divmod(abs(value), SCALE)
    return f"{sign}{whole}.{fraction:018d}"


def sha256(path):
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024*1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def verify(paths, requested_ids=()):
    hashes = [sha256(path) for path in paths]
    reference = defaultdict(lambda: [0, 0])
    source_reports = []
    for path in paths:
        count = amount = 0
        days = set()
        ids = set()
        with path.open(encoding="utf-8-sig", newline="") as stream:
            reader = csv.reader(stream, strict=True)
            header = ["".join(unicodedata.normalize("NFKC", cell).split()).lower() for cell in next(reader)]
            uid_col, money_col, day_col = [header.index(name) for name in ("好友id(现货)", "返佣收入(usdt)", "返佣时间")]
            order_col = header.index("订单类型") if "订单类型" in header else None
            for row in reader:
                if not row or all(not cell.strip() for cell in row):
                    continue
                assert len(row) == len(header)
                uid = row[uid_col].strip()
                day = date.fromisoformat(row[day_col].strip()[:10].replace("/", "-"))
                order = normalize_order_type(row[order_col] if order_col is not None else "")
                value = units(row[money_col])
                reference[day, uid, order][0] += 1
                reference[day, uid, order][1] += value
                count += 1
                amount += value
                ids.add(uid)
                days.add(day)
        source_reports.append(dict(file=path.name, records=count, usdt=money(amount),
                                   uids=len(ids), start=str(min(days)) if days else None,
                                   end=str(max(days)) if days else None))
    dataset = load_csvs(paths)
    result = dataset.query()
    assert set(reference) == set(dataset.buckets)
    for key, (count, amount) in reference.items():
        actual = dataset.buckets[key]
        assert actual.count == count and actual.total == Decimal(money(amount)), key
    for source, report in zip(dataset.sources, source_reports):
        assert source.row_count == report["records"]
        assert source.total == Decimal(report["usdt"])
    assert result.count == sum(item["records"] for item in source_reports)
    assert result.total == Decimal(money(sum(value[1] for value in reference.values())))
    rng = random.Random(831)
    ids = sorted(dataset.ids)
    days = sorted({key[0] for key in reference})
    for index in range(40):
        selected = tuple(rng.sample(ids, min(len(ids), 20)) + ["99999999999999999999999999"]) if index % 2 else ()
        start, end = sorted(rng.choices(days, k=2))
        f = Filters(selected, start if index % 3 else None, end if index % 4 else None,
                    rng.choice(dataset.order_types) if index % 5 else None)
        queried = dataset.query(f)
        expected = defaultdict(lambda: [0, 0])
        for (day, uid, order), (count, amount) in reference.items():
            if ((selected and uid not in selected) or (f.start and day < f.start) or
                    (f.end and day > f.end) or (f.order_type and order != f.order_type)):
                continue
            expected[uid][0] += count
            expected[uid][1] += amount
        expected_amount = sum(item[1] for item in expected.values())
        expected_count = sum(item[0] for item in expected.values())
        assert queried.total == Decimal(money(expected_amount))
        assert queried.count == expected_count
        for grouping in (queried.per_id, queried.per_day, queried.daily_ids):
            assert sum(item.count for item in grouping.values()) == expected_count
            assert sum(units(format(item.total, "f")) for item in grouping.values()) == expected_amount
        for uid, aggregate in queried.per_id.items():
            count, value = expected.get(uid, (0, 0))
            assert aggregate.count == count and aggregate.total == Decimal(money(value))
            status = "missing" if uid not in dataset.ids else "inactive" if not count else "zero" if value == 0 else "positive" if value > 0 else "negative"
            assert queried.statuses[uid] == status
    assert hashes == [sha256(path) for path in paths], "Source files changed"
    screenshot = dataset.query(Filters(tuple(requested_ids)))
    return dict(sources=source_reports, records=result.count, usdt=str(result.total),
                uids=len(dataset.ids), start=str(dataset.min_day), end=str(dataset.max_day),
                buckets=len(reference), status_counts=dict(Counter(result.statuses.values())),
                exact_bucket_reconciliation="PASS", independent_queries=40, source_sha256=hashes,
                screenshot_visible_uids_all_time=[dict(uid=uid, status=screenshot.statuses[uid],
                    records=screenshot.per_id[uid].count, usdt=str(screenshot.per_id[uid].total)) for uid in requested_ids],
                scope_note="Optional supplied UIDs checked all-time only. Original screenshot date/order filters and any truncated IDs are not inferred.")


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("csv", nargs="+")
    parser.add_argument("--report", type=Path)
    parser.add_argument("--uids", nargs="*", default=[])
    args = parser.parse_args()
    report = verify([Path(path).resolve() for path in args.csv], args.uids)
    if args.report:
        args.report.parent.mkdir(parents=True, exist_ok=True)
        args.report.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf8")
    print(json.dumps(report, ensure_ascii=False, indent=2))
