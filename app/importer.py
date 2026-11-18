"""One-time import of the original Excel files.

Run: .venv\\Scripts\\python -m app.importer [--force]
"""
import re
import shutil
import sqlite3
import sys
from datetime import date, datetime

import openpyxl

from . import config, db
from .logic import fmt_num, to_num
from .weeks import now_kst

SRC = config.ROOT
COMPANY_A_TASKS = "협력사A_과제진척관리.xlsx"
COMPANY_A_REPORT = "45W_협력사A_Feedback_Report_261103.xlsx"
COMPANY_B_TASKS = "협력사B-하반기과제.xlsx"
COMPANY_B_REPORT = "44W_협력사B_Feedback_Report_261029.xlsx"
COMPANY_A_LOG_WEEK = (2026, 45)  # S26 file is dated 2026-11-03 (Q4)
STATUS_MAP = {"완료": "완료", "진행중": "진행중", "대기중": "대기", "대기": "대기", "보류": "보류"}
TYPE_BY_SHEET = ("신규", "확산", "고도화")


class ImportFailed(Exception):
    pass


def _s(v) -> str | None:
    """Cell value -> stored text (numbers without trailing .0)."""
    if v is None:
        return None
    if isinstance(v, (int, float)):
        return fmt_num(float(v)).replace(",", "")
    s = str(v).strip()
    return s or None


def _d(v) -> str | None:
    if isinstance(v, datetime):
        return v.date().isoformat()
    if isinstance(v, date):
        return v.isoformat()
    return None


def _period(text: str) -> tuple[str | None, str | None]:
    """"'26.08.18 ~  '26.12.04" -> ('2026-08-18', '2026-12-04')"""
    found = re.findall(r"'(\d{2})\.(\d{2})\.(\d{2})", text or "")
    dates = [f"20{y}-{m}-{d}" for y, m, d in found]
    return (dates[0] if dates else None, dates[1] if len(dates) > 1 else None)


def report_overview(path) -> dict:
    ws = openpyxl.load_workbook(path, data_only=True).worksheets[0]
    start, end = _period(ws["H7"].value)
    return {"name": ws["B7"].value.strip(), "field": ws["D7"].value, "consultant": ws["F7"].value,
            "start_date": start, "end_date": end}


def _insert_company(conn, info: dict, kpi_mode: str) -> int:
    cur = conn.execute(
        "INSERT INTO companies (name, field, consultant, start_date, end_date, kpi_mode, created_at) VALUES (?,?,?,?,?,?,?)",
        (info["name"], info["field"], info["consultant"], info["start_date"], info["end_date"], kpi_mode,
         now_kst().isoformat(timespec="seconds")))
    return cur.lastrowid


def _insert_task(conn, company_id: int, no: int, t: dict) -> int:
    t = {**t, "company_id": company_id, "no": no, "created_at": now_kst().isoformat(timespec="seconds")}
    cols = list(t)
    cur = conn.execute(f"INSERT INTO tasks ({', '.join(cols)}) VALUES ({', '.join('?' * len(cols))})",
                       [t[c] for c in cols])
    return cur.lastrowid


def company_a_report_categories(path) -> dict[str, str]:
    """task title -> 구분 used in the 45W report KPI table."""
    ws = openpyxl.load_workbook(path, data_only=True).worksheets[0]
    out = {}
    in_kpi = False
    for r in range(1, ws.max_row + 1):
        b = ws.cell(r, 2).value
        if isinstance(b, str) and b.startswith("□ KPI"):
            in_kpi = True
            continue
        if in_kpi and isinstance(b, str) and b.startswith("□"):
            break
        title = ws.cell(r, 3).value
        if in_kpi and b and b != "구분" and title:
            out[str(title).strip()] = str(b).strip()
    return out


def import_company_a(conn) -> int:
    info = report_overview(SRC / COMPANY_A_REPORT)
    cid = _insert_company(conn, info, "task")
    cats = company_a_report_categories(SRC / COMPANY_A_REPORT)
    ws = openpyxl.load_workbook(SRC / COMPANY_A_TASKS, data_only=True).worksheets[0]
    corp = None
    no = 0
    for r in range(6, ws.max_row + 1):
        title = _s(ws.cell(r, 5).value)
        if not title or not isinstance(ws.cell(r, 3).value, int):
            continue
        corp = _s(ws.cell(r, 2).value) or corp
        if title not in cats:
            raise ImportFailed(f"협력사A 과제 '{title}' 가 45W 보고서 KPI 표에 없습니다 (구분을 정할 수 없음)")
        raw_status = _s(ws.cell(r, 13).value)
        status = STATUS_MAP.get(raw_status)
        if status is None:
            raise ImportFailed(f"협력사A '{title}' 계획일정 값 '{raw_status}' 을 상태로 바꿀 수 없습니다")
        no += 1
        tid = _insert_task(conn, cid, no, {
            "category": cats[title],
            "sub_category": f"{corp} / {_s(ws.cell(r, 4).value)}",
            "title": title,
            "owner": _s(ws.cell(r, 15).value),
            "status": status,
            "improve_item": _s(ws.cell(r, 6).value),
            "unit": _s(ws.cell(r, 7).value),
            "target": _s(ws.cell(r, 8).value),
            "before_val": _s(ws.cell(r, 9).value),
            "after_val": _s(ws.cell(r, 10).value),
            "effect": _s(ws.cell(r, 11).value),
            "rate": ws.cell(r, 12).value if isinstance(ws.cell(r, 12).value, (int, float)) else None,
        })
        progress = ws.cell(r, 14).value
        content = _s(ws.cell(r, 16).value)
        if content:
            content = re.sub(r"^\.\s*", "", content)
        conn.execute("INSERT INTO progress_logs (task_id, year, week, progress, content, created_at) VALUES (?,?,?,?,?,?)",
                     (tid, *COMPANY_A_LOG_WEEK, round(float(progress or 0) * 100), content,
                      now_kst().isoformat(timespec="seconds")))
    if no == 0:
        raise ImportFailed("협력사A 과제가 0건입니다 — S26 파일 형식을 확인하세요")
    shutil.copyfile(SRC / COMPANY_A_REPORT, config.TEMPLATE_DIR / f"{cid}.xlsx")
    return no


