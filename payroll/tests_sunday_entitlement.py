"""
Sunday Entitlement Engine — test suite.

`SimpleTestCase` with `databases = []`: the engine is a pure function, so
these run without touching a database at all. That matters — it means the
whole suite runs in milliseconds and can be exercised on every change, rather
than being the kind of test nobody runs because it needs a fixture.

Covers the eighteen specified scenarios, plus two property tests that check
the engine against a brute-force calendar walk over tens of thousands of
random periods. The property tests are the ones that would catch a subtle
off-by-one in the date arithmetic; the scenarios pin the business rules.

    python manage.py test payroll.tests_sunday_entitlement
"""

import calendar
import datetime
import random

from django.test import SimpleTestCase

from payroll.services_sunday_entitlement import (
    SUNDAY, SundayPolicy, calculate_sunday_entitlement, weekly_off_dates,
)

D = datetime.date.fromisoformat


def iso(dates):
    return [d.isoformat() for d in dates]


class SundayCountingTests(SimpleTestCase):
    databases = []

    def test_four_sunday_period(self):
        r = calculate_sunday_entitlement('2026-02-01', '2026-02-28',
                                         date_of_joining='2025-01-10')
        self.assertEqual(r['total_sundays_in_period'], 4)
        self.assertEqual(r['eligible_sunday_count'], 4)
        self.assertEqual(r['basis'], 'Existing Employee')

    def test_five_sunday_period_is_not_hard_coded_to_four(self):
        r = calculate_sunday_entitlement('2026-08-01', '2026-08-31',
                                         date_of_joining='2025-01-10')
        self.assertEqual(r['total_sundays_in_period'], 5)
        self.assertEqual(iso(r['eligible_sunday_dates']),
                         ['2026-08-02', '2026-08-09', '2026-08-16',
                          '2026-08-23', '2026-08-30'])

    def test_existing_employee_eligibility_starts_the_day_before_the_period(self):
        r = calculate_sunday_entitlement('2026-08-01', '2026-08-31',
                                         date_of_joining='2020-01-01')
        self.assertEqual(r['sunday_eligibility_start_date'], D('2026-07-31'))


class NewJoinerTests(SimpleTestCase):
    databases = []

    def test_joins_before_the_first_sunday(self):
        r = calculate_sunday_entitlement('2026-08-01', '2026-08-31',
                                         date_of_joining='2026-08-01')
        self.assertEqual(r['eligible_sunday_count'], 5)

    def test_joins_mid_month(self):
        r = calculate_sunday_entitlement('2026-08-01', '2026-08-31',
                                         date_of_joining='2026-08-10')
        self.assertEqual(r['eligible_sunday_count'], 3)
        self.assertEqual(iso(r['eligible_sunday_dates']),
                         ['2026-08-16', '2026-08-23', '2026-08-30'])
        self.assertEqual(r['basis'], 'New Joiner')

    def test_joining_ON_a_sunday_does_not_count_that_sunday(self):
        """Strictly after, never on. The rule most likely to be got wrong."""
        r = calculate_sunday_entitlement('2026-08-01', '2026-08-31',
                                         date_of_joining='2026-08-16')
        self.assertEqual(r['eligible_sunday_count'], 2)
        self.assertNotIn(D('2026-08-16'), r['eligible_sunday_dates'])
        self.assertEqual(r['exclusion_reasons']['2026-08-16'],
                         'Falls on the date of joining')

    def test_joins_after_the_final_sunday(self):
        r = calculate_sunday_entitlement('2026-08-01', '2026-08-31',
                                         date_of_joining='2026-08-31')
        self.assertEqual(r['eligible_sunday_count'], 0)


