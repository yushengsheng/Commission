"""Exact, local-only rebate aggregation. Every CSV record counts, including duplicates."""
from __future__ import annotations

import csv
import re
import unicodedata
from dataclasses import dataclass, field
from datetime import date, datetime
from decimal import Decimal, localcontext
from pathlib import Path
from typing import Callable

ZERO = Decimal("0")
STATUS_LABELS = {
    "positive": "有返佣 · 净额为正",
    "negative": "有记录 · 净额为负",
    "zero": "有记录 · 净额为 0",
    "inactive": "存在 · 当前条件无记录",
    "missing": "所选文件无此 UID",
}
MONEY_PRECISION = 80
ID_COLUMN = "好友ID（现货）"
AMOUNT_COLUMN = "返佣收入(USDT)"
DATE_COLUMN = "返佣时间"
ORDER_COLUMN = "订单类型"
REQUIRED = (ID_COLUMN, AMOUNT_COLUMN, DATE_COLUMN)
MONEY_RE = re.compile(r"[+-]?[0-9]{1,48}(?:\.[0-9]{1,48})?(?:[eE][+-]?[0-9]{1,3})?\Z")
DATE_RE = re.compile(r"[0-9]{4}[-/][0-9]{2}[-/][0-9]{2}(?:[ T][0-9]{2}:[0-9]{2}:[0-9]{2}(?:\.[0-9]{1,6})?)?\Z")


class DataError(ValueError):
    """Invalid input; no partial dataset may be published."""


class ImportCancelled(Exception):
    pass


def normalize_header(value: str) -> str:
    return re.sub(r"\s+", "", unicodedata.normalize("NFKC", value).lstrip("\ufeff")).lower()


def normalize_order_type(value: str) -> str:
    """Canonicalize known exchange labels without merging unknown business values."""
    text = unicodedata.normalize("NFKC", value).strip()
    if not text:
        return "未标注"
    return {
        "spot": "spot",
        "usdt-futures": "USDT-futures",
    }.get(text.casefold(), text)


def parse_ids(text: str) -> tuple[str, ...]:
    """Deduplicate FILTER IDs, never CSV records; preserve leading zeros."""
    items = [s for s in re.split(r"[\s,，;；、]+", text.strip()) if s]
    invalid = [s for s in items if not re.fullmatch(r"[0-9]{1,64}", s)]
    if invalid:
        raise DataError("好友 ID 应为完整数字，不支持科学计数法或模糊匹配：" + "、".join(s[:30] for s in invalid[:3]))
    return tuple(dict.fromkeys(items))


def parse_day(value: str) -> date:
    text = value.strip()
    if not DATE_RE.fullmatch(text):
        raise DataError("返佣时间须为 YYYY-MM-DD 或 YYYY-MM-DD HH:MM:SS，不自动转换时区")
    try:
        return datetime.fromisoformat(text.replace("/", "-")).date()
    except ValueError as exc:
        raise DataError("返佣时间不是有效日期") from exc


def parse_money(value: str) -> Decimal:
    text = value.strip()
    if not MONEY_RE.fullmatch(text):
        raise DataError("返佣收入(USDT) 须为有效十进制金额（支持科学计数法，最多 30 位整数、18 位小数）")
    amount = Decimal(text)
    # Bound the expanded number before formatting; never convert through float.
    if amount.as_tuple().exponent < -18 or amount.as_tuple().exponent > 30 or amount.adjusted() >= 30:
        raise DataError("返佣收入(USDT) 超出精度范围（最多 30 位整数、18 位小数）")
    return Decimal(format(amount, "f"))


def format_money(value: Decimal, grouping: bool = False) -> str:
    places = max(8, -value.as_tuple().exponent)
    return format(value, f",.{places}f" if grouping else f".{places}f")


@dataclass
class Aggregate:
    count: int = 0
    total: Decimal = ZERO

    def add(self, amount: Decimal, count: int = 1) -> None:
        # Callers use an 80-digit local context; inputs are bounded to 48 digits.
        self.count += count
        self.total += amount


