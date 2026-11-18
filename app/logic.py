"""Task rules: validation, effect calculation, status transitions, stats."""
import re
import sqlite3
from datetime import date

from .weeks import now_kst, parse_week_key, today_kst, week_key

STATUSES = ("대기", "진행중", "완료", "보류")
TASK_TYPES = ("신규", "확산", "고도화")

TEXT_FIELDS = ("category", "sub_category", "task_type", "title", "owner", "improve_item", "unit",
               "target", "before_val", "after_val", "effect", "participants", "to_be",
               "saved_hours", "saved_basis", "money_effect", "qual_effect", "check_items")
DATE_FIELDS = ("start_date", "due_date", "done_date")


class ValidationError(Exception):
    def __init__(self, errors: dict):
        super().__init__("validation failed")
        self.errors = errors


def to_num(value) -> float | None:
    if value is None:
        return None
    if isinstance(value, (int, float)):
        return float(value)
    s = str(value).strip().replace(",", "")
    if re.fullmatch(r"-?\d+(\.\d+)?", s):
        return float(s)
    return None


def fmt_num(x: float) -> str:
    if abs(x - round(x)) < 1e-9:
        return f"{int(round(x)):,}"
    return f"{x:,.2f}".rstrip("0").rstrip(".")


def compute_effect(before, after, unit) -> tuple[str, float | None] | None:
    """'120','1','분/일' -> ('119분↓', 0.99). None when before/after are not both numbers."""
    b, a = to_num(before), to_num(after)
    if b is None or a is None:
        return None
    diff = b - a
    head = (unit or "").split("/")[0].strip()
    arrow = "↓" if diff > 0 else ("↑" if diff < 0 else "")
    effect = f"{fmt_num(abs(diff))}{head}{arrow}"
    rate = abs(diff) / b if b else None
    return effect, rate


def _clean(value):
    if value is None:
        return None
    s = str(value).strip()
    return s or None


def _valid_date(value: str | None) -> bool:
    if value is None:
        return True
    try:
        date.fromisoformat(value)
        return True
    except ValueError:
        return False


# ---------- reading ----------

def latest_logs(conn: sqlite3.Connection, task_ids: list[int]) -> dict[int, sqlite3.Row]:
    if not task_ids:
        return {}
    marks = ",".join("?" * len(task_ids))
    rows = conn.execute(
        f"SELECT * FROM progress_logs WHERE task_id IN ({marks}) ORDER BY task_id, year DESC, week DESC",
        task_ids).fetchall()
    out = {}
    for r in rows:
        out.setdefault(r["task_id"], r)
    return out


def enrich(conn: sqlite3.Connection, rows: list[sqlite3.Row], today: date | None = None) -> list[dict]:
    today = today or today_kst()
    logs = latest_logs(conn, [r["id"] for r in rows])
    out = []
    for r in rows:
        t = dict(r)
        log = logs.get(r["id"])
        t["progress"] = log["progress"] if log else 0
        t["progress_week"] = week_key(log["year"], log["week"]) if log else None
        t["issue"] = (log["issue"] or "").strip() if log else ""
        done = t["status"] == "완료"
        t["delayed"] = bool(not done and t["due_date"] and date.fromisoformat(t["due_date"]) < today)
        t["has_issue"] = bool(not done and t["issue"])
        t["no_due"] = bool(not done and not t["due_date"])
        out.append(t)
    return out


def company_tasks(conn: sqlite3.Connection, company_id: int, today: date | None = None) -> list[dict]:
    rows = conn.execute("SELECT * FROM tasks WHERE company_id = ? ORDER BY no, id", (company_id,)).fetchall()
    return enrich(conn, rows, today)


def stats(tasks: list[dict]) -> dict:
    total = len(tasks)
    done = sum(t["status"] == "완료" for t in tasks)
    return {
        "total": total,
        "in_progress": sum(t["status"] == "진행중" for t in tasks),
        "waiting": sum(t["status"] == "대기" for t in tasks),
        "on_hold": sum(t["status"] == "보류" for t in tasks),
        "done": done,
        "done_rate": done / total if total else 0.0,
        "avg_progress": sum(t["progress"] for t in tasks) / total if total else 0.0,
        "delayed": sum(t["delayed"] for t in tasks),
        "issues": sum(t["has_issue"] for t in tasks),
        "no_due": sum(t["no_due"] for t in tasks),
        "by_category": _category_progress(tasks),
    }