class AnnualLeaveTests(SimpleTestCase):
    databases = []

    def test_returns_from_leave_before_a_sunday(self):
        r = calculate_sunday_entitlement(
            '2026-08-01', '2026-08-31', date_of_joining='2024-01-01',
            annual_leave_records=[{'start_date': '2026-08-01',
                                   'end_date': '2026-08-12',
                                   'actual_rejoining_date': '2026-08-13'}])
        self.assertEqual(iso(r['eligible_sunday_dates']),
                         ['2026-08-16', '2026-08-23', '2026-08-30'])
        self.assertEqual(r['basis'], 'Returned from Annual Leave')

    def test_rejoining_ON_a_sunday_does_not_count_that_sunday(self):
        r = calculate_sunday_entitlement(
            '2026-08-01', '2026-08-31', date_of_joining='2024-01-01',
            annual_leave_records=[{'start_date': '2026-08-01',
                                   'end_date': '2026-08-15',
                                   'actual_rejoining_date': '2026-08-16'}])
        self.assertEqual(r['eligible_sunday_count'], 2)
        self.assertTrue(r['exclusion_reasons']['2026-08-16']
                        .startswith('Falls on the rejoining date'))

    def test_rejoins_after_the_final_sunday(self):
        r = calculate_sunday_entitlement(
            '2026-08-01', '2026-08-31', date_of_joining='2024-01-01',
            annual_leave_records=[{'start_date': '2026-08-01',
                                   'end_date': '2026-08-30',
                                   'actual_rejoining_date': '2026-08-31'}])
        self.assertEqual(r['eligible_sunday_count'], 0)

    def test_missing_rejoining_date_is_inferred_and_labelled_as_such(self):
        r = calculate_sunday_entitlement(
            '2026-08-01', '2026-08-31', date_of_joining='2024-01-01',
            annual_leave_records=[{'start_date': '2026-08-01',
                                   'end_date': '2026-08-12'}])
        self.assertEqual(r['eligible_sunday_count'], 3)
        self.assertIn('inferred', r['exclusion_reasons']['2026-08-09'])


class TerminationTests(SimpleTestCase):
    databases = []

    def test_no_sundays_after_the_last_working_date(self):
        r = calculate_sunday_entitlement('2026-08-01', '2026-08-31',
                                         date_of_joining='2024-01-01',
                                         last_working_date='2026-08-20')
        self.assertEqual(iso(r['eligible_sunday_dates']),
                         ['2026-08-02', '2026-08-09', '2026-08-16'])
        self.assertEqual(r['exclusion_reasons']['2026-08-23'],
                         'After last working date')
        self.assertIn('Terminated', r['basis'])


class PeriodShapeTests(SimpleTestCase):
    databases = []

    def test_custom_payroll_cycle(self):
        r = calculate_sunday_entitlement('2026-07-21', '2026-08-20',
                                         date_of_joining='2024-01-01')
        self.assertEqual(r['total_sundays_in_period'], 4)
        self.assertEqual(r['all_sunday_dates'][0], D('2026-07-26'))

    def test_period_crossing_two_months(self):
        r = calculate_sunday_entitlement('2026-08-15', '2026-09-14',
                                         date_of_joining='2024-01-01')
        self.assertEqual(r['total_sundays_in_period'], 5)

    def test_period_crossing_year_end(self):
        r = calculate_sunday_entitlement('2025-12-21', '2026-01-20',
                                         date_of_joining='2024-01-01')
        self.assertEqual(r['total_sundays_in_period'], 5)
        self.assertEqual(r['all_sunday_dates'][0].year, 2025)
        self.assertEqual(r['all_sunday_dates'][-1].year, 2026)

    def test_leap_year_february(self):
        self.assertEqual(calculate_sunday_entitlement(
            '2024-02-01', '2024-02-29')['total_sundays_in_period'], 4)
        self.assertEqual(calculate_sunday_entitlement(
            '2032-02-01', '2032-02-29')['total_sundays_in_period'], 5)

    def test_single_day_period(self):
        self.assertEqual(calculate_sunday_entitlement(
            '2026-08-02', '2026-08-02')['total_sundays_in_period'], 1)
        self.assertEqual(calculate_sunday_entitlement(
            '2026-08-03', '2026-08-03')['total_sundays_in_period'], 0)


class MultipleEventTests(SimpleTestCase):
    databases = []

    def test_sundays_worked_before_leave_are_not_stripped(self):
        """Joins the 5th, leave 10th-23rd, back the 24th.

        The Sunday on the 9th was genuinely worked. A single latest-date
        cut-off would drop it; the timeline keeps it.
        """
        r = calculate_sunday_entitlement(
            '2026-08-01', '2026-08-31', date_of_joining='2026-08-05',
            annual_leave_records=[{'start_date': '2026-08-10',
                                   'end_date': '2026-08-23',
                                   'actual_rejoining_date': '2026-08-24'}])
        self.assertEqual(iso(r['eligible_sunday_dates']),
                         ['2026-08-09', '2026-08-30'])
        self.assertEqual(r['basis'], 'New Joiner + Annual Leave')
        self.assertEqual(r['exclusion_reasons']['2026-08-02'],
                         'Before date of joining')
        self.assertIn('annual leave', r['exclusion_reasons']['2026-08-16'])


