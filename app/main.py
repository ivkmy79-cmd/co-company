"""FastAPI app: dashboard page + JSON endpoints used by the page's modals."""
import json
import math
from contextlib import asynccontextmanager
from datetime import date, timedelta
from urllib.parse import quote, urlencode

from fastapi import Body, FastAPI, Request
from fastapi.responses import JSONResponse, RedirectResponse, Response
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from . import config, db, logic, progress_io, report
from .icons import icon
from .weeks import current_week, parse_week_key, today_kst, week_key, week_monday, week_of, weeks_between

PAGE_SIZE = 15
SORT_KEYS = ("no", "category", "task_type", "title", "owner", "status", "progress", "effect", "due_date", "done_date")
FILTER_KEYS = ("q", "category", "task_type", "status", "owner", "flag")
FLAGS = {"delayed": "지연", "issue": "이슈", "no_due": "예정일 미입력"}
STATUS_ORDER = {s: i for i, s in enumerate(logic.STATUSES)}


@asynccontextmanager
async def lifespan(_app):
    conn = db.connect()
    db.init(conn)
    res = db.backup(conn)
    tag = "[backup]" if res["ok"] else "[ERROR][backup]"
    print(f"{tag} {res['path']} integrity={res['integrity']} rows={res['counts']}", flush=True)
    conn.close()
    yield


app = FastAPI(lifespan=lifespan)
app.mount("/static", StaticFiles(directory=config.ROOT / "app" / "static"), name="static")
templates = Jinja2Templates(directory=config.ROOT / "app" / "templates")
templates.env.globals["icon"] = icon


def conn():
    c = db.connect()
    db.init(c)
    return c


def bad_request(errors: dict, code: int = 422):
    return JSONResponse({"errors": errors}, status_code=code)


def week_options(company) -> list[dict]:
    start_d = company["start_date"] or company["created_at"][:10]
    start = week_of(date.fromisoformat(start_d))
    cy, cw = current_week()
    end = week_of(week_monday(cy, cw) + timedelta(days=7))
    if week_monday(*start) > week_monday(*end):
        start = (cy, cw)
    out = []
    for y, w in reversed(weeks_between(start, end)):
        mon = week_monday(y, w)
        sun = mon + timedelta(days=6)
        out.append({"key": week_key(y, w), "label": f"{w}주 ({mon.month}/{mon.day}~{sun.month}/{sun.day})"})
    return out


# ---------- page ----------

def _sort_value(t, key):
    v = t.get(key)
    if key == "status":
        return STATUS_ORDER.get(v, 99)
    return v


def _apply_list(tasks, args):
    q = (args.get("q") or "").strip().lower()
    out = []
    for t in tasks:
        if q and q not in " ".join(str(t.get(k) or "") for k in ("title", "owner", "category", "sub_category")).lower():
            continue
        if any(args.get(k) and (t.get(k) or "") != args[k] for k in ("category", "task_type", "status", "owner")):
            continue
        flag = args.get("flag")
        if flag == "delayed" and not t["delayed"] or flag == "issue" and not t["has_issue"] \
                or flag == "no_due" and not t["no_due"]:
            continue
        out.append(t)
    key = args.get("sort") if args.get("sort") in SORT_KEYS else "no"
    desc = args.get("dir") == "desc"
    present = [t for t in out if _sort_value(t, key) not in (None, "")]
    missing = [t for t in out if _sort_value(t, key) in (None, "")]
    present.sort(key=lambda t: (_sort_value(t, key), t["no"]), reverse=desc)
    return present + missing, key, desc


@app.get("/")
def home():
    c = conn()
    first = c.execute("SELECT id FROM companies ORDER BY name").fetchone()
    c.close()
    return RedirectResponse(f"/c/{first['id']}" if first else "/c/0", status_code=303)


