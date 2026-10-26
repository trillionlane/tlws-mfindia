"""Identical disposable-fixture rows/SQL; batch only the client messages."""

from datetime import date, timedelta

NAV_INSERT_SQL = "INSERT INTO mf.nav_history(amfi_scheme_code,nav_date,nav) VALUES(%s,%s,%s)"


def fixture_nav_rows(code):
    day = date(2024, 1, 1)
    rows = []
    for i in range(270):
        while day.weekday() >= 5:
            day += timedelta(days=1)
        rows.append((code, day, '100' if i % 2 == 0 else '110'))
        day += timedelta(days=1)
    return rows


def seed_fixture_nav(conn, code):
    # A cursor context only closes the cursor; the caller still owns its SAME
    # transaction. Each row remains the original single-row INSERT, not COPY.
    with conn.cursor() as cursor:
        cursor.executemany(NAV_INSERT_SQL, fixture_nav_rows(code))