def _category_progress(tasks: list[dict]) -> list[dict]:
    """구분별 평균 진척률(전체 진행률과 같은 방식). 순서는 과제 목록에 처음 나온 순서."""
    groups: dict[str, list[int]] = {}
    for t in tasks:
        groups.setdefault(t["category"] or "미분류", []).append(t["progress"])
    return [{"name": k, "count": len(v), "avg": sum(v) / len(v)} for k, v in groups.items()]


def task_logs(conn: sqlite3.Connection, task_id: int) -> list[dict]:
    rows = conn.execute("SELECT * FROM progress_logs WHERE task_id = ? ORDER BY year DESC, week DESC",
                        (task_id,)).fetchall()
    return [dict(r, week_key=week_key(r["year"], r["week"])) for r in rows]


def delete_impact_company(conn, company_id: int) -> dict:
    q = lambda sql: conn.execute(sql, (company_id,)).fetchone()[0]
    return {
        "tasks": q("SELECT COUNT(*) FROM tasks WHERE company_id = ?"),
        "logs": q("SELECT COUNT(*) FROM progress_logs WHERE task_id IN (SELECT id FROM tasks WHERE company_id = ?)"),
        "notes": q("SELECT COUNT(*) FROM weekly_notes WHERE company_id = ?"),
    }


def delete_impact_task(conn, task_id: int) -> dict:
    return {"logs": conn.execute("SELECT COUNT(*) FROM progress_logs WHERE task_id = ?", (task_id,)).fetchone()[0]}


# ---------- writing ----------

def _validate_task(data: dict) -> tuple[dict, dict]:
    errors = {}
    clean = {f: _clean(data.get(f)) for f in TEXT_FIELDS + DATE_FIELDS}
    if not clean["title"]:
        errors["title"] = "과제명을 입력하세요."
    status = _clean(data.get("status")) or "대기"
    if status not in STATUSES:
        errors["status"] = "상태 값이 올바르지 않습니다."
    clean["status"] = status
    if clean["task_type"] and clean["task_type"] not in TASK_TYPES:
        errors["task_type"] = "유형은 신규/확산/고도화 중 하나입니다."
    for f in DATE_FIELDS:
        if not _valid_date(clean[f]):
            errors[f] = "날짜 형식(YYYY-MM-DD)이 아닙니다."
    if not errors.get("start_date") and not errors.get("due_date") and clean["start_date"] and clean["due_date"] \
            and clean["start_date"] > clean["due_date"]:
        errors["due_date"] = "완료 예정일이 시작일보다 빠릅니다."
    clean["is_added"] = 1 if data.get("is_added") else 0
    return clean, errors


def _validate_logs(logs: dict) -> tuple[list, list, dict]:
    errors = {}
    upserts = []
    for i, item in enumerate(logs.get("upsert") or []):
        try:
            year, week = parse_week_key(str(item.get("week", "")))
        except (ValueError, TypeError):
            errors[f"log{i}.week"] = "주차가 올바르지 않습니다."
            continue
        try:
            progress = int(item.get("progress"))
        except (ValueError, TypeError):
            progress = -1
        if not 0 <= progress <= 100:
            errors[f"log{i}.progress"] = "진척률은 0~100 사이 정수입니다."
            continue
        upserts.append({"id": item.get("id"), "year": year, "week": week, "progress": progress,
                        "content": _clean(item.get("content")), "issue": _clean(item.get("issue"))})
    deletes = [int(x) for x in (logs.get("delete") or [])]
    return upserts, deletes, errors