@app.get("/c/{company_id}")
def dashboard(request: Request, company_id: int):
    c = conn()
    try:
        companies = []
        for r in c.execute("SELECT * FROM companies ORDER BY name").fetchall():
            ts = logic.company_tasks(c, r["id"])
            companies.append({**dict(r), "count": len(ts), "delayed": sum(t["delayed"] for t in ts)})
        company = next((x for x in companies if x["id"] == company_id), None)
        if company is None and companies:
            return RedirectResponse(f"/c/{companies[0]['id']}", status_code=303)
        args = {k: request.query_params.get(k, "") for k in FILTER_KEYS + ("sort", "dir", "page")}
        ctx = {"companies": companies, "company": company, "args": args, "flags": FLAGS,
               "statuses": logic.STATUSES, "task_types": logic.TASK_TYPES, "sort_keys": SORT_KEYS}
        if company:
            tasks = logic.company_tasks(c, company_id)
            rows, key, desc = _apply_list(tasks, args)
            pages = max(1, math.ceil(len(rows) / PAGE_SIZE))
            try:
                page = min(max(1, int(args["page"] or 1)), pages)
            except ValueError:
                page = 1
            start = (page - 1) * PAGE_SIZE
            filtered = any(args[k] for k in FILTER_KEYS)

            def url(**changes):
                params = {k: v for k, v in {**args, **changes}.items() if v not in ("", None)}
                if "page" not in changes and any(k in changes for k in FILTER_KEYS + ("sort", "dir")):
                    params.pop("page", None)
                return f"/c/{company_id}" + (f"?{urlencode(params)}" if params else "")

            ctx.update({
                "stats": logic.stats(tasks), "rows": rows[start:start + PAGE_SIZE],
                "total_rows": len(rows), "all_rows": len(tasks), "page": page, "pages": pages,
                "range_from": start + 1 if rows else 0, "range_to": min(start + PAGE_SIZE, len(rows)),
                "sort": key, "desc": desc, "sorted": bool(args["sort"]), "filtered": filtered, "url": url,
                "options": {
                    "category": sorted({t["category"] for t in tasks if t["category"]}),
                    "task_type": [x for x in logic.TASK_TYPES if any(t["task_type"] == x for t in tasks)],
                    "status": [s for s in logic.STATUSES if any(t["status"] == s for t in tasks)],
                    "owner": sorted({t["owner"] for t in tasks if t["owner"]}),
                },
                "weeks": week_options(company),
                "current_week": week_key(*current_week()),
                "today": today_kst().isoformat(),
            })
        ctx["boot"] = json.dumps({
            "companyId": company["id"] if company else None,
            "weeks": ctx.get("weeks", []), "currentWeek": ctx.get("current_week"),
            "today": ctx.get("today"), "categories": ctx.get("options", {}).get("category", []),
            "owners": ctx.get("options", {}).get("owner", []),
        }, ensure_ascii=False).replace("</", "<\\/")
        return templates.TemplateResponse(request, "index.html", ctx)
    finally:
        c.close()


# ---------- companies ----------

@app.post("/api/companies")
def create_company(data: dict = Body(...)):
    c = conn()
    try:
        return {"id": logic.save_company(c, data)}
    except logic.ValidationError as e:
        return bad_request(e.errors)
    finally:
        c.close()


@app.get("/api/companies/{company_id}")
def get_company(company_id: int):
    c = conn()
    try:
        r = c.execute("SELECT * FROM companies WHERE id=?", (company_id,)).fetchone()
        if not r:
            return bad_request({"_": "협력사가 없습니다."}, 404)
        return {**dict(r), "impact": logic.delete_impact_company(c, company_id)}
    finally:
        c.close()


@app.put("/api/companies/{company_id}")
def update_company(company_id: int, data: dict = Body(...)):
    c = conn()
    try:
        if not c.execute("SELECT 1 FROM companies WHERE id=?", (company_id,)).fetchone():
            return bad_request({"_": "협력사가 없습니다."}, 404)
        return {"id": logic.save_company(c, data, company_id)}
    except logic.ValidationError as e:
        return bad_request(e.errors)
    finally:
        c.close()


@app.delete("/api/companies/{company_id}")
def delete_company(company_id: int):
    c = conn()
    try:
        with c:
            c.execute("DELETE FROM companies WHERE id=?", (company_id,))
        tpl = config.TEMPLATE_DIR / f"{company_id}.xlsx"
        if tpl.exists():
            tpl.unlink()
        return {"ok": True}
    finally:
        c.close()


# ---------- tasks ----------

