from __future__ import annotations

import calendar
import csv
import html
import json
import os
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlencode, urlparse


BASE_DIR = Path(__file__).resolve().parent
DATA_DIR = BASE_DIR / "data"
WORK_ITEMS_CSV = DATA_DIR / "work_items.csv"
TASKS_CSV = DATA_DIR / "tasks.csv"

DATETIME_FORMAT = "%Y-%m-%dT%H:%M"
DISPLAY_DATETIME_FORMAT = "%Y-%m-%d %H:%M"

WORK_FIELDS = [
    "id",
    "title",
    "category",
    "kind",
    "status",
    "meeting_setup_status",
    "start_at",
    "end_at",
    "owner",
    "memo",
]
TASK_FIELDS = [
    "id",
    "parent_id",
    "title",
    "kind",
    "status",
    "start_at",
    "end_at",
    "owner",
    "sort_order",
    "memo",
]

CATEGORIES = ["保守", "会議", "調査", "自社作業", "その他"]
MEETING_TYPES = ["GI", "食農", "全体"]
MEETING_SETUP_STATUSES = ["未設定", "設定済み"]

CASE_STATUSES = [
    "未実施",
    "見積り中",
    "見積もりレビュー",
    "設計書修正中",
    "設計者レビュー中",
    "開発中",
    "開発レビュー中",
    "テスト仕様書作成中",
    "テスト仕様書レビュー中",
    "テスト実施中",
    "テストレビュー中",
    "リリース資材格納中",
    "リリース準備中",
    "リリース待ち",
    "完了",
]
MEETING_STATUSES = ["資料作成中", "資料レビュー中", "完了"]
GENERAL_STATUSES = ["未実施", "進行中", "レビュー中", "完了", "保留"]
TASK_STATUSES = ["未実施", "進行中", "レビュー中", "完了", "保留"]

BAR_COLORS = {
    "保守": "bar-case",
    "会議": "bar-meeting",
    "調査": "bar-research",
    "自社作業": "bar-company",
    "その他": "bar-other",
}

STATUS_MAP = {
    "保守": CASE_STATUSES,
    "会議": MEETING_STATUSES,
    "調査": GENERAL_STATUSES,
    "自社作業": GENERAL_STATUSES,
    "その他": GENERAL_STATUSES,
}


@dataclass(frozen=True)
class WorkItem:
    id: str
    title: str
    category: str
    kind: str
    status: str
    meeting_setup_status: str
    start_at: datetime
    end_at: datetime
    owner: str
    memo: str


@dataclass(frozen=True)
class Task:
    id: str
    parent_id: str
    title: str
    kind: str
    status: str
    start_at: datetime
    end_at: datetime
    owner: str
    sort_order: int
    memo: str


def work_item_label(item: WorkItem) -> str:
    return f"{item.category}: {item.title}: {item.status}"


def task_label(task: Task) -> str:
    kind = task.kind or "子タスク"
    return f"{kind}: {task.title}: {task.status}"


def parse_datetime(value: str) -> datetime:
    return datetime.strptime(value.strip(), DATETIME_FORMAT)


def format_dt(value: datetime) -> str:
    return value.strftime(DISPLAY_DATETIME_FORMAT)


def format_period(start_at: datetime, end_at: datetime) -> str:
    if start_at.date() == end_at.date():
        return f'{start_at.strftime("%m/%d %H:%M")}-{end_at.strftime("%H:%M")}'
    return f'{start_at.strftime("%m/%d %H:%M")}-{end_at.strftime("%m/%d %H:%M")}'


def escape(value: object) -> str:
    return html.escape(str(value), quote=True)


def read_csv(path: Path, fields: list[str]) -> list[dict[str, str]]:
    if not path.exists():
        return []
    with path.open(newline="", encoding="utf-8-sig") as f:
        reader = csv.DictReader(f)
        return [{field: (row.get(field) or "").strip() for field in fields} for row in reader]