def _upsert_log(conn, task_id: int, lg: dict) -> None:
    now = now_kst().isoformat(timespec="seconds")
    if lg.get("id"):
        # editing an existing row may move it to another week; merge into that week if taken
        clash = conn.execute("SELECT id FROM progress_logs WHERE task_id=? AND year=? AND week=? AND id<>?",
                             (task_id, lg["year"], lg["week"], lg["id"])).fetchone()
        if clash:
            conn.execute("DELETE FROM progress_logs WHERE id=?", (clash["id"],))
        conn.execute("UPDATE progress_logs SET year=?, week=?, progress=?, content=?, issue=? WHERE id=? AND task_id=?",
                     (lg["year"], lg["week"], lg["progress"], lg["content"], lg["issue"], lg["id"], task_id))
    else:
        conn.execute(
            "INSERT INTO progress_logs (task_id, year, week, progress, content, issue, created_at) VALUES (?,?,?,?,?,?,?) "
            "ON CONFLICT(task_id, year, week) DO UPDATE SET progress=excluded.progress, "
            "content=excluded.content, issue=excluded.issue",
            (task_id, lg["year"], lg["week"], lg["progress"], lg["content"], lg["issue"], now))


def save_task(conn: sqlite3.Connection, payload: dict, task_id: int | None = None,
              company_id: int | None = None, today: date | None = None) -> dict:
    """Create/update a task and its logs in one transaction. Returns {id, notices}."""
    today = today or today_kst()
    data, errors = _validate_task(payload.get("task") or {})
    upserts, deletes, log_errors = _validate_logs(payload.get("logs") or {})
    errors.update(log_errors)
    old = None
    if task_id is not None:
        old = conn.execute("SELECT * FROM tasks WHERE id = ?", (task_id,)).fetchone()
        if old is None:
            raise LookupError("task not found")
        company_id = old["company_id"]
    elif company_id is None or not conn.execute("SELECT 1 FROM companies WHERE id=?", (company_id,)).fetchone():
        errors["company_id"] = "협력사를 선택하세요."
    if errors:
        raise ValidationError(errors)

    notices = []
    # effect: auto only when before/after/unit changed (keeps imported effect text as-is)
    changed = old is None or any((old[f] or None) != data[f] for f in ("before_val", "after_val", "unit"))
    auto = compute_effect(data["before_val"], data["after_val"], data["unit"])
    if auto and (changed or not data["effect"]):
        data["effect"], rate = auto
    elif auto:
        rate = old["rate"] if old is not None else auto[1]
        data["effect"] = old["effect"] if old is not None and old["effect"] else data["effect"]
    else:
        rate = old["rate"] if old is not None and not changed else None

    with conn:
        cols = TEXT_FIELDS + DATE_FIELDS + ("status", "is_added")
        values = [data[c] for c in cols]
        if old is None:
            no = conn.execute("SELECT COALESCE(MAX(no), 0) + 1 FROM tasks WHERE company_id=?", (company_id,)).fetchone()[0]
            cur = conn.execute(
                f"INSERT INTO tasks (company_id, no, {', '.join(cols)}, rate, created_at) "
                f"VALUES (?, ?, {', '.join('?' * len(cols))}, ?, ?)",
                [company_id, no, *values, rate, now_kst().isoformat(timespec="seconds")])
            task_id = cur.lastrowid
        else:
            conn.execute(f"UPDATE tasks SET {', '.join(c + '=?' for c in cols)}, rate=? WHERE id=?",
                         [*values, rate, task_id])

        for log_id in deletes:
            conn.execute("DELETE FROM progress_logs WHERE id=? AND task_id=?", (log_id, task_id))
        for lg in upserts:
            _upsert_log(conn, task_id, lg)

        latest = latest_logs(conn, [task_id]).get(task_id)
        status = data["status"]
        was_done = old is not None and old["status"] == "완료"
        if status == "완료":
            # an already-complete task keeps its (possibly unknown) date; a new completion defaults to today
            done_date = data["done_date"] or (old["done_date"] if was_done else today.isoformat())
            if not latest or latest["progress"] != 100:
                # write 100% on the latest week so it becomes the current progress (a log may be ahead of today)
                y, w = max(tuple(today.isocalendar()[:2]), (latest["year"], latest["week"]) if latest else (0, 0))
                existing = conn.execute("SELECT * FROM progress_logs WHERE task_id=? AND year=? AND week=?",
                                        (task_id, y, w)).fetchone()
                _upsert_log(conn, task_id, {"id": None, "year": y, "week": w, "progress": 100,
                                            "content": existing["content"] if existing else None,
                                            "issue": existing["issue"] if existing else None})
                notices.append(f"완료 처리: {week_key(y, w)} 진척률 100% 기록")
            if not data["done_date"] and not was_done:
                notices.append(f"실제 완료일 {done_date}")
        else:
            done_date = None
            if was_done:
                notices.append("완료 해제: 실제 완료일을 비웠습니다")
            if status == "대기" and upserts and latest and latest["progress"] > 0:
                status = "진행중"
                notices.append("진척 기록이 있어 상태를 「진행중」으로 바꿨습니다")
        conn.execute("UPDATE tasks SET status=?, done_date=? WHERE id=?", (status, done_date, task_id))
    return {"id": task_id, "notices": notices}


