"""KST time and ISO week helpers."""
from datetime import date, datetime, timedelta, timezone

# Korea has no DST; a fixed offset avoids needing tzdata on Windows.
KST = timezone(timedelta(hours=9), "KST")
WEEKDAYS = "월화수목금토일"


def now_kst() -> datetime:
    return datetime.now(KST)


def today_kst() -> date:
    return now_kst().date()


def week_of(d: date) -> tuple[int, int]:
    iso = d.isocalendar()
    return iso[0], iso[1]


def current_week() -> tuple[int, int]:
    return week_of(today_kst())


def week_monday(year: int, week: int) -> date:
    return date.fromisocalendar(year, week, 1)


def week_sunday(year: int, week: int) -> date:
    return date.fromisocalendar(year, week, 7)


def week_key(year: int, week: int) -> str:
    return f"{year}-W{week:02d}"


def parse_week_key(key: str) -> tuple[int, int]:
    """'2026-W45' -> (2026, 45). Raises ValueError on bad input."""
    year_s, week_s = key.strip().upper().split("-W")
    year, week = int(year_s), int(week_s)
    date.fromisocalendar(year, week, 1)  # validates the week exists
    return year, week


def next_week(year: int, week: int) -> tuple[int, int]:
    return week_of(week_monday(year, week) + timedelta(days=7))


def weeks_between(start: tuple[int, int], end: tuple[int, int]) -> list[tuple[int, int]]:
    """Inclusive list of ISO weeks from start to end."""
    out = []
    d = week_monday(*start)
    stop = week_monday(*end)
    while d <= stop:
        out.append(week_of(d))
        d += timedelta(days=7)
    return out


def md_with_weekday(d: date) -> str:
    """date -> '11/10 (화)'"""
    return f"{d.month}/{d.day} ({WEEKDAYS[d.weekday()]})"
