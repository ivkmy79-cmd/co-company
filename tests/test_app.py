import io
import sys
import tempfile
import unittest
from datetime import date
from pathlib import Path

import openpyxl

from app import config, db, importer, logic, progress_io, report

sys.stdout.reconfigure(encoding="utf-8")


def fresh_db(tmp: Path):
    config.TEMPLATE_DIR = tmp / "templates"
    conn = db.connect(tmp / "t.db")
    importer.run(conn)
    return conn


def cid(conn, name):
    return conn.execute("SELECT id FROM companies WHERE name=?", (name,)).fetchone()[0]


def report_counts(path, sheet=0):
    """{category: 총발굴건수} from a report's 과제 추진현황 table."""
    ws = openpyxl.load_workbook(path, data_only=True).worksheets[sheet]
    out = {}
    for r in range(1, 40):
        if ws.cell(r, 2).value == "구분":
            for c in range(3, 11):
                h = ws.cell(r, c).value
                if h and h != "합계" and ws.cell(r + 1, 2).value == "총발굴건수":
                    out[h] = ws.cell(r + 1, c).value or 0
    return out


class ImportTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.conn = fresh_db(Path(self.tmp.name))

    def tearDown(self):
        self.conn.close()
        self.tmp.cleanup()

    def counts(self, name):
        rows = self.conn.execute("SELECT category, COUNT(*) FROM tasks WHERE company_id=? GROUP BY category",
                                 (cid(self.conn, name),)).fetchall()
        return {r[0]: r[1] for r in rows}

    def test_task_totals(self):
        self.assertEqual(sum(self.counts("협력사A").values()), 18)
        self.assertEqual(sum(self.counts("협력사B").values()), 83)

    def test_category_counts_match_reports(self):
        for name, file in (("협력사A", importer.COMPANY_A_REPORT), ("협력사B", importer.COMPANY_B_REPORT)):
            expected = {k: v for k, v in report_counts(config.ROOT / file).items() if v}
            self.assertEqual(self.counts(name), expected, name)

    def test_company_a_week45_logs_and_status(self):
        c = cid(self.conn, "협력사A")
        tasks = logic.company_tasks(self.conn, c)
        s = logic.stats(tasks)
        self.assertEqual((s["done"], s["total"]), (6, 18))
        self.assertAlmostEqual(s["avg_progress"], 65.0, places=1)  # S26 "종합 0.65"
        self.assertTrue(all(t["progress_week"] == "2026-W45" for t in tasks))
        # 팀별 진행률: 과제수 합 = 전체, 과제수 가중평균 = 전체 진행률
        cats = s["by_category"]
        self.assertEqual(sum(g["count"] for g in cats), s["total"])
        self.assertAlmostEqual(sum(g["avg"] * g["count"] for g in cats) / s["total"], s["avg_progress"], places=6)
        self.assertEqual({g["name"] for g in cats}, {t["category"] for t in tasks})

    def test_company_b_initial_state(self):
        tasks = logic.company_tasks(self.conn, cid(self.conn, "협력사B"))
        self.assertTrue(all(t["status"] == "대기" and t["progress"] == 0 for t in tasks))
        first = tasks[0]
        self.assertEqual((first["title"], first["owner"], first["task_type"], first["effect"]),
                         ("총괄 계획 수립 자동화", "담당자A 책임", "신규", "369 hr/년"))


class LogicTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.conn = db.connect(Path(self.tmp.name) / "t.db")
        db.init(self.conn)
        self.c = logic.save_company(self.conn, {"name": "테스트사"})
        self.today = date(2026, 11, 4)

    def tearDown(self):
        self.conn.close()
        self.tmp.cleanup()

    def save(self, task, logs=None, tid=None):
        return logic.save_task(self.conn, {"task": task, "logs": logs or {}}, task_id=tid,
                               company_id=self.c, today=self.today)

    def task(self, tid):
        return logic.enrich(self.conn, [self.conn.execute("SELECT * FROM tasks WHERE id=?", (tid,)).fetchone()],
                            self.today)[0]

    def test_effect(self):
        self.assertEqual(logic.compute_effect("5400", "10", "초/건"), ("5,390초↓", 5390 / 5400))
        self.assertEqual(logic.compute_effect("32.1", "72.2", "%")[0], "40.1%↑")
        self.assertIsNone(logic.compute_effect("수작업", "자동", "Tool"))

    def test_manual_effect_kept_when_values_missing(self):
        tid = self.save({"title": "A", "effect": "불량 유출 차단"})["id"]
        self.assertEqual(self.task(tid)["effect"], "불량 유출 차단")

    def test_imported_effect_not_recomputed_on_unrelated_save(self):
        tid = self.save({"title": "A", "before_val": "120", "after_val": "1", "unit": "분/일"})["id"]
        self.conn.execute("UPDATE tasks SET effect='118분↓' WHERE id=?", (tid,))  # like the S26 import
        self.save({"title": "A2", "before_val": "120", "after_val": "1", "unit": "분/일", "effect": "118분↓"}, tid=tid)
        self.assertEqual(self.task(tid)["effect"], "118분↓")
        self.save({"title": "A2", "before_val": "120", "after_val": "2", "unit": "분/일"}, tid=tid)
        self.assertEqual(self.task(tid)["effect"], "118분↓")  # 120-2 recomputed
        self.save({"title": "A2", "before_val": "120", "after_val": "1", "unit": "분/일"}, tid=tid)
        self.assertEqual(self.task(tid)["effect"], "119분↓")

    def test_complete_sets_100_and_done_date_then_clears(self):
        tid = self.save({"title": "A", "status": "진행중"}, {"upsert": [{"week": "2026-W44", "progress": 40}]})["id"]
        res = self.save({"title": "A", "status": "완료"}, tid=tid)
        t = self.task(tid)
        self.assertEqual((t["progress"], t["progress_week"], t["done_date"]), (100, "2026-W45", "2026-11-04"))
        self.assertTrue(res["notices"])
        self.save({"title": "A", "status": "진행중"}, tid=tid)
        self.assertIsNone(self.task(tid)["done_date"])

    def test_complete_with_future_log_marks_latest_week(self):
        tid = self.save({"title": "A", "status": "진행중"}, {"upsert": [{"week": "2026-W46", "progress": 90}]})["id"]
        self.save({"title": "A", "status": "완료"}, tid=tid)
        t = self.task(tid)
        self.assertEqual((t["progress"], t["progress_week"]), (100, "2026-W46"))

    def test_done_without_date_stays_unknown(self):
        tid = self.save({"title": "A", "status": "완료"})["id"]
        self.conn.execute("UPDATE tasks SET done_date=NULL WHERE id=?", (tid,))  # like imported 완료 tasks
        self.save({"title": "A2", "status": "완료"}, tid=tid)
        self.assertIsNone(self.task(tid)["done_date"])

    def test_waiting_becomes_in_progress_only_with_new_log(self):
        tid = self.save({"title": "A"}, {"upsert": [{"week": "2026-W45", "progress": 0}]})["id"]
        self.assertEqual(self.task(tid)["status"], "대기")
        self.save({"title": "A", "status": "대기"}, {"upsert": [{"week": "2026-W45", "progress": 10}]}, tid=tid)
        self.assertEqual(self.task(tid)["status"], "진행중")
        self.conn.execute("UPDATE tasks SET status='대기' WHERE id=?", (tid,))
        self.save({"title": "B", "status": "대기"}, tid=tid)  # no log in this save
        self.assertEqual(self.task(tid)["status"], "대기")

    def test_same_week_upsert(self):
        tid = self.save({"title": "A"}, {"upsert": [{"week": "2026-W45", "progress": 10, "content": "x"}]})["id"]
        self.save({"title": "A"}, {"upsert": [{"week": "2026-W45", "progress": 30, "content": "y"}]}, tid=tid)
        logs = logic.task_logs(self.conn, tid)
        self.assertEqual([(l["progress"], l["content"]) for l in logs], [(30, "y")])

    def test_validation(self):
        with self.assertRaises(logic.ValidationError) as e:
            self.save({"title": " ", "start_date": "2026-12-01", "due_date": "2026-11-01"},
                      {"upsert": [{"week": "2026-W45", "progress": 120}]})
        self.assertEqual(set(e.exception.errors), {"title", "due_date", "log0.progress"})

    def test_delay_and_issue(self):
        tid = self.save({"title": "A", "due_date": "2026-11-03"},
                        {"upsert": [{"week": "2026-W45", "progress": 10, "issue": "자료 미수령"}]})["id"]
        t = self.task(tid)
        self.assertTrue(t["delayed"] and t["has_issue"])
        self.save({"title": "A", "due_date": "2026-11-03", "status": "완료"}, tid=tid)
        t = self.task(tid)
        self.assertFalse(t["delayed"] or t["has_issue"])

    def test_cascade_delete(self):
        tid = self.save({"title": "A"}, {"upsert": [{"week": "2026-W45", "progress": 10}]})["id"]
        self.assertEqual(logic.delete_impact_company(self.conn, self.c), {"tasks": 1, "logs": 1, "notes": 0})
        with self.conn:
            self.conn.execute("DELETE FROM companies WHERE id=?", (self.c,))
        self.assertEqual(db.row_counts(self.conn)["progress_logs"], 0)

    def test_backup(self):
        res = db.backup(self.conn, Path(self.tmp.name) / "bk", keep=2)
        self.assertTrue(res["ok"], res)
        db.backup(self.conn, Path(self.tmp.name) / "bk", keep=2)
        db.backup(self.conn, Path(self.tmp.name) / "bk", keep=2)
        self.assertEqual(len(list((Path(self.tmp.name) / "bk").glob("*.db"))), 2)


