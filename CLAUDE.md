# co-company — 협력사 과제 관리 대시보드

AI 컨설팅 협력사별 과제(진척·개선효과·이슈)를 관리하고, 주간 Feedback Report 엑셀을 원본 양식 그대로 생성하는 로컬 웹앱.
설계·평가 기록(PLAN.md)과 원본 엑셀·DB 는 고객사 정보라 공개 저장소에 포함하지 않는다.

## 기술 스택
- Python 3.12 · FastAPI 0.141.1 · uvicorn 0.52.4 · Jinja2 3.1.6 · openpyxl 3.1.5 (`.venv`, `requirements.txt` 고정)
- SQLite 단일 파일 `data/app.db` (외래키 ON, 삭제는 CASCADE)
- 화면: 서버 렌더(Jinja) + 바닐라 JS 모달. 서버↔JS 는 JSON(`/api/...`)만.
- 아이콘: Lucide 0.460.0 SVG 를 `app/icons.py` 에 인라인(런타임 npm 불필요). 파비콘 아이콘: **clipboard-list**.
- 실행: `start.ps1` / `stop.ps1` (공통 로직 `server-common.ps1`, 포트·호스트는 `.env`, ASCII 전용).

## 구조
- `app/config.py` 설정(.env) · `app/db.py` 스키마·연결·백업 · `app/weeks.py` KST·ISO 주차
- `app/logic.py` 과제 규칙(상태·진척·효과·지연/이슈·통계) · `app/report.py` 엑셀 보고서
- `app/importer.py` 초기 엑셀 1회 가져오기 · `app/main.py` 라우트
- `data/templates/{company_id}.xlsx` 협력사별 원본 보고서(템플릿 겸 과거 주차 시트 보관), `_base.xlsx` 신규 협력사용
- `tests/` unittest (`.venv\Scripts\python -m unittest`)

## 코딩 규칙
- 사용자 노출 시각·날짜는 KST. `weeks.KST` 고정 오프셋(UTC+9, 한국은 DST 없음 — Windows 에 tzdata 가 없어 ZoneInfo 대신 사용).
- 진척률은 **진행 이력에서만** 입력. 과제의 현재 진척률 = 최신 주차 이력.
- 개선효과 자동 계산은 개선전·개선후·단위가 **바뀌었을 때만**(가져온 원본 효과 텍스트를 덮어쓰지 않기 위해).
- 정렬 키·필터 키는 `main.py` 의 화이트리스트만.
- 삭제는 항상 영향 건수를 보여 주는 확인 창을 거친다.

## Constraints
- 보고서 셀 위치를 상수로 박지 않는다. `□ 과제 추진현황`·`□ KPI 현황`·`□ 금주 컨설팅 요약`·`□ 주요 추진내용`·`금주 내용`·`차주 계획` 표지 문자열로 찾는다(협력사마다 행 위치가 다르다).
- 보고서에 들어가지 못한 값(템플릿에 없는 구분 등)은 조용히 버리지 않는다 — `warnings` 로 화면에 표시.
- 원본 엑셀(프로젝트 루트 4개)은 읽기 전용. 가져오기는 `python -m app.importer` 1회.