class InvalidInputTests(SimpleTestCase):
    databases = []

    def test_period_ending_before_it_starts(self):
        with self.assertRaises(ValueError):
            calculate_sunday_entitlement('2026-08-31', '2026-08-01')

    def test_last_working_date_before_joining(self):
        with self.assertRaises(ValueError):
            calculate_sunday_entitlement('2026-08-01', '2026-08-31',
                                         date_of_joining='2026-08-20',
                                         last_working_date='2026-08-10')

    def test_leave_ending_before_it_starts(self):
        with self.assertRaises(ValueError):
            calculate_sunday_entitlement(
                '2026-08-01', '2026-08-31',
                annual_leave_records=[{'start_date': '2026-08-20',
                                       'end_date': '2026-08-10'}])

    def test_rejoining_before_the_leave_started(self):
        with self.assertRaises(ValueError):
            calculate_sunday_entitlement(
                '2026-08-01', '2026-08-31',
                annual_leave_records=[{'start_date': '2026-08-20',
                                       'end_date': '2026-08-25',
                                       'actual_rejoining_date': '2026-08-01'}])


class PolicyTests(SimpleTestCase):
    databases = []

    def test_weekly_off_can_move_to_another_day(self):
        r = calculate_sunday_entitlement('2026-08-01', '2026-08-31',
                                         policy=SundayPolicy(weekday=4))
        self.assertEqual(r['weekday_name'], 'Friday')
        self.assertEqual(r['total_sundays_in_period'], 4)

    def test_policy_can_count_the_effective_date_itself(self):
        r = calculate_sunday_entitlement('2026-08-01', '2026-08-31',
                                         date_of_joining='2026-08-16',
                                         policy=SundayPolicy(count_on_effective_date=True))
        self.assertEqual(r['eligible_sunday_count'], 3)


class PropertyTests(SimpleTestCase):
    """The tests most likely to catch a real defect."""
    databases = []

    def test_counting_matches_a_brute_force_calendar_walk(self):
        random.seed(11)
        for _ in range(5000):
            y, m = random.randint(2020, 2035), random.randint(1, 12)
            start = datetime.date(y, m, random.randint(1, calendar.monthrange(y, m)[1]))
            end = start + datetime.timedelta(days=random.randint(0, 400))
            brute = [start + datetime.timedelta(days=i)
                     for i in range((end - start).days + 1)
                     if (start + datetime.timedelta(days=i)).weekday() == SUNDAY]
            self.assertEqual(weekly_off_dates(start, end), brute,
                             f'mismatch for {start} to {end}')

    def test_eligible_plus_excluded_always_equals_total(self):
        random.seed(5)
        for _ in range(5000):
            y, m = random.randint(2024, 2030), random.randint(1, 12)
            start = datetime.date(y, m, 1)
            end = datetime.date(y, m, calendar.monthrange(y, m)[1])
            join = start + datetime.timedelta(days=random.randint(-400, 40))
            lwd = (start + datetime.timedelta(days=random.randint(0, 60))
                   if random.random() < 0.4 else None)
            if lwd and lwd < join:
                lwd = None
            ls = start + datetime.timedelta(days=random.randint(0, 25))
            le = ls + datetime.timedelta(days=random.randint(0, 20))
            leave = ([{'start_date': ls, 'end_date': le,
                       'actual_rejoining_date': le + datetime.timedelta(days=1)}]
                     if random.random() < 0.5 else [])
            r = calculate_sunday_entitlement(start, end, date_of_joining=join,
                                             annual_leave_records=leave,
                                             last_working_date=lwd)
            self.assertEqual(
                r['eligible_sunday_count'] + r['excluded_sunday_count'],
                r['total_sundays_in_period'])
            self.assertEqual(len(r['exclusion_reasons']), r['excluded_sunday_count'])
            self.assertFalse(set(r['eligible_sunday_dates'])
                             & set(r['excluded_sunday_dates']))
            self.assertTrue(all(d.weekday() == SUNDAY for d in r['all_sunday_dates']))