@dataclass(frozen=True)
class Filters:
    ids: tuple[str, ...] = ()
    start: date | None = None
    end: date | None = None
    order_type: str | None = None

    def validate(self) -> None:
        if self.start and self.end and self.start > self.end:
            raise DataError("开始日期不能晚于结束日期")


@dataclass
class QueryResult:
    filters: Filters
    total: Decimal
    count: int
    per_id: dict[str, Aggregate]
    per_day: dict[date, Aggregate]
    daily_ids: dict[tuple[date, str], Aggregate]
    missing_ids: tuple[str, ...]
    inactive_ids: tuple[str, ...]
    statuses: dict[str, str] = field(default_factory=dict)
    lifetime_counts: dict[str, int] = field(default_factory=dict)

    @property
    def matched_ids(self) -> int:
        return sum(a.count > 0 for a in self.per_id.values())

    def visible_ids(self, status: str = "", search: str = "") -> list[str]:
        """View-only filtering; never changes settlement totals."""
        return sorted(uid for uid in self.per_id if search.strip() in uid and
                (not status or self.statuses[uid] == status or
                 (status == "no_rebate" and self.statuses[uid] in ("inactive", "zero"))))


@dataclass(frozen=True)
class SourceSummary:
    path: Path
    size_bytes: int
    mtime_ns: int
    device: int
    inode: int
    row_count: int
    total: Decimal
    min_day: date | None
    max_day: date | None

    def verify_unchanged(self):
        current = self.path.stat()
        if (self.size_bytes, self.mtime_ns, self.device, self.inode) != (
                current.st_size, current.st_mtime_ns, current.st_dev, current.st_ino):
            raise DataError(f"{self.path.name}：导入期间源文件发生变化，请重新导入")


@dataclass
class Dataset:
    source: Path
    size_bytes: int
    mtime_ns: int
    encoding: str
    row_count: int
    ids: frozenset[str]
    min_day: date | None
    max_day: date | None
    order_types: tuple[str, ...]
    buckets: dict[tuple[date, str, str], Aggregate] = field(repr=False)
    sources: tuple[SourceSummary, ...] = ()
    uid_bucket_keys: dict[str, list[tuple[date, str, str]]] = field(
        init=False, repr=False, compare=False, default_factory=dict)

    def __post_init__(self) -> None:
        self.uid_bucket_keys = {}
        for key in self.buckets:
            self.uid_bucket_keys.setdefault(key[1], []).append(key)

    def query(self, filters: Filters = Filters()) -> QueryResult:
        filters.validate()
        wanted = set(filters.ids)
        per_id: dict[str, Aggregate] = {}
        per_day: dict[date, Aggregate] = {}
        daily_ids: dict[tuple[date, str], Aggregate] = {}
        total = Aggregate()
        lifetime_counts: dict[str, int] = {}
        with localcontext() as context:
            context.prec = MONEY_PRECISION
            if wanted:
                bucket_items = ((key, self.buckets[key]) for uid in wanted
                                for key in self.uid_bucket_keys.get(uid, ()))
            else:
                bucket_items = self.buckets.items()
            for (day, uid, order), values in bucket_items:
                lifetime_counts[uid] = lifetime_counts.get(uid, 0) + values.count
                if filters.start and day < filters.start:
                    continue
                if filters.end and day > filters.end:
                    continue
                if filters.order_type is not None and order != filters.order_type:
                    continue
                total.add(values.total, values.count)
                per_id.setdefault(uid, Aggregate()).add(values.total, values.count)
                per_day.setdefault(day, Aggregate()).add(values.total, values.count)
                daily_ids.setdefault((day, uid), Aggregate()).add(values.total, values.count)
        candidates = tuple(dict.fromkeys(filters.ids)) if filters.ids else tuple(sorted(self.ids))
        missing = tuple(uid for uid in candidates if uid not in self.ids)
        inactive = tuple(uid for uid in candidates if uid in self.ids and uid not in per_id)
        # Explicitly requested IDs remain visible even when the result is zero.
        for uid in candidates:
            per_id.setdefault(uid, Aggregate())
        statuses = {}
        for uid in candidates:
            value = per_id[uid]
            statuses[uid] = ("missing" if uid not in self.ids else "inactive" if not value.count else
                             "zero" if value.total == 0 else "positive" if value.total > 0 else "negative")
        return QueryResult(filters, total.total, total.count, per_id, per_day, daily_ids,
                           missing, inactive, statuses, lifetime_counts)


