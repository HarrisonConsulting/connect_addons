# -*- coding: utf-8 -*-

import logging

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

    @api.model
    def _tz_get(self):
        return [(tz, tz) for tz in sorted(pytz.all_timezones_set)]