COMPANY_FIELDS = ("name", "field", "consultant", "start_date", "end_date", "kpi_mode")


def save_company(conn: sqlite3.Connection, data: dict, company_id: int | None = None) -> int:
    clean = {f: _clean(data.get(f)) for f in COMPANY_FIELDS}
    clean["kpi_mode"] = clean["kpi_mode"] or "task"
    errors = {}
    if not clean["name"]:
        errors["name"] = "회사명을 입력하세요."
    if clean["kpi_mode"] not in ("task", "category"):
        errors["kpi_mode"] = "KPI 표시 방식이 올바르지 않습니다."
    for f in ("start_date", "end_date"):
        if not _valid_date(clean[f]):
            errors[f] = "날짜 형식(YYYY-MM-DD)이 아닙니다."
    if not errors and clean["start_date"] and clean["end_date"] and clean["start_date"] > clean["end_date"]:
        errors["end_date"] = "종료일이 시작일보다 빠릅니다."
    dup = conn.execute("SELECT id FROM companies WHERE name=? AND id IS NOT ?", (clean["name"], company_id)).fetchone()
    if clean["name"] and dup:
        errors["name"] = "같은 이름의 협력사가 있습니다."
    if errors:
        raise ValidationError(errors)
    with conn:
        if company_id is None:
            cur = conn.execute(
                "INSERT INTO companies (name, field, consultant, start_date, end_date, kpi_mode, created_at) "
                "VALUES (?,?,?,?,?,?,?)",
                (*[clean[f] for f in COMPANY_FIELDS], now_kst().isoformat(timespec="seconds")))
            return cur.lastrowid
        conn.execute(f"UPDATE companies SET {', '.join(f + '=?' for f in COMPANY_FIELDS)} WHERE id=?",
                     (*[clean[f] for f in COMPANY_FIELDS], company_id))
        return company_id


NOTE_FIELDS = ("report_date", "summary", "common", "detail", "next_visit_date", "next_visit_time", "next_plan")


def get_note(conn, company_id: int, year: int, week: int) -> dict | None:
    r = conn.execute("SELECT * FROM weekly_notes WHERE company_id=? AND year=? AND week=?",
                     (company_id, year, week)).fetchone()
    return dict(r) if r else None


def save_note(conn, company_id: int, year: int, week: int, data: dict) -> None:
    # memo text keeps its own leading spaces/newlines (the report uses ' - ' indents)
    clean = {f: (str(data[f]) if f in data and data[f] is not None and str(data[f]).strip() else None)
             for f in NOTE_FIELDS}
    for f in ("report_date", "next_visit_date", "next_visit_time"):
        clean[f] = _clean(clean[f])
    errors = {f: "날짜 형식(YYYY-MM-DD)이 아닙니다." for f in ("report_date", "next_visit_date")
              if not _valid_date(clean[f])}
    if errors:
        raise ValidationError(errors)
    with conn:
        conn.execute(
            f"INSERT INTO weekly_notes (company_id, year, week, {', '.join(NOTE_FIELDS)}) "
            f"VALUES (?,?,?,{', '.join('?' * len(NOTE_FIELDS))}) "
            f"ON CONFLICT(company_id, year, week) DO UPDATE SET "
            f"{', '.join(f + '=excluded.' + f for f in NOTE_FIELDS)}",
            (company_id, year, week, *[clean[f] for f in NOTE_FIELDS]))