def load_csv(
    path: str | Path,
    progress: Callable[[int, int], None] | None = None,
    cancelled: Callable[[], bool] | None = None,
    encoding: str = "utf-8-sig",
) -> Dataset:
    """Build a new dataset transactionally; never append to an existing dataset.

    A CSV row is an independent rebate even if all its cells equal another row.
    Invalid rows abort the import rather than silently reducing the settlement.
    """
    source = Path(path).expanduser().resolve()
    if source.suffix.lower() != ".csv":
        raise DataError("请选择 CSV 文件；ZIP 请先解压")
    if encoding not in ("utf-8-sig", "gb18030"):
        raise DataError("不支持的文件编码")
    stat = source.stat()
    if not source.is_file():
        raise DataError("所选路径不是文件")
    buckets: dict[tuple[date, str, str], Aggregate] = {}
    ids: set[str] = set()
    orders: set[str] = set()
    count = 0
    file_total = Aggregate()
    min_day = max_day = None
    reader = None
    if progress:
        progress(0, 0)
    try:
        with source.open("r", encoding=encoding, newline="") as stream, localcontext() as context:
            context.prec = MONEY_PRECISION
            reader = csv.reader(stream, strict=True)
            header = next(reader, None)
            if not header:
                raise DataError("CSV 为空或没有表头")
            normalized = [normalize_header(h) for h in header]
            for column in REQUIRED:
                if normalized.count(normalize_header(column)) != 1:
                    raise DataError(f"缺少或重复的必要列：{column}")
            positions = {column: normalized.index(normalize_header(column)) for column in REQUIRED}
            order_key = normalize_header(ORDER_COLUMN)
            if normalized.count(order_key) > 1:
                raise DataError("订单类型列重复")
            order_pos = normalized.index(order_key) if order_key in normalized else None
            for row in reader:
                if cancelled and cancelled():
                    raise ImportCancelled()
                if not row or all(not cell.strip() for cell in row):
                    continue
                if len(row) != len(header):
                    raise DataError(f"第 {reader.line_num} 行列数与表头不一致")
                try:
                    uid = row[positions[ID_COLUMN]].strip()
                    if not re.fullmatch(r"[0-9]{1,64}", uid):
                        raise DataError("好友ID（现货）为空或不是完整数字")
                    day = parse_day(row[positions[DATE_COLUMN]])
                    amount = parse_money(row[positions[AMOUNT_COLUMN]])
                except DataError as exc:
                    raise DataError(f"第 {reader.line_num} 行：{exc}") from exc
                order = normalize_order_type(row[order_pos] if order_pos is not None else "")
                # NO row deduplication: identical records increment both total and count.
                buckets.setdefault((day, uid, order), Aggregate()).add(amount)
                file_total.add(amount)
                ids.add(uid)
                orders.add(order)
                count += 1
                min_day = min(min_day, day) if min_day else day
                max_day = max(max_day, day) if max_day else day
                if progress and count % 2048 == 0:
                    # TextIOWrapper.tell() is unavailable during csv iteration.
                    percent = min(99, int(stream.buffer.tell() * 100 / max(stat.st_size, 1)))
                    progress(percent, count)
    except UnicodeError as exc:
        raise DataError("文件编码不匹配。请选择正确编码后重试（通常 UTF-8，旧版中文 CSV 可选 GB18030）。") from exc
    except csv.Error as exc:
        line = reader.line_num if reader else 1
        raise DataError(f"第 {line} 行 CSV 格式错误：{exc}") from exc
    if cancelled and cancelled():
        raise ImportCancelled()
    current = source.stat()
    if (stat.st_size, stat.st_mtime_ns, stat.st_ino) != (current.st_size, current.st_mtime_ns, current.st_ino):
        raise DataError("导入期间源文件发生变化，请待文件保存完成后重新导入")
    if progress:
        progress(100, count)
    summary = SourceSummary(source, stat.st_size, stat.st_mtime_ns, stat.st_dev, stat.st_ino,
                            count, file_total.total, min_day, max_day)
    return Dataset(source, stat.st_size, stat.st_mtime_ns, encoding, count, frozenset(ids),
                   min_day, max_day, tuple(sorted(orders)), buckets, (summary,))


