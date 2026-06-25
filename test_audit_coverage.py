#!/usr/bin/env python3
import unittest
from datetime import datetime, timezone

import duckdb

import audit_coverage


def ms_for_date(date_expr: str) -> int:
    dt = datetime.fromisoformat(date_expr).replace(tzinfo=timezone.utc)
    return int(dt.timestamp() * 1000)


class AuditCoverageIntegrityTest(unittest.TestCase):
    def test_current_day_provisional_rows_are_reported_but_not_strict_failures(self):
        con = duckdb.connect(':memory:')
        try:
            con.execute('''
                CREATE TABLE daily_bars (
                    ticker VARCHAR,
                    timestamp BIGINT,
                    transactions BIGINT
                )
            ''')
            today_ms = con.execute("SELECT epoch_ms(current_date)::BIGINT").fetchone()[0]
            con.execute('INSERT INTO daily_bars VALUES (?, ?, NULL)', ['CURR', today_ms])

            metrics = audit_coverage.print_integrity_checks(con)
            issues = audit_coverage.strict_issues(
                con,
                coverage={},
                vti_count=1,
                integrity={
                    'daily_duplicate_utc_dates': 0,
                    'daily_current_provisional_rows': metrics['daily_current_provisional_rows'],
                    'daily_future_provisional_rows': metrics['daily_future_provisional_rows'],
                },
            )

            self.assertEqual(metrics['daily_current_provisional_rows'], 1)
            self.assertEqual(metrics['daily_future_provisional_rows'], 0)
            self.assertFalse(any('current/future provisional' in issue for issue in issues))
            self.assertFalse(any('future provisional' in issue for issue in issues))
        finally:
            con.close()

    def test_future_provisional_rows_are_strict_failures(self):
        con = duckdb.connect(':memory:')
        try:
            con.execute('''
                CREATE TABLE daily_bars (
                    ticker VARCHAR,
                    timestamp BIGINT,
                    transactions BIGINT
                )
            ''')
            tomorrow_ms = con.execute("SELECT epoch_ms(current_date + INTERVAL 1 DAY)::BIGINT").fetchone()[0]
            con.execute('INSERT INTO daily_bars VALUES (?, ?, NULL)', ['FUTR', tomorrow_ms])

            metrics = audit_coverage.print_integrity_checks(con)
            issues = audit_coverage.strict_issues(
                con,
                coverage={},
                vti_count=1,
                integrity={
                    'daily_duplicate_utc_dates': 0,
                    'daily_current_provisional_rows': metrics['daily_current_provisional_rows'],
                    'daily_future_provisional_rows': metrics['daily_future_provisional_rows'],
                },
            )

            self.assertEqual(metrics['daily_current_provisional_rows'], 0)
            self.assertEqual(metrics['daily_future_provisional_rows'], 1)
            self.assertTrue(any('future provisional hourly-derived rows' in issue for issue in issues))
        finally:
            con.close()


if __name__ == '__main__':
    unittest.main()
