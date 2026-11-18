"""Progress sheet: download a per-company Excel form, upload it back as progress logs.

Only the system's own form is accepted (hidden `_meta` sheet carries the company id), so columns never drift."""
import io
import sqlite3

import openpyxl
from openpyxl.styles import Alignment, Font, PatternFill
from openpyxl.worksheet.datavalidation import DataValidation

from . import logic
from .weeks import parse_week_key, week_key

SHEET = "진행사항"
META = "_meta"
HEADER_ROW = 4
COLUMNS = ("NO", "과제명", "담당자", "상태", "현재 진척률", "진척률(%)", "진행 내용", "이슈")
EDITABLE = (6, 7, 8)  # F..H
WIDTHS = (6, 36, 14, 9, 12, 11, 60, 30)


class FormError(Exception):
    pass


def build_form(conn: sqlite3.Connection, company_id: int, year: int, week: int) -> tuple[bytes, str]:
    company = conn.execute("SELECT * FROM companies WHERE id=?", (company_id,)).fetchone()
    if company is None:
        raise LookupError("company not found")
    tasks = logic.company_tasks(conn, company_id)
    logs = {r["task_id"]: r for r in conn.execute(
        "SELECT * FROM progress_logs WHERE year=? AND week=? AND task_id IN (SELECT id FROM tasks WHERE company_id=?)",
        (year, week, company_id))}

    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = SHEET
    ws["A1"] = f"{company['name']} 진행사항"
    ws["A1"].font = Font(bold=True, size=14)
    ws["A2"] = "주차"
    ws["B2"] = week_key(year, week)
    ws["C2"] = "← 다른 주차로 올리려면 이 값을 바꾸세요 (예: 2026-W47)"
    ws["A3"] = "노란 칸(진척률·진행 내용·이슈)만 입력하세요. 빈 줄은 반영하지 않습니다."
    ws["A3"].font = Font(italic=True, color="FF5F6B7A")
    head_fill = PatternFill("solid", fgColor="FFE8EEFF")
    edit_fill = PatternFill("solid", fgColor="FFFFF4CC")
    for i, (name, width) in enumerate(zip(COLUMNS, WIDTHS), start=1):
        c = ws.cell(HEADER_ROW, i, name)
        c.font = Font(bold=True)
        c.fill = head_fill
        ws.column_dimensions[c.column_letter].width = width
    for r, t in enumerate(tasks, start=HEADER_ROW + 1):
        lg = logs.get(t["id"])
        values = (t["no"], t["title"], t["owner"], t["status"], f"{t['progress']}%",
                  lg["progress"] if lg else None, lg["content"] if lg else None, lg["issue"] if lg else None)
        for i, v in enumerate(values, start=1):
            c = ws.cell(r, i, v)
            c.alignment = Alignment(vertical="top", wrap_text=i in (2, 7, 8))
            if i in EDITABLE:
                c.fill = edit_fill
    last = HEADER_ROW + max(len(tasks), 1)
    dv = DataValidation(type="whole", operator="between", formula1="0", formula2="100", allow_blank=True,
                        showErrorMessage=True, error="0~100 사이 정수를 입력하세요.")
    ws.add_data_validation(dv)
    dv.add(f"F{HEADER_ROW + 1}:F{last}")
    ws.freeze_panes = ws.cell(HEADER_ROW + 1, 3)

    meta = wb.create_sheet(META)
    meta["A1"], meta["B1"] = "company_id", company_id
    meta["A2"], meta["B2"] = "company_name", company["name"]
    meta.sheet_state = "hidden"

    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue(), f"진행사항_{company['name']}_{week_key(year, week)}.xlsx"


def _text(v) -> str | None:
    if v is None:
        return None
    s = str(v).strip()
    return s or None