def write_csv(path: Path, fields: list[str], rows: list[dict[str, str]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=fields)
        writer.writeheader()
        writer.writerows(rows)


def parse_int(value: str, default: int = 100) -> int:
    try:
        return int(value)
    except ValueError:
        return default


def next_numeric_id(rows: list[dict[str, str]], prefix: str, width: int = 3) -> str:
    max_number = 0
    for row in rows:
        value = row.get("id", "")
        if value.startswith(prefix):
            try:
                max_number = max(max_number, int(value.removeprefix(prefix)))
            except ValueError:
                pass
    return f"{prefix}{max_number + 1:0{width}d}"


def next_work_id(category: str, rows: list[dict[str, str]]) -> str:
    if category == "保守":
        return next_numeric_id(rows, "CRM-", 4)
    return next_numeric_id(rows, "W", 3)


def next_task_id(rows: list[dict[str, str]]) -> str:
    return next_numeric_id(rows, "T", 4)


def load_work_items() -> list[WorkItem]:
    items: list[WorkItem] = []
    for row in read_csv(WORK_ITEMS_CSV, WORK_FIELDS):
        category = normalize_category(row["category"])
        status = normalize_status(category, row["status"])
        try:
            start_at = parse_datetime(row["start_at"])
            end_at = parse_datetime(row["end_at"])
        except ValueError:
            continue
        if end_at < start_at:
            end_at = start_at
        items.append(
            WorkItem(
                id=row["id"],
                title=row["title"],
                category=category,
                kind=row["kind"],
                status=status,
                meeting_setup_status=row["meeting_setup_status"] or "未設定",
                start_at=start_at,
                end_at=end_at,
                owner=row["owner"],
                memo=row["memo"],
            )
        )
    return sorted(items, key=lambda item: (item.start_at, item.id))


def load_tasks() -> list[Task]:
    tasks: list[Task] = []
    for row in read_csv(TASKS_CSV, TASK_FIELDS):
        try:
            start_at = parse_datetime(row["start_at"])
            end_at = parse_datetime(row["end_at"])
        except ValueError:
            continue
        if end_at < start_at:
            end_at = start_at
        tasks.append(
            Task(
                id=row["id"],
                parent_id=row["parent_id"],
                title=row["title"],
                kind=row["kind"],
                status=row["status"] or "未実施",
                start_at=start_at,
                end_at=end_at,
                owner=row["owner"],
                sort_order=parse_int(row["sort_order"]),
                memo=row["memo"],
            )
        )
    return sorted(tasks, key=lambda task: (task.parent_id, task.start_at, task.sort_order, task.id))


def default_status(category: str) -> str:
    if category == "保守":
        return "未実施"
    if category == "会議":
        return "資料作成中"
    return "未実施"


def status_options(category: str) -> list[str]:
    return STATUS_MAP.get(normalize_category(category), GENERAL_STATUSES)


def normalize_category(category: str) -> str:
    if category == "案件":
        return "保守"
    return category if category in CATEGORIES else "その他"


def normalize_status(category: str, status: str) -> str:
    options = status_options(category)
    return status if status in options else default_status(category)


def is_open_status(status: str) -> bool:
    return status not in {"完了", "保留"}


def show_task_on_calendar(status: str) -> bool:
    return status in {"進行中", "レビュー中"}


def tasks_by_parent(tasks: list[Task]) -> dict[str, list[Task]]:
    grouped: dict[str, list[Task]] = {}
    for task in tasks:
        grouped.setdefault(task.parent_id, []).append(task)
    return grouped


def render_page(title: str, body: str, view: str) -> bytes:
    status_map_json = json.dumps(STATUS_MAP, ensure_ascii=False)
    css = """
    :root {
      color-scheme: light;
      --bg: #f5f6f2;
      --ink: #20231f;
      --muted: #667064;
      --line: #d9ded3;
      --panel: #ffffff;
      --accent: #2b7a78;
      --case: #2f5f9f;
      --meeting: #c77920;
      --research: #7a4fa3;
      --company: #317a4c;
      --other: #68707a;
      --danger: #b42318;
    }
    * { box-sizing: border-box; }
    body {
      background: var(--bg);
      color: var(--ink);
      font-family: -apple-system, BlinkMacSystemFont, "Hiragino Sans", "Yu Gothic", sans-serif;
      line-height: 1.5;
      margin: 0;
    }
    header {
      background: #fff;
      border-bottom: 1px solid var(--line);
      position: sticky;
      top: 0;
      z-index: 3;
    }
    .topbar {
      align-items: center;
      display: flex;
      gap: 16px;
      justify-content: space-between;
      margin: 0 auto;
      max-width: 1240px;
      padding: 14px 20px;
    }
    h1 { font-size: 19px; margin: 0; }
    h2 { font-size: 17px; margin: 0; }
    h3 { font-size: 15px; margin: 0 0 10px; }
    nav { display: flex; flex-wrap: wrap; gap: 8px; }
    nav a, .button, button {
      align-items: center;
      background: #fff;
      border: 1px solid var(--line);
      border-radius: 6px;
      color: var(--ink);
      cursor: pointer;
      display: inline-flex;
      font: inherit;
      min-height: 34px;
      padding: 6px 10px;
      text-decoration: none;
    }
    nav a.active, button.primary, .button.primary {
      background: var(--accent);
      border-color: var(--accent);
      color: #fff;
    }
    button.danger {
      background: #fff;
      border-color: #e9b4aa;
      color: var(--danger);
    }
    main {
      margin: 0 auto;
      max-width: 1240px;
      padding: 18px 20px 40px;
    }
    .layout {
      display: grid;
      gap: 16px;
      grid-template-columns: 300px minmax(0, 1fr);
    }
    .panel {
      background: var(--panel);
      border: 1px solid var(--line);
      border-radius: 8px;
      padding: 14px;
    }
    .panel + .panel { margin-top: 14px; }
    .section-title {
      align-items: center;
      display: flex;
      gap: 10px;
      justify-content: space-between;
      margin-bottom: 12px;
    }
    form.grid {
      display: grid;
      gap: 10px;
    }
    label {
      color: var(--muted);
      display: grid;
      font-size: 12px;
      gap: 4px;
    }
    input, select, textarea {
      border: 1px solid var(--line);
      border-radius: 6px;
      color: var(--ink);
      font: inherit;
      min-height: 34px;
      padding: 6px 8px;
      width: 100%;
    }
    textarea { min-height: 72px; resize: vertical; }
    table {
      border-collapse: collapse;
      width: 100%;
    }
    th, td {
      border-bottom: 1px solid var(--line);
      padding: 8px;
      text-align: left;
      vertical-align: top;
    }
    th {
      color: var(--muted);
      font-size: 12px;
      white-space: nowrap;
    }
    .table-wrap { overflow-x: auto; }
    .badge {
      border-radius: 999px;
      display: inline-block;
      font-size: 12px;
      font-weight: 700;
      line-height: 1;
      padding: 5px 7px;
      white-space: nowrap;
    }
    .badge.case { background: #e7eefb; color: var(--case); }
    .badge.meeting { background: #fff0dc; color: var(--meeting); }
    .badge.research { background: #f0e9f8; color: var(--research); }
    .badge.company { background: #e4f3ea; color: var(--company); }
    .badge.other { background: #eceff2; color: var(--other); }
    .cards {
      display: grid;
      gap: 10px;
    }
    .item-card {
      border: 1px solid var(--line);
      border-radius: 8px;
      padding: 12px;
    }
    .item-head {
      align-items: flex-start;
      display: flex;
      gap: 10px;
      justify-content: space-between;
    }
    .item-title { font-weight: 800; }
    .meta {
      color: var(--muted);
      font-size: 12px;
      margin-top: 4px;
    }
    .actions {
      display: flex;
      flex-wrap: wrap;
      gap: 6px;
    }
    .inline-form {
      display: contents;
    }
    .inline-control {
      min-width: 118px;
    }
    .calendar-timeline {
      display: grid;
      gap: 14px;
    }
    .week-block {
      background: #fff;
      border: 1px solid var(--line);
      border-radius: 8px;
      overflow: hidden;
    }
    .week-days, .week-bars {
      display: grid;
      grid-template-columns: repeat(7, minmax(0, 1fr));
    }
    .week-days {
      background: #f4f6f1;
      border-bottom: 1px solid var(--line);
    }
    .day-head {
      border-right: 1px solid var(--line);
      min-height: 42px;
      padding: 6px 8px;
    }
    .day-head:last-child { border-right: 0; }
    .day-head.outside {
      background: #eef0ea;
      color: #9aa197;
    }
    .day-name {
      color: var(--muted);
      display: block;
      font-size: 11px;
      font-weight: 700;
    }
    .date-number { font-size: 15px; font-weight: 800; }
    .week-bars {
      background-image: linear-gradient(to right, transparent calc(100% / 7 - 1px), var(--line) calc(100% / 7 - 1px));
      background-size: calc(100% / 7) 100%;
      grid-auto-rows: minmax(28px, auto);
      padding: 8px 5px 10px;
      row-gap: 5px;
    }
    .calendar-bar {
      border: 0;
      border-radius: 5px;
      color: #fff;
      font-size: 12px;
      font-weight: 800;
      overflow: hidden;
    }
    .calendar-bar summary {
      cursor: pointer;
      display: block;
      list-style: none;
      overflow: hidden;
      padding: 5px 8px;
      text-overflow: ellipsis;
      white-space: nowrap;
    }
    .calendar-bar summary::-webkit-details-marker { display: none; }
    .calendar-bar.task {
      opacity: .92;
    }
    .calendar-bar.task summary { padding-left: 18px; }
    .bar-case { background: var(--case); }
    .bar-meeting { background: var(--meeting); }
    .bar-research { background: var(--research); }
    .bar-company { background: var(--company); }
    .bar-other { background: var(--other); }
    .bar-task { background: #5c6f78; }
    .bar-detail {
      background: #fff;
      border: 1px solid var(--line);
      border-radius: 6px;
      color: var(--ink);
      font-weight: 400;
      margin-top: 2px;
      padding: 8px;
      white-space: normal;
    }
    .child-list {
      display: grid;
      gap: 5px;
      margin: 6px 0 0;
      padding: 0;
    }
    .child-list li {
      display: grid;
      gap: 1px;
      list-style: none;
    }
    .child-list small { color: var(--muted); }
    @media (max-width: 880px) {
      .topbar { align-items: flex-start; flex-direction: column; }
      .layout { grid-template-columns: 1fr; }
      main { padding: 12px; }
      table { min-width: 980px; }
      .calendar-bar { font-size: 11px; }
    }
    """
    nav = "".join(
        f'<a class="{ "active" if view == key else "" }" href="/?{urlencode({"view": key})}">{label}</a>'
        for key, label in [
            ("calendar", "カレンダー"),
            ("items", "親タスク"),
            ("tasks", "子タスク"),
        ]
    )
    doc = f"""<!doctype html>
<html lang="ja">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>{escape(title)}</title>
  <style>{css}</style>
</head>
<body>
  <header>
    <div class="topbar">
      <h1>保守タスク管理</h1>
      <nav>{nav}</nav>
    </div>
  </header>
  <main>{body}</main>
  <script>
    const STATUS_OPTIONS = {status_map_json};
    function syncStatusOptions(form) {{
      const category = form.querySelector('select[name="category"]');
      const status = form.querySelector('select[name="status"]');
      if (!category || !status) return;
      const values = STATUS_OPTIONS[category.value] || STATUS_OPTIONS["その他"];
      const current = status.value;
      status.innerHTML = "";
      values.forEach((value) => {{
        const option = document.createElement("option");
        option.value = value;
        option.textContent = value;
        if (value === current) option.selected = true;
        status.appendChild(option);
      }});
      if (!values.includes(current)) status.value = values[0] || "";
    }}
    document.addEventListener("DOMContentLoaded", () => {{
      document.querySelectorAll("form").forEach(syncStatusOptions);
    }});
    document.addEventListener("change", (event) => {{
      if (event.target && event.target.matches('select[name="category"]')) {{
        syncStatusOptions(event.target.closest("form"));
      }}
    }});
  </script>
</body>
</html>"""
    return doc.encode("utf-8")


def render_calendar(query: dict[str, str]) -> str:
    today = date.today()
    year = int(query.get("year", today.year))
    month = int(query.get("month", today.month))
    selected = query.get("selected", f"{year:04d}-{month:02d}-01")
    first = date(year, month, 1)
    previous = add_months(first, -1)
    next_month = add_months(first, 1)
    items = load_work_items()
    tasks = load_tasks()
    grouped = tasks_by_parent(tasks)
    calendar_html = render_timeline_month(year, month, items, tasks, grouped)
    sidebar = render_work_item_form(selected, compact=True)
    return f"""
    <div class="layout">
      <aside>
        {sidebar}
      </aside>
      <section class="panel">
        <div class="section-title">
          <a class="button" href="/?{urlencode({"view": "calendar", "year": previous.year, "month": previous.month})}">前月</a>
          <h2>{year}年{month}月</h2>
          <a class="button" href="/?{urlencode({"view": "calendar", "year": next_month.year, "month": next_month.month})}">翌月</a>
        </div>
        {calendar_html}
      </section>
    </div>
    """


def render_timeline_month(
    year: int,
    month: int,
    items: list[WorkItem],
    tasks: list[Task],
    grouped: dict[str, list[Task]],
) -> str:
    weeks = calendar.Calendar(firstweekday=6).monthdatescalendar(year, month)
    day_names = ["日", "月", "火", "水", "木", "金", "土"]
    blocks = ""
    for week in weeks:
        week_start = week[0]
        week_end = week[-1]
        heads = ""
        for day in week:
            outside = " outside" if day.month != month else ""
            heads += (
                f'<a class="day-head{outside}" href="/?{urlencode({"view": "calendar", "year": year, "month": month, "selected": day.strftime("%Y-%m-%d")})}">'
                f'<span class="day-name">{day_names[(day.weekday() + 1) % 7]}</span>'
                f'<span class="date-number">{day.day}</span>'
                f"</a>"
            )
        visible_items = [
            item
            for item in items
            if item.start_at.date() <= week_end and item.end_at.date() >= week_start
        ]
        visible_tasks = [
            task
            for task in tasks
            if show_task_on_calendar(task.status)
            and task.start_at.date() <= week_end
            and task.end_at.date() >= week_start
        ]
        bars = "".join(render_item_bar(item, week_start, week_end, grouped.get(item.id, [])) for item in visible_items)
        bars += "".join(render_task_bar(task, week_start, week_end) for task in visible_tasks)
        if not bars:
            bars = '<div style="grid-column:1 / span 7; color:var(--muted); font-size:12px; padding:5px 8px;">予定はありません</div>'
        blocks += f'<div class="week-block"><div class="week-days">{heads}</div><div class="week-bars">{bars}</div></div>'
    return f'<div class="calendar-timeline">{blocks}</div>'


def render_item_bar(item: WorkItem, week_start: date, week_end: date, children: list[Task]) -> str:
    grid_start, grid_span = week_grid(item.start_at.date(), item.end_at.date(), week_start, week_end)
    color = BAR_COLORS.get(item.category, "bar-other")
    label = work_item_label(item)
    child_items = "".join(
        f'<li><strong>{escape(task.title)}</strong><small>{escape(format_period(task.start_at, task.end_at))} / {escape(task.status)} / {escape(task.owner or "-")}</small></li>'
        for task in sorted(children, key=lambda task: (task.start_at, task.sort_order, task.title))
    )
    if not child_items:
        child_items = '<li><small>子タスクはありません。</small></li>'
    return f"""
    <details class="calendar-bar {color}" style="grid-column:{grid_start} / span {grid_span};">
      <summary title="{escape(format_period(item.start_at, item.end_at))}">{escape(label)}</summary>
      <div class="bar-detail">
        <div>{escape(item.category)} / {escape(item.kind or "-")} / {escape(item.status)} / {escape(item.owner or "-")}</div>
        <div class="meta">{escape(format_period(item.start_at, item.end_at))}</div>
        <ul class="child-list">{child_items}</ul>
        <div class="actions" style="margin-top:8px">
          <a class="button" href="/?{urlencode({"view": "tasks", "parent_id": item.id})}">子タスクを開く</a>
        </div>
      </div>
    </details>
    """


def render_task_bar(task: Task, week_start: date, week_end: date) -> str:
    grid_start, grid_span = week_grid(task.start_at.date(), task.end_at.date(), week_start, week_end)
    label = task_label(task)
    return f"""
    <details class="calendar-bar bar-task task" style="grid-column:{grid_start} / span {grid_span};">
      <summary title="{escape(format_period(task.start_at, task.end_at))}">{escape(label)}</summary>
      <div class="bar-detail">
        <div>{escape(task.status)} / {escape(task.owner or "-")}</div>
        <div class="meta">{escape(format_period(task.start_at, task.end_at))}</div>
        <div>{escape(task.memo)}</div>
      </div>
    </details>
    """


def week_grid(start_day: date, end_day: date, week_start: date, week_end: date) -> tuple[int, int]:
    clipped_start = max(start_day, week_start)
    clipped_end = min(end_day, week_end)
    return (clipped_start - week_start).days + 1, (clipped_end - clipped_start).days + 1


def render_items(query: dict[str, str]) -> str:
    items = load_work_items()
    tasks = load_tasks()
    grouped = tasks_by_parent(tasks)
    rows = "".join(render_item_row(item, grouped.get(item.id, [])) for item in items)
    return f"""
    <div class="layout">
      <aside>{render_work_item_form(date.today().strftime("%Y-%m-%d"), compact=False)}</aside>
      <section class="panel">
        <div class="section-title"><h2>親タスク</h2><span class="meta">{len(items)}件</span></div>
        <div class="cards">{rows}</div>
      </section>
    </div>
    """


def render_item_row(item: WorkItem, children: list[Task]) -> str:
    badge_class = {
        "保守": "case",
        "会議": "meeting",
        "調査": "research",
        "自社作業": "company",
    }.get(item.category, "other")
    child_count = len(children)
    return f"""
    <article class="item-card">
      <div class="item-head">
        <div>
          <div class="item-title">{escape(work_item_label(item))}</div>
          <div class="meta">{escape(format_period(item.start_at, item.end_at))} / {escape(item.owner or "-")}</div>
        </div>
        <span class="badge {badge_class}">{escape(item.category)}</span>
      </div>
      <div class="meta">{escape(item.kind or "-")} / {escape(item.status)} / 会議設定: {escape(item.meeting_setup_status or "-")} / 子タスク {child_count}件</div>
      <div style="margin-top:10px">{render_item_edit_form(item)}</div>
    </article>
    """


def render_work_item_form(selected_date: str, compact: bool) -> str:
    start_value = f"{selected_date}T09:00"
    end_value = f"{selected_date}T17:00"
    title = "カレンダーから作成" if compact else "親タスクを作成"
    return f"""
    <section class="panel">
      <h3>{title}</h3>
      <form class="grid" method="post" action="/add_work_item">
        <label>種別{select_control("category", "保守", CATEGORIES)}</label>
        <label>タイトル<input name="title" required placeholder="例: 顧客検索条件の追加"></label>
        <label>種類<input name="kind" placeholder="保守なら空欄可 / 会議なら GI など"></label>
        <label>状態{select_control("status", "未実施", status_options("保守"))}</label>
        <label>会議設定{select_control("meeting_setup_status", "未設定", MEETING_SETUP_STATUSES)}</label>
        <label>開始<input name="start_at" type="datetime-local" value="{escape(start_value)}" required></label>
        <label>終了<input name="end_at" type="datetime-local" value="{escape(end_value)}" required></label>
        <label>担当<input name="owner"></label>
        <label>メモ<textarea name="memo"></textarea></label>
        <label><span><input name="with_defaults" type="checkbox" value="1" checked style="width:auto; min-height:0;"> 標準の子タスクを作る</span></label>
        <button class="primary" type="submit">作成</button>
      </form>
    </section>
    """


def render_item_edit_form(item: WorkItem) -> str:
    form_id = f"item-{item.id}"
    return f"""
    <form class="grid" id="{escape(form_id)}" method="post" action="/update_work_item">
      <input type="hidden" name="id" value="{escape(item.id)}">
      <label>タイトル<input name="title" value="{escape(item.title)}" required></label>
      <label>種別{select_control("category", item.category, CATEGORIES)}</label>
      <label>種類<input name="kind" value="{escape(item.kind)}"></label>
      <label>状態{select_control("status", item.status, status_options(item.category))}</label>
      <label>会議設定{select_control("meeting_setup_status", item.meeting_setup_status, MEETING_SETUP_STATUSES)}</label>
      <label>開始<input name="start_at" type="datetime-local" value="{item.start_at.strftime(DATETIME_FORMAT)}" required></label>
      <label>終了<input name="end_at" type="datetime-local" value="{item.end_at.strftime(DATETIME_FORMAT)}" required></label>
      <label>担当<input name="owner" value="{escape(item.owner)}"></label>
      <label>メモ<textarea name="memo">{escape(item.memo)}</textarea></label>
      <button type="submit">保存</button>
    </form>
    <div class="actions" style="margin-top:8px">
      <form method="post" action="/delete_work_item">
        <input type="hidden" name="id" value="{escape(item.id)}">
        <button class="danger" type="submit">削除</button>
      </form>
      <a class="button" href="/?{urlencode({"view": "tasks", "parent_id": item.id})}">子タスク</a>
    </div>
    """


def render_tasks(query: dict[str, str]) -> str:
    items = load_work_items()
    item_by_id = {item.id: item for item in items}
    tasks = load_tasks()
    parent_id = query.get("parent_id", "")
    if parent_id:
        tasks = [task for task in tasks if task.parent_id == parent_id]
    parent_options = [("", "")] + [(item.id, work_item_label(item)) for item in items]
    rows = "".join(render_task_row(task, item_by_id.get(task.parent_id)) for task in tasks)
    return f"""
    <div class="layout">
      <aside>{render_task_form(parent_id, parent_options)}</aside>
      <section class="panel">
        <div class="section-title"><h2>子タスク</h2><span class="meta">{len(tasks)}件</span></div>
        <div class="table-wrap">
          <table>
            <thead><tr><th>親</th><th>子タスク</th><th>種類</th><th>状態</th><th>期間</th><th>担当</th><th>操作</th></tr></thead>
            <tbody>{rows or '<tr><td colspan="7">子タスクはありません。</td></tr>'}</tbody>
          </table>
        </div>
      </section>
    </div>
    """


def render_task_form(parent_id: str, parent_options: list[tuple[str, str]]) -> str:
    today = date.today().strftime("%Y-%m-%d")
    return f"""
    <section class="panel">
      <h3>子タスクを作成</h3>
      <form class="grid" method="post" action="/add_task">
        <label>親タスク{select_control_with_labels("parent_id", parent_id, parent_options)}</label>
        <label>タイトル<input name="title" required></label>
        <label>種類<input name="kind"></label>
        <label>状態{select_control("status", "未実施", TASK_STATUSES)}</label>
        <label>開始<input name="start_at" type="datetime-local" value="{today}T09:00" required></label>
        <label>終了<input name="end_at" type="datetime-local" value="{today}T17:00" required></label>
        <label>担当<input name="owner"></label>
        <label>並び順<input name="sort_order" type="number" value="100"></label>
        <label>メモ<textarea name="memo"></textarea></label>
        <button class="primary" type="submit">作成</button>
      </form>
    </section>
    """


def render_task_row(task: Task, parent: WorkItem | None) -> str:
    parent_label = work_item_label(parent) if parent else task.parent_id
    return f"""
    <tr>
      <td>{escape(parent_label)}</td>
      <td>{escape(task_label(task))}</td>
      <td>{escape(task.kind)}</td>
      <td>{escape(task.status)}</td>
      <td>{escape(format_period(task.start_at, task.end_at))}</td>
      <td>{escape(task.owner or "-")}</td>
      <td>{render_task_edit_form(task)}</td>
    </tr>
    """


def render_task_edit_form(task: Task) -> str:
    return f"""
    <details>
      <summary>編集</summary>
      <form class="grid" method="post" action="/update_task">
        <input type="hidden" name="id" value="{escape(task.id)}">
        <input type="hidden" name="parent_id" value="{escape(task.parent_id)}">
        <label>タイトル<input name="title" value="{escape(task.title)}" required></label>
        <label>種類<input name="kind" value="{escape(task.kind)}"></label>
        <label>状態{select_control("status", task.status, TASK_STATUSES)}</label>
        <label>開始<input name="start_at" type="datetime-local" value="{task.start_at.strftime(DATETIME_FORMAT)}" required></label>
        <label>終了<input name="end_at" type="datetime-local" value="{task.end_at.strftime(DATETIME_FORMAT)}" required></label>
        <label>担当<input name="owner" value="{escape(task.owner)}"></label>
        <label>並び順<input name="sort_order" type="number" value="{task.sort_order}"></label>
        <label>メモ<textarea name="memo">{escape(task.memo)}</textarea></label>
        <button type="submit">保存</button>
      </form>
      <div class="actions" style="margin-top:8px">
          <form method="post" action="/delete_task">
            <input type="hidden" name="id" value="{escape(task.id)}">
            <button class="danger" type="submit">削除</button>
          </form>
      </div>
    </details>
    """


def select_control(name: str, selected: str, options: list[str]) -> str:
    seen: set[str] = set()
    option_html = ""
    for option in options:
        if option in seen:
            continue
        seen.add(option)
        is_selected = " selected" if option == selected else ""
        display = "なし" if option == "" else option
        option_html += f'<option value="{escape(option)}"{is_selected}>{escape(display)}</option>'
    return f'<select name="{escape(name)}">{option_html}</select>'


def select_control_with_labels(name: str, selected: str, options: list[tuple[str, str]]) -> str:
    seen: set[str] = set()
    option_html = ""
    for value, label in options:
        if value in seen:
            continue
        seen.add(value)
        is_selected = " selected" if value == selected else ""
        display = "なし" if label == "" else label
        option_html += f'<option value="{escape(value)}"{is_selected}>{escape(display)}</option>'
    return f'<select name="{escape(name)}">{option_html}</select>'


def add_months(value: date, months: int) -> date:
    month_index = value.month - 1 + months
    year = value.year + month_index // 12
    month = month_index % 12 + 1
    return date(year, month, 1)


def default_tasks_for(category: str, start_at: datetime, end_at: datetime, owner: str) -> list[dict[str, str]]:
    category = normalize_category(category)
    if category == "保守":
        specs = [
            ("見積もり", "見積もり", 0, 2),
            ("設計書修正", "設計", 3, 5),
            ("開発", "開発", 6, 9),
            ("テスト仕様書作成", "テスト準備", 10, 11),
            ("テスト", "テスト", 12, 14),
            ("リリース準備", "リリース", 15, 16),
        ]
    elif category == "会議":
        specs = [
            ("事前の会議設定", "会議設定", -7, -7),
            ("資料作成", "資料", -5, -2),
            ("内部事前会議", "内部会議", -2, -2),
        ]
    elif category == "調査":
        specs = [
            ("調査観点整理", "整理", 0, 0),
            ("調査実施", "調査", 1, 3),
            ("結果まとめ", "報告", 4, 4),
        ]
    else:
        specs = [("作業", category, 0, 0)]

    rows: list[dict[str, str]] = []
    for index, (title, kind, start_delta, end_delta) in enumerate(specs, start=1):
        task_start = start_at + timedelta(days=start_delta)
        task_end = min(end_at, start_at + timedelta(days=end_delta, hours=8))
        if task_end < task_start:
            task_end = task_start + timedelta(hours=1)
        rows.append(
            {
                "id": "",
                "parent_id": "",
                "title": title,
                "kind": kind,
                "status": "未実施",
                "start_at": task_start.strftime(DATETIME_FORMAT),
                "end_at": task_end.strftime(DATETIME_FORMAT),
                "owner": owner,
                "sort_order": str(index * 10),
                "memo": "",
            }
        )
    return rows


def redirect_body(location: str) -> bytes:
    return f"Redirecting to {escape(location)}".encode("utf-8")


class TaskAppHandler(BaseHTTPRequestHandler):
    def do_HEAD(self) -> None:
        self.send_response(HTTPStatus.OK)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.end_headers()

    def do_GET(self) -> None:
        parsed = urlparse(self.path)
        if parsed.path != "/":
            self.send_error(HTTPStatus.NOT_FOUND, "Not found")
            return
        query = {key: values[-1] for key, values in parse_qs(parsed.query).items()}
        view = query.get("view", "calendar")
        if view == "items":
            body = render_items(query)
        elif view == "tasks":
            body = render_tasks(query)
        else:
            view = "calendar"
            body = render_calendar(query)
        self.send_response(HTTPStatus.OK)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.end_headers()
        self.wfile.write(render_page("保守タスク管理", body, view))

    def do_POST(self) -> None:
        length = int(self.headers.get("Content-Length", "0"))
        form = {
            key: values[-1].strip()
            for key, values in parse_qs(self.rfile.read(length).decode("utf-8")).items()
        }
        if self.path == "/add_work_item":
            self.add_work_item(form)
            return
        if self.path == "/update_work_item":
            self.update_work_item(form)
            return
        if self.path == "/delete_work_item":
            self.delete_work_item(form)
            return
        if self.path == "/add_task":
            self.add_task(form)
            return
        if self.path == "/update_task":
            self.update_task(form)
            return
        if self.path == "/delete_task":
            self.delete_task(form)
            return
        self.send_error(HTTPStatus.NOT_FOUND, "Not found")

    def add_work_item(self, form: dict[str, str]) -> None:
        rows = read_csv(WORK_ITEMS_CSV, WORK_FIELDS)
        task_rows = read_csv(TASKS_CSV, TASK_FIELDS)
        category = normalize_category(form.get("category", "その他"))
        work_id = next_work_id(category, rows)
        status = normalize_status(category, form.get("status", ""))
        row = {
            "id": work_id,
            "title": form.get("title", ""),
            "category": category,
            "kind": form.get("kind", ""),
            "status": status,
            "meeting_setup_status": form.get("meeting_setup_status", "未設定"),
            "start_at": form.get("start_at", ""),
            "end_at": form.get("end_at", ""),
            "owner": form.get("owner", ""),
            "memo": form.get("memo", ""),
        }
        rows.append(row)
        write_csv(WORK_ITEMS_CSV, WORK_FIELDS, rows)
        if form.get("with_defaults") == "1":
            try:
                start_at = parse_datetime(row["start_at"])
                end_at = parse_datetime(row["end_at"])
            except ValueError:
                start_at = datetime.now()
                end_at = start_at + timedelta(hours=1)
            for template in default_tasks_for(category, start_at, end_at, row["owner"]):
                template["id"] = next_task_id(task_rows)
                template["parent_id"] = work_id
                task_rows.append(template)
            write_csv(TASKS_CSV, TASK_FIELDS, task_rows)
        self.respond_redirect(f"/?view=tasks&parent_id={work_id}")

    def update_work_item(self, form: dict[str, str]) -> None:
        rows = read_csv(WORK_ITEMS_CSV, WORK_FIELDS)
        for row in rows:
            if row["id"] == form.get("id", ""):
                row.update({field: form.get(field, row.get(field, "")) for field in WORK_FIELDS if field != "id"})
                row["category"] = normalize_category(row["category"])
                row["status"] = normalize_status(row["category"], row["status"])
                break
        write_csv(WORK_ITEMS_CSV, WORK_FIELDS, rows)
        self.respond_redirect("/?view=items")

    def delete_work_item(self, form: dict[str, str]) -> None:
        item_id = form.get("id", "")
        rows = [row for row in read_csv(WORK_ITEMS_CSV, WORK_FIELDS) if row["id"] != item_id]
        tasks = [row for row in read_csv(TASKS_CSV, TASK_FIELDS) if row["parent_id"] != item_id]
        write_csv(WORK_ITEMS_CSV, WORK_FIELDS, rows)
        write_csv(TASKS_CSV, TASK_FIELDS, tasks)
        self.respond_redirect("/?view=items")

    def add_task(self, form: dict[str, str]) -> None:
        rows = read_csv(TASKS_CSV, TASK_FIELDS)
        parent_id = form.get("parent_id", "")
        rows.append(
            {
                "id": next_task_id(rows),
                "parent_id": parent_id,
                "title": form.get("title", ""),
                "kind": form.get("kind", ""),
                "status": form.get("status", "未実施"),
                "start_at": form.get("start_at", ""),
                "end_at": form.get("end_at", ""),
                "owner": form.get("owner", ""),
                "sort_order": form.get("sort_order", "100"),
                "memo": form.get("memo", ""),
            }
        )
        write_csv(TASKS_CSV, TASK_FIELDS, rows)
        self.respond_redirect(f"/?view=tasks&parent_id={parent_id}")

    def update_task(self, form: dict[str, str]) -> None:
        rows = read_csv(TASKS_CSV, TASK_FIELDS)
        parent_id = form.get("parent_id", "")
        for row in rows:
            if row["id"] == form.get("id", ""):
                row.update({field: form.get(field, row.get(field, "")) for field in TASK_FIELDS if field != "id"})
                parent_id = row["parent_id"]
                break
        write_csv(TASKS_CSV, TASK_FIELDS, rows)
        self.respond_redirect(f"/?view=tasks&parent_id={parent_id}")

    def delete_task(self, form: dict[str, str]) -> None:
        parent_id = ""
        rows = []
        for row in read_csv(TASKS_CSV, TASK_FIELDS):
            if row["id"] == form.get("id", ""):
                parent_id = row["parent_id"]
                continue
            rows.append(row)
        write_csv(TASKS_CSV, TASK_FIELDS, rows)
        self.respond_redirect(f"/?view=tasks&parent_id={parent_id}")

    def respond_redirect(self, location: str) -> None:
        self.send_response(HTTPStatus.SEE_OTHER)
        self.send_header("Location", location)
        self.end_headers()
        self.wfile.write(redirect_body(location))

    def log_message(self, format: str, *args: object) -> None:
        print(f"{self.address_string()} - {format % args}")


def ensure_sample_files() -> None:
    DATA_DIR.mkdir(exist_ok=True)
    if not WORK_ITEMS_CSV.exists():
        write_csv(
            WORK_ITEMS_CSV,
            WORK_FIELDS,
            [
                {
                    "id": "CRM-0001",
                    "title": "案件B 本番リリース",
                    "category": "保守",
                    "kind": "CRM保守",
                    "status": "開発中",
                    "meeting_setup_status": "",
                    "start_at": "2026-06-13T09:00",
                    "end_at": "2026-07-03T23:00",
                    "owner": "佐藤",
                    "memo": "検索条件追加対応",
                },
                {
                    "id": "W001",
                    "title": "A社 6月保守定例",
                    "category": "会議",
                    "kind": "GI",
                    "status": "資料作成中",
                    "meeting_setup_status": "設定済み",
                    "start_at": "2026-06-18T14:00",
                    "end_at": "2026-06-18T15:00",
                    "owner": "山田",
                    "memo": "月次報告",
                },
                {
                    "id": "W002",
                    "title": "障害傾向の調査",
                    "category": "調査",
                    "kind": "大きめな調査",
                    "status": "進行中",
                    "meeting_setup_status": "",
                    "start_at": "2026-06-10T09:00",
                    "end_at": "2026-06-14T17:00",
                    "owner": "山田",
                    "memo": "問い合わせ増加の原因確認",
                },
            ],
        )
    if not TASKS_CSV.exists():
        write_csv(
            TASKS_CSV,
            TASK_FIELDS,
            [
                {
                    "id": "T0001",
                    "parent_id": "CRM-0001",
                    "title": "見積もり",
                    "kind": "見積もり",
                    "status": "完了",
                    "start_at": "2026-06-13T09:00",
                    "end_at": "2026-06-15T17:00",
                    "owner": "佐藤",
                    "sort_order": "10",
                    "memo": "",
                },
                {
                    "id": "T0002",
                    "parent_id": "CRM-0001",
                    "title": "設計書修正",
                    "kind": "設計",
                    "status": "完了",
                    "start_at": "2026-06-18T09:00",
                    "end_at": "2026-06-20T17:00",
                    "owner": "佐藤",
                    "sort_order": "20",
                    "memo": "",
                },
                {
                    "id": "T0003",
                    "parent_id": "CRM-0001",
                    "title": "開発",
                    "kind": "開発",
                    "status": "進行中",
                    "start_at": "2026-06-21T09:00",
                    "end_at": "2026-06-24T18:00",
                    "owner": "佐藤",
                    "sort_order": "30",
                    "memo": "",
                },
                {
                    "id": "T0004",
                    "parent_id": "CRM-0001",
                    "title": "テスト仕様書作成",
                    "kind": "テスト準備",
                    "status": "未実施",
                    "start_at": "2026-06-25T09:00",
                    "end_at": "2026-06-26T17:00",
                    "owner": "佐藤",
                    "sort_order": "40",
                    "memo": "",
                },
                {
                    "id": "T0005",
                    "parent_id": "W001",
                    "title": "資料作成",
                    "kind": "資料",
                    "status": "進行中",
                    "start_at": "2026-06-13T09:00",
                    "end_at": "2026-06-17T17:00",
                    "owner": "山田",
                    "sort_order": "10",
                    "memo": "",
                },
                {
                    "id": "T0006",
                    "parent_id": "W001",
                    "title": "内部事前会議",
                    "kind": "内部会議",
                    "status": "未実施",
                    "start_at": "2026-06-16T15:00",
                    "end_at": "2026-06-16T16:00",
                    "owner": "山田",
                    "sort_order": "20",
                    "memo": "",
                },
            ],
        )


def main() -> None:
    ensure_sample_files()
    port = int(os.environ.get("PORT", "8000"))
    server = ThreadingHTTPServer(("127.0.0.1", port), TaskAppHandler)
    print(f"Serving on http://127.0.0.1:{port}")
    server.serve_forever()


if __name__ == "__main__":
    main()