def company_b_category(team: str, dept: str | None) -> str:
    """Normalize team names to the 44W report column names (Q23)."""
    team = team.strip().strip("[]").strip()
    if team == "경영전략실":
        return "경영전략"
    if team == "제품개발2팀":
        return "제품개발2팀(Energy)" if dept and "에너지" in dept else "제품개발2팀(Display)"
    return team


def import_company_b(conn) -> int:
    info = report_overview(SRC / COMPANY_B_REPORT)
    cid = _insert_company(conn, info, "category")
    wb = openpyxl.load_workbook(SRC / COMPANY_B_TASKS, data_only=True)
    no = 0
    for sheet_idx, ws in enumerate(wb.worksheets[:3]):
        header = {str(c.value).split("\n")[0].strip(): c.column for c in ws[4] if c.value}
        col = lambda name: header[name]
        need = ["No", "과제명", "팀", "부서", "AI Scout", "참여 인원", "시작일", "종료일", "주요 개선 내용 (TO-BE)",
                "절감시간", "절감시간 산정 근거", "금액 효과", "정성적 효과", "확인 필요 사항"]
        missing = [n for n in need if n not in header]
        if missing:
            raise ImportFailed(f"{ws.title}: 열 {missing} 을 찾지 못했습니다")
        for r in range(5, ws.max_row + 1):
            if not isinstance(ws.cell(r, col("No")).value, int):
                continue
            title = _s(ws.cell(r, col("과제명")).value)
            team = _s(ws.cell(r, col("팀")).value)
            dept = _s(ws.cell(r, col("부서")).value)
            hours = ws.cell(r, col("절감시간")).value
            money = ws.cell(r, col("금액 효과")).value
            no += 1
            _insert_task(conn, cid, no, {
                "category": company_b_category(team, dept),
                "sub_category": f"{team} / {dept}" if dept else team,
                "task_type": TYPE_BY_SHEET[sheet_idx],
                "title": title,
                "owner": _s(ws.cell(r, col("AI Scout")).value),
                "status": "대기",
                "start_date": _d(ws.cell(r, col("시작일")).value),
                "due_date": _d(ws.cell(r, col("종료일")).value),
                "effect": f"{fmt_num(float(hours))} hr/년" if to_num(hours) is not None else None,
                "participants": _s(ws.cell(r, col("참여 인원")).value),
                "to_be": _s(ws.cell(r, col("주요 개선 내용 (TO-BE)")).value),
                "saved_hours": _s(hours),
                "saved_basis": _s(ws.cell(r, col("절감시간 산정 근거")).value),
                "money_effect": f"{fmt_num(float(money))}원" if to_num(money) is not None else _s(money),
                "qual_effect": _s(ws.cell(r, col("정성적 효과")).value),
                "check_items": _s(ws.cell(r, col("확인 필요 사항")).value),
            })
    if no == 0:
        raise ImportFailed("협력사B 과제가 0건입니다 — 하반기과제 파일 형식을 확인하세요")
    shutil.copyfile(SRC / COMPANY_B_REPORT, config.TEMPLATE_DIR / f"{cid}.xlsx")
    return no


def make_base_template() -> None:
    """Template for companies added later: the 협력사A latest sheet only."""
    wb = openpyxl.load_workbook(SRC / COMPANY_A_REPORT)
    for ws in wb.worksheets[1:]:
        wb.remove(ws)
    wb.worksheets[0].title = "base"
    wb.save(config.TEMPLATE_DIR / "_base.xlsx")


def run(conn: sqlite3.Connection, force: bool = False) -> dict:
    db.init(conn)
    if conn.execute("SELECT COUNT(*) FROM companies").fetchone()[0] and not force:
        raise ImportFailed("이미 데이터가 있습니다. 다시 가져오려면 --force (기존 데이터 삭제)")
    config.TEMPLATE_DIR.mkdir(parents=True, exist_ok=True)
    with conn:
        if force:
            conn.execute("DELETE FROM companies")
        counts = {"협력사A": import_company_a(conn), "협력사B": import_company_b(conn)}
    make_base_template()
    return counts


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    conn = db.connect()
    db.init(conn)
    try:
        if "--force" in sys.argv:
            print("[import] 백업 후 기존 데이터를 지우고 다시 가져옵니다:", db.backup(conn)["path"])
        print("[import] 완료:", run(conn, force="--force" in sys.argv))
    except ImportFailed as e:
        print("[ERROR]", e)
        sys.exit(1)