def parse_upload(conn: sqlite3.Connection, company_id: int, data: bytes) -> dict:
    """Compare an uploaded form with the DB. Returns {week, changes, errors, unchanged, blank}."""
    try:
        wb = openpyxl.load_workbook(io.BytesIO(data), data_only=True)
    except Exception:
        raise FormError("엑셀(.xlsx) 파일을 읽을 수 없습니다.")
    if SHEET not in wb.sheetnames or META not in wb.sheetnames:
        raise FormError("이 프로그램의 「진행사항 양식」 파일이 아닙니다. 양식을 내려받아 작성해 주세요.")
    meta = wb[META]
    if meta["B1"].value != company_id:
        raise FormError(f"다른 협력사의 양식입니다 (파일: {meta['B2'].value}).")
    ws = wb[SHEET]
    if tuple(ws.cell(HEADER_ROW, i).value for i in range(1, len(COLUMNS) + 1)) != COLUMNS:
        raise FormError("양식의 열 구성이 바뀌었습니다. 양식을 다시 내려받아 주세요.")
    try:
        year, week = parse_week_key(str(ws["B2"].value or ""))
    except ValueError:
        raise FormError(f"B2 칸의 주차 「{ws['B2'].value}」 형식이 올바르지 않습니다 (예: 2026-W46).")

    tasks = {r["no"]: r for r in conn.execute("SELECT * FROM tasks WHERE company_id=?", (company_id,))}
    changes, errors = [], []
    unchanged = blank = 0
    seen = set()
    for r in range(HEADER_ROW + 1, ws.max_row + 1):
        no, title = ws.cell(r, 1).value, _text(ws.cell(r, 2).value)
        progress_raw, content, issue = ws.cell(r, 6).value, _text(ws.cell(r, 7).value), _text(ws.cell(r, 8).value)
        if no is None and title is None:
            continue
        if progress_raw in (None, "") and content is None and issue is None:
            blank += 1
            continue
        label = f"{r}행"
        task = tasks.get(no) if isinstance(no, int) else None
        if task is None:
            errors.append(f"{label}: NO {no} 과제가 없습니다.")
            continue
        if _text(task["title"]) != title:
            errors.append(f"{label}: NO {no} 과제명이 다릅니다 (시스템: {task['title']} / 파일: {title}).")
            continue
        if no in seen:
            errors.append(f"{label}: NO {no} 가 두 번 나옵니다.")
            continue
        seen.add(no)
        try:
            progress = int(progress_raw) if float(progress_raw) == int(float(progress_raw)) else None
        except (TypeError, ValueError):
            progress = None
        if progress is None or not 0 <= progress <= 100:
            errors.append(f"{label}: NO {no} 진척률 「{progress_raw if progress_raw is not None else '(빈 칸)'}」 — 0~100 정수를 입력하세요.")
            continue
        old = conn.execute("SELECT * FROM progress_logs WHERE task_id=? AND year=? AND week=?",
                           (task["id"], year, week)).fetchone()
        new = {"progress": progress, "content": content, "issue": issue}
        if old and (old["progress"], _text(old["content"]), _text(old["issue"])) == (progress, content, issue):
            unchanged += 1
            continue
        changes.append({"task_id": task["id"], "no": no, "title": task["title"], "kind": "update" if old else "new",
                        "before": {"progress": old["progress"], "content": old["content"], "issue": old["issue"]} if old else None,
                        "after": new})
    return {"week": week_key(year, week), "changes": changes, "errors": errors,
            "unchanged": unchanged, "blank": blank}


def apply_upload(conn: sqlite3.Connection, company_id: int, data: bytes) -> dict:
    """Apply a form with no errors; each row goes through the same rules as the task popup."""
    result = parse_upload(conn, company_id, data)
    if result["errors"]:
        raise FormError("오류가 있어 반영하지 않았습니다. 미리보기의 오류를 고친 뒤 다시 올려 주세요.")
    notices = []
    for ch in result["changes"]:
        task = dict(conn.execute("SELECT * FROM tasks WHERE id=?", (ch["task_id"],)).fetchone())
        payload = {"task": task, "logs": {"upsert": [{"week": result["week"], **ch["after"]}]}}
        try:
            res = logic.save_task(conn, payload, task_id=ch["task_id"])
        except logic.ValidationError as e:  # e.g. the task itself has dates out of order
            raise FormError(f"NO {ch['no']} 반영 실패 — 과제 정보 오류: {'; '.join(e.errors.values())}. "
                            f"앞선 행은 이미 반영되었습니다.")
        notices += [f"NO {ch['no']}: {n}" for n in res["notices"]]
    return {**result, "notices": notices}