# Cells where the original 45W sheet itself disagrees with the S26 source file (see test output).
KNOWN_45W_DIFFS = {
    "H21",  # 개선후: report says 2, S26 says 1
    "C50",  # 금주 과제별 내용: report text was edited by hand vs S26 단계 내용
    "C52",  # 방문일: report says 11/10 (목); 2026-11-10 is a Tuesday
}


class ReportTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.conn = fresh_db(Path(self.tmp.name))
        self.company_a = cid(self.conn, "협력사A")
        self.company_b = cid(self.conn, "협력사B")

    def tearDown(self):
        self.conn.close()
        self.tmp.cleanup()

    def note_from_original_45w(self):
        ws = openpyxl.load_workbook(config.ROOT / importer.COMPANY_A_REPORT).worksheets[0]
        logic.save_note(self.conn, self.company_a, 2026, 45, {
            "report_date": "2026-11-03", "summary": ws["B42"].value, "common": ws["C49"].value,
            "next_visit_date": "2026-11-10", "next_visit_time": "13:00∼15:00", "next_plan": ws["C54"].value})

    def test_company_a_45w_matches_original(self):
        self.note_from_original_45w()
        path, _ = report.template_path(self.company_a)
        wb = openpyxl.load_workbook(path)
        # data_only: compare against the values Excel computed for the original's formulas
        original = openpyxl.load_workbook(config.ROOT / importer.COMPANY_A_REPORT, data_only=True).worksheets[0]
        ws = wb.copy_worksheet(wb["45주"])
        company = self.conn.execute("SELECT * FROM companies WHERE id=?", (self.company_a,)).fetchone()
        warnings = []
        report.fill_sheet(ws, self.conn, company, 2026, 45, False, warnings)
        self.assertEqual(warnings, [])
        diffs, compared = {}, 0
        for row in original.iter_rows(min_row=1, max_row=original.max_row, min_col=2, max_col=10):
            for o in row:
                g = ws[o.coordinate]
                compared += 1
                ov, gv = o.value, g.value
                if isinstance(ov, float) and isinstance(gv, (int, float)):
                    same = abs(ov - gv) < 1e-9
                else:
                    same = ov == gv
                if not same:
                    diffs[o.coordinate] = (ov, gv)
        print(f"\n[45W 비교] 셀 {compared}개 비교, 불일치 {len(diffs)}개")
        for k, (ov, gv) in sorted(diffs.items(), key=lambda x: (int(x[0][1:]), x[0][0])):
            print(f"  {k}: 원본={str(ov)[:60]!r} / 생성={str(gv)[:60]!r}")
        self.assertEqual(set(diffs) - KNOWN_45W_DIFFS, set())
        # fonts: completed tasks blue, others not
        for r in range(21, 39):
            done = original.cell(r, 3).font.color is not None and original.cell(r, 3).font.color.rgb == report.BLUE
            got = ws.cell(r, 3).font.color is not None and ws.cell(r, 3).font.color.rgb == report.BLUE
            self.assertEqual(done, got, f"row {r} color")

    def test_company_a_cumulative_w46(self):
        res = report.build(self.conn, self.company_a, 2026, 46)
        self.assertEqual(res.sheets, ["46주", "45주", "44주", "43주"])
        self.assertTrue(res.filename.startswith("46W_협력사A_Feedback_Report_"))
        ws = openpyxl.load_workbook(io.BytesIO(res.data)).worksheets[0]
        self.assertEqual(ws["B2"].value, "'26년 46주차 컨설팅 Feedback Report")
        self.assertEqual((ws["C12"].value, ws["C15"].value), (18, 6))
        self.assertIn("46주 주간 메모가 없어", res.warnings[0])
        # original 45주 untouched
        ws45 = openpyxl.load_workbook(io.BytesIO(res.data))["45주"]
        self.assertTrue(ws45["C50"].value.startswith("1.데일리 브리핑 자동화 : 사내 운영중, 보안강화 중"))

    def test_company_a_week_lines_from_logs(self):
        tid = self.conn.execute("SELECT id FROM tasks WHERE company_id=? AND no=2", (self.company_a,)).fetchone()[0]
        logic.save_task(self.conn, {"task": {"title": "방문 예약 시스템", "status": "진행중", "category": "지원"},
                                    "logs": {"upsert": [{"week": "2026-W46", "progress": 90, "content": "다국어 적용"}]}},
                        task_id=tid)
        logic.save_note(self.conn, self.company_a, 2026, 46, {"report_date": "2026-11-10", "common": "[공통] 점검",
                                                          "next_visit_date": "2026-11-17", "next_visit_time": "13:00∼15:00",
                                                          "next_plan": " - 지원"})
        res = report.build(self.conn, self.company_a, 2026, 46)
        self.assertEqual(res.filename, "46W_협력사A_Feedback_Report_261110.xlsx")
        ws = openpyxl.load_workbook(io.BytesIO(res.data)).worksheets[0]
        self.assertEqual(ws["B49"].value, "금주 내용\n[46W,11/10] ")
        self.assertEqual(ws["C50"].value, "1.방문 예약 시스템 : 다국어 적용")
        self.assertEqual(ws["B51"].value, "차주 계획\n[47W,11/17]")
        self.assertEqual(ws["C52"].value, " - 11/17 (화) 13:00∼15:00")

    def test_company_b_w45_category_kpi(self):
        res = report.build(self.conn, self.company_b, 2026, 45)
        self.assertEqual(res.sheets, ["45주", "44주", "43주", "과제수"])
        ws = openpyxl.load_workbook(io.BytesIO(res.data)).worksheets[0]
        m = next(r for r in range(1, 80) if str(ws.cell(r, 2).value).startswith("□ KPI"))
        r = next(r for r in range(m, m + 5) if ws.cell(r, 2).value == "구분") + 1
        rows = {}
        while ws.cell(r, 2).value:
            rows[ws.cell(r, 2).value] = ws.cell(r, 3).value
            r += 1
        self.assertEqual(sum(rows.values()), 83)
        self.assertEqual(rows.get("제품개발3팀"), 1)  # missing from the 44W KPI table: appended
        self.assertEqual(rows.get("글로벌 제조혁신팀"), 10)  # matched despite the space
        # summary section still below KPI after the inserted row
        self.assertTrue(any(str(ws.cell(x, 2).value).startswith("□ 금주 컨설팅 요약") for x in range(r, r + 5)))

    def test_new_company_uses_base_template(self):
        c = logic.save_company(self.conn, {"name": "신규사", "field": "AX", "kpi_mode": "task"})
        for i, cat in enumerate(["A", "B", "C", "D", "E", "F", "G", "H", "I"]):
            logic.save_task(self.conn, {"task": {"title": f"t{i}", "category": cat}}, company_id=c)
        y, w = date.today().isocalendar()[:2]
        res = report.build(self.conn, c, y, w)
        self.assertEqual(res.sheets, [f"{w}주"])
        ws = openpyxl.load_workbook(io.BytesIO(res.data)).worksheets[0]
        kpi = next(r for r in range(1, 80) if str(ws.cell(r, 2).value).startswith("□ KPI"))
        heads = [ws.cell(r, cc).value for r in range(1, kpi) if ws.cell(r, 2).value == "구분"
                 for cc in range(4, 11)]
        self.assertEqual([h for h in heads if h], list("ABCDEFGHI"))
        self.assertEqual(ws["C12"].value, 9)


class ProgressFormTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.conn = fresh_db(Path(self.tmp.name))
        self.company_a = cid(self.conn, "협력사A")
        self.company_b = cid(self.conn, "협력사B")

    def tearDown(self):
        self.conn.close()
        self.tmp.cleanup()

    def form(self, company, week=(2026, 46)):
        data, name = progress_io.build_form(self.conn, company, *week)
        return openpyxl.load_workbook(io.BytesIO(data)), name

    def upload(self, wb, company, apply=False):
        buf = io.BytesIO()
        wb.save(buf)
        fn = progress_io.apply_upload if apply else progress_io.parse_upload
        return fn(self.conn, company, buf.getvalue())

    def row_of(self, ws, no):
        return next(r for r in range(progress_io.HEADER_ROW + 1, ws.max_row + 1) if ws.cell(r, 1).value == no)

    def test_form_lists_tasks_and_prefills_week(self):
        wb, name = self.form(self.company_a, (2026, 45))
        ws = wb[progress_io.SHEET]
        self.assertEqual(name, "진행사항_협력사A_2026-W45.xlsx")
        self.assertEqual(ws["B2"].value, "2026-W45")
        self.assertEqual(ws.max_row - progress_io.HEADER_ROW, 18)
        r = self.row_of(ws, 2)
        self.assertEqual((ws.cell(r, 2).value, ws.cell(r, 6).value), ("방문 예약 시스템", 85))
        self.assertEqual(wb[progress_io.META].sheet_state, "hidden")

    def test_untouched_form_changes_nothing(self):
        wb, _ = self.form(self.company_a, (2026, 45))
        res = self.upload(wb, self.company_a)
        self.assertEqual((res["changes"], res["errors"], res["unchanged"]), ([], [], 18))

    def test_preview_then_apply(self):
        wb, _ = self.form(self.company_b)
        ws = wb[progress_io.SHEET]
        r = self.row_of(ws, 1)
        ws.cell(r, 6).value, ws.cell(r, 7).value, ws.cell(r, 8).value = 30, "MRP 인터페이스 개발", "ERP 권한 대기"
        res = self.upload(wb, self.company_b)
        self.assertEqual(len(res["changes"]), 1)
        self.assertEqual(res["changes"][0]["kind"], "new")
        self.assertEqual(db.row_counts(self.conn)["progress_logs"], 18)  # preview wrote nothing
        res = self.upload(wb, self.company_b, apply=True)
        t = logic.company_tasks(self.conn, self.company_b)[0]
        self.assertEqual((t["progress"], t["progress_week"], t["status"], t["issue"]),
                         (30, "2026-W46", "진행중", "ERP 권한 대기"))  # 대기 -> 진행중 rule applied
        self.assertTrue(res["notices"])
        again = self.upload(wb, self.company_b)
        self.assertEqual((again["changes"], again["unchanged"]), ([], 1))

    def test_errors_block_apply(self):
        wb, _ = self.form(self.company_b)
        ws = wb[progress_io.SHEET]
        ws.cell(self.row_of(ws, 1), 6).value = 150
        ws.cell(self.row_of(ws, 2), 2).value = "다른 과제명"
        ws.cell(self.row_of(ws, 2), 6).value = 10
        ws.cell(self.row_of(ws, 3), 7).value = "진척률 없이 내용만"
        res = self.upload(wb, self.company_b)
        self.assertEqual(len(res["errors"]), 3)
        with self.assertRaises(progress_io.FormError):
            self.upload(wb, self.company_b, apply=True)
        self.assertEqual(db.row_counts(self.conn)["progress_logs"], 18)

    def test_other_company_or_foreign_file_rejected(self):
        wb, _ = self.form(self.company_a)
        with self.assertRaises(progress_io.FormError) as e:
            self.upload(wb, self.company_b)
        self.assertIn("다른 협력사", str(e.exception))
        with self.assertRaises(progress_io.FormError):
            self.upload(openpyxl.load_workbook(config.ROOT / importer.COMPANY_A_TASKS), self.company_a)

    def test_week_cell_can_be_changed(self):
        wb, _ = self.form(self.company_a, (2026, 45))
        ws = wb[progress_io.SHEET]
        ws["B2"] = "2026-W47"
        res = self.upload(wb, self.company_a)
        self.assertEqual((res["week"], len(res["changes"])), ("2026-W47", 18))
        self.assertTrue(all(c["kind"] == "new" for c in res["changes"]))
        ws["B2"] = "47주"
        with self.assertRaises(progress_io.FormError):
            self.upload(wb, self.company_a)


if __name__ == "__main__":
    unittest.main()