def load_csvs(paths, progress=None, cancelled=None, encoding="utf-8-sig", file_started=None) -> Dataset:
    """One atomic replacement batch. No record/content deduplication, even across files."""
    if isinstance(paths, (str, Path)):
        paths = [paths]
    paths = [Path(path).expanduser().resolve() for path in paths]
    if not paths:
        raise DataError("请至少选择一个 CSV 文件")
    identities = set()
    initial_stamps = {}
    total_bytes = 0
    for path in paths:
        stat = path.stat()
        identity = (stat.st_dev, stat.st_ino) if stat.st_ino else str(path)
        if identity in identities:
            raise DataError(f"重复选择了同一个文件：{path.name}。请每份只选一次；文件内相同记录仍全部累加。")
        identities.add(identity)
        initial_stamps[path] = (stat.st_size, stat.st_mtime_ns, stat.st_dev, stat.st_ino)
        total_bytes += stat.st_size
    batch = None
    done_bytes = done_count = 0
    sources = []
    for index, path in enumerate(paths):
        if cancelled and cancelled():
            raise ImportCancelled()
        if file_started:
            file_started(index + 1, len(paths), path.name)
        current_size = initial_stamps[path][0]
        def report(percent, count):
            if progress:
                progress(min(99, int((done_bytes + current_size * percent / 100) * 100 /
                                     max(total_bytes, 1))), done_count + count)
        try:
            current = path.stat()
            if initial_stamps[path] != (current.st_size, current.st_mtime_ns, current.st_dev, current.st_ino):
                raise DataError("批次导入期间源文件发生变化，请重新导入")
            part = load_csv(path, report, cancelled, encoding)
            imported = part.sources[0]
            if initial_stamps[path] != (imported.size_bytes, imported.mtime_ns, imported.device, imported.inode):
                raise DataError("批次导入期间源文件发生变化，请重新导入")
        except (DataError, OSError) as exc:
            raise DataError(f"{path.name}：{exc}") from exc
        sources.extend(part.sources)
        if batch is None:
            batch = part
        else:
            with localcontext() as context:
                context.prec = MONEY_PRECISION
                for position, (key, value) in enumerate(part.buckets.items()):
                    if position % 2048 == 0 and cancelled and cancelled():
                        raise ImportCancelled()
                    target = batch.buckets.get(key)
                    if target is None:
                        target = batch.buckets[key] = Aggregate()
                        batch.uid_bucket_keys.setdefault(key[1], []).append(key)
                    target.add(value.total, value.count)
            batch.row_count += part.row_count
            batch.size_bytes += part.size_bytes
            batch.ids = batch.ids | part.ids
            batch.order_types = tuple(sorted(set(batch.order_types) | set(part.order_types)))
            days = [day for day in (batch.min_day, part.min_day) if day]
            batch.min_day = min(days) if days else None
            days = [day for day in (batch.max_day, part.max_day) if day]
            batch.max_day = max(days) if days else None
        done_bytes += current_size
        done_count += part.sources[0].row_count
    if cancelled and cancelled():
        raise ImportCancelled()
    # Detect a file changing after it was read, while a later part was importing.
    for source in sources:
        source.verify_unchanged()
    batch.sources = tuple(sources)
    if progress:
        progress(100, batch.row_count)
    return batch