@app.get("/api/tasks/{task_id}")
def get_task(task_id: int):
    c = conn()
    try:
        r = c.execute("SELECT * FROM tasks WHERE id=?", (task_id,)).fetchone()
        if not r:
            return bad_request({"_": "과제가 없습니다."}, 404)
        t = logic.enrich(c, [r])[0]
        return {"task": t, "logs": logic.task_logs(c, task_id), "impact": logic.delete_impact_task(c, task_id)}
    finally:
        c.close()


@app.post("/api/tasks")
def create_task(payload: dict = Body(...)):
    c = conn()
    try:
        return logic.save_task(c, payload, company_id=payload.get("company_id"))
    except logic.ValidationError as e:
        return bad_request(e.errors)
    finally:
        c.close()


@app.put("/api/tasks/{task_id}")
def update_task(task_id: int, payload: dict = Body(...)):
    c = conn()
    try:
        return logic.save_task(c, payload, task_id=task_id)
    except logic.ValidationError as e:
        return bad_request(e.errors)
    except LookupError:
        return bad_request({"_": "과제가 없습니다."}, 404)
    finally:
        c.close()


@app.delete("/api/tasks/{task_id}")
def delete_task(task_id: int):
    c = conn()
    try:
        with c:
            c.execute("DELETE FROM tasks WHERE id=?", (task_id,))
        return {"ok": True}
    finally:
        c.close()


# ---------- weekly notes & report ----------

def _week(key: str):
    try:
        return parse_week_key(key)
    except (ValueError, AttributeError):
        return None


@app.get("/api/companies/{company_id}/notes/{wk}")
def get_note(company_id: int, wk: str):
    w = _week(wk)
    if not w:
        return bad_request({"week": "주차가 올바르지 않습니다."})
    c = conn()
    try:
        return {"note": logic.get_note(c, company_id, *w)}
    finally:
        c.close()


@app.put("/api/companies/{company_id}/notes/{wk}")
def put_note(company_id: int, wk: str, data: dict = Body(...)):
    w = _week(wk)
    if not w:
        return bad_request({"week": "주차가 올바르지 않습니다."})
    c = conn()
    try:
        logic.save_note(c, company_id, *w, data)
        return {"ok": True}
    except logic.ValidationError as e:
        return bad_request(e.errors)
    finally:
        c.close()


@app.get("/api/companies/{company_id}/report")
def report_check(company_id: int, week: str):
    w = _week(week)
    if not w:
        return bad_request({"week": "주차가 올바르지 않습니다."})
    c = conn()
    try:
        res = report.build(c, company_id, *w)
        return {"filename": res.filename, "sheets": res.sheets, "warnings": res.warnings}
    finally:
        c.close()


@app.get("/c/{company_id}/report.xlsx")
def report_download(company_id: int, week: str):
    w = _week(week)
    if not w:
        return Response("bad week", status_code=422)
    c = conn()
    try:
        res = report.build(c, company_id, *w)
    finally:
        c.close()
    return Response(res.data, media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                    headers={"Content-Disposition": f"attachment; filename*=UTF-8''{quote(res.filename)}"})


# ---------- progress form (Excel round trip) ----------

MAX_UPLOAD = 5 * 1024 * 1024


@app.get("/c/{company_id}/progress-form.xlsx")
def progress_form(company_id: int, week: str):
    w = _week(week)
    if not w:
        return Response("bad week", status_code=422)
    c = conn()
    try:
        data, filename = progress_io.build_form(c, company_id, *w)
    except LookupError:
        return Response("company not found", status_code=404)
    finally:
        c.close()
    return Response(data, media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                    headers={"Content-Disposition": f"attachment; filename*=UTF-8''{quote(filename)}"})


@app.post("/api/companies/{company_id}/progress-upload")
async def progress_upload(request: Request, company_id: int, apply: int = 0):
    data = await request.body()
    if not data:
        return bad_request({"_": "파일이 비어 있습니다."})
    if len(data) > MAX_UPLOAD:
        return bad_request({"_": "파일이 너무 큽니다 (5MB 이하)."})
    c = conn()
    try:
        return (progress_io.apply_upload if apply else progress_io.parse_upload)(c, company_id, data)
    except progress_io.FormError as e:
        return bad_request({"_": str(e)})
    finally:
        c.close()


@app.post("/api/backup")
def backup_now():
    c = conn()
    try:
        return db.backup(c)
    finally:
        c.close()
