"""Export one aggregation view, never overlapping subtotals in a single amount column."""
import csv
import os
import tempfile
from pathlib import Path

from rebate_engine import DataError, Dataset, QueryResult, STATUS_LABELS, format_money


def safe_cell(value: str) -> str:
    # Defend spreadsheet formula injection in filenames and user-controlled labels.
    return "'" + value if value.lstrip().startswith(("=", "+", "-", "@")) else value


def validate_export_target(path, dataset):
    target = Path(path).expanduser().resolve()
    target_stat = target.stat() if target.exists() else None
    for source in dataset.sources:
        same_identity = (target_stat is not None and source.inode and
                         (target_stat.st_dev, target_stat.st_ino) == (source.device, source.inode))
        if target == source.path or same_identity:
            raise DataError("不能覆盖源 CSV，请另选导出文件名")
        # Also protect a replacement source at the original path or its alias.
        if target_stat is not None and source.path.exists() and os.path.samefile(target, source.path):
            raise DataError("不能覆盖源 CSV，请另选导出文件名")
    return target


def export_csv(path: str | Path, dataset: Dataset, result: QueryResult, view="id",
               status="", search="") -> int:
    target = validate_export_target(path, dataset)
    if view not in ("id", "day", "daily_id"):
        raise DataError("未知导出类型")
    if view == "id":
        rows = [(uid, "", result.per_id[uid]) for uid in sorted(result.visible_ids(status, search))]
    elif view == "day":
        rows = [("", day.isoformat(), a) for day, a in sorted(result.per_day.items())]
    else:
        rows = [(uid, day.isoformat(), a) for (day, uid), a in sorted(result.daily_ids.items())]
    filters = result.filters
    sources = " | ".join(source.path.name for source in dataset.sources)
    prefix = [safe_cell(sources), str(filters.start or "不限"), str(filters.end or "不限"),
              safe_cell(filters.order_type or "全部"), safe_cell(" ".join(dict.fromkeys(filters.ids)) or "全部")]
    temp_path = None
    try:
        with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8-sig", newline="", dir=target.parent,
                                         prefix=".rebate-export-", suffix=".tmp", delete=False) as stream:
            temp_path = Path(stream.name)
            writer = csv.writer(stream)
            writer.writerow(["来源文件", "开始日期(含)", "结束日期(含)", "订单类型筛选", "好友ID筛选",
                             "好友ID（现货）", "日期", "返佣笔数", "返佣收入(USDT)",
                             "所选文件中存在", "UID状态", "全批次笔数", "视图状态筛选", "视图UID搜索"])
            for index, (uid, day, aggregate) in enumerate(rows):
                # Potentially long provenance/ID lists are written ONCE, not once per result row.
                metadata = prefix if index == 0 else ["", *prefix[1:4], ""]
                uid_status = result.statuses.get(uid, "") if view == "id" else ""
                writer.writerow(metadata + [uid, day, aggregate.count, format_money(aggregate.total),
                    ("否" if uid_status == "missing" else "是") if uid_status else "",
                    STATUS_LABELS.get(uid_status, ""), result.lifetime_counts.get(uid, 0) if uid_status else "",
                    safe_cell(status) if view == "id" else "", safe_cell(search) if view == "id" else ""])
            stream.flush()
            os.fsync(stream.fileno())
        validate_export_target(target, dataset)
        os.replace(temp_path, target)
        temp_path = None
    finally:
        if temp_path is not None:
            temp_path.unlink(missing_ok=True)
    return len(rows)
