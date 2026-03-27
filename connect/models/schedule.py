# -*- coding: utf-8 -*-

import logging
from datetime import datetime

import pytz
from odoo import fields, models, api

logger = logging.getLogger(__name__)


class Schedule(models.Model):
    _name = 'connect.schedule'
    _description = 'Business Hours Schedule'
    _order = 'name'

    name = fields.Char(required=True, help="Schedule name, e.g. 'Main Office Hours'")
    active = fields.Boolean(default=True, help="Whether this schedule is active")
    timezone = fields.Selection(
        '_tz_get', string='Timezone', required=True, default='US/Eastern',
        help="Timezone for this schedule")
    line_ids = fields.One2many(
        'connect.schedule.line', 'schedule_id', string='Schedule Lines',
        help="Daily schedule rules")
    holiday_ids = fields.One2many(
        'connect.schedule.holiday', 'schedule_id', string='Holidays',
        help="Holiday dates when office is closed")

    @api.model
    def _tz_get(self):
        return [(tz, tz) for tz in sorted(pytz.all_timezones_set)]

    def is_open(self):
        """Check if schedule is currently open, considering day-of-week, time, and holidays."""
        self.ensure_one()
        tz = pytz.timezone(self.timezone or 'UTC')
        now = datetime.now(tz)
        today = now.date()

        # Check exact-date holidays
        for holiday in self.holiday_ids:
            if holiday.date == today:
                return False
            # Check recurring holidays (match month + day)
            if holiday.recurring and holiday.date.month == today.month and holiday.date.day == today.day:
                return False

        # Find schedule lines for current day of week (Monday=0 ... Sunday=6)
        day_str = str(today.weekday())
        day_lines = self.line_ids.filtered(lambda l: l.day_of_week == day_str)

        # No lines for today means closed
        if not day_lines:
            return False

        # If any line marks the day as closed, return False
        if any(line.is_closed for line in day_lines):
            return False

        # Check if current time falls within any line's hour range
        current_hour = now.hour + now.minute / 60.0

        for line in day_lines:
            if line.start_hour <= line.end_hour:
                # Normal range (e.g. 9:00-17:00)
                if line.start_hour <= current_hour < line.end_hour:
                    return True
            else:
                # Overnight range (e.g. 22:00-06:00)
                if current_hour >= line.start_hour or current_hour < line.end_hour:
                    return True

        return False


class ScheduleLine(models.Model):
    _name = 'connect.schedule.line'
    _description = 'Schedule Line'
    _order = 'day_of_week, start_hour'

    schedule_id = fields.Many2one(
        'connect.schedule', required=True, ondelete='cascade',
        help="Parent schedule")
    day_of_week = fields.Selection([
        ('0', 'Monday'), ('1', 'Tuesday'), ('2', 'Wednesday'),
        ('3', 'Thursday'), ('4', 'Friday'), ('5', 'Saturday'), ('6', 'Sunday')
    ], required=True, help="Day of the week")
    start_hour = fields.Float(
        required=True, default=9.0,
        help="Opening hour (24h format, e.g. 9.0 for 9:00 AM)")
    end_hour = fields.Float(
        required=True, default=17.0,
        help="Closing hour (24h format, e.g. 17.0 for 5:00 PM)")
    is_closed = fields.Boolean(
        default=False,
        help="Check to mark this day as fully closed (overrides hours)")


class ScheduleHoliday(models.Model):
    _name = 'connect.schedule.holiday'
    _description = 'Schedule Holiday'
    _order = 'date'

    schedule_id = fields.Many2one(
        'connect.schedule', required=True, ondelete='cascade',
        help="Parent schedule")
    name = fields.Char(required=True, help="Holiday name, e.g. 'Christmas Day'")
    date = fields.Date(required=True, help="Holiday date")
    recurring = fields.Boolean(
        default=False,
        help="If checked, this holiday recurs annually (month and day matched)")
