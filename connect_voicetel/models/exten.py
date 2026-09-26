# -*- coding: utf-8 -*-
import logging
from odoo import fields, models, api
from odoo.exceptions import ValidationError
from odoo.models import Constraint

from .voicetel_response import VoiceResponse

logger = logging.getLogger(__name__)


class VoicetelExten(models.Model):
    _name = 'connect.voicetel.exten'
    _description = 'VoiceTel Extension'
    _order = 'number'

    name = fields.Char(compute='_get_name', copy=False)
    number = fields.Char('Extension Number', required=True, copy=False)
    model = fields.Char('AppModel')
    model_friendly = fields.Char('Model', compute='_get_model_friendly', store=True, copy=False)
    res_id = fields.Integer()
    dst = fields.Reference(
        string='Destination',
        ondelete='cascade',
        required=False,
        selection=[
            ('connect.user', 'User'),
            ('connect.voicetel.callflow', 'Call Flow'),
            ('connect.voicetel.application', 'Application'),
        ],
        compute='_get_dst', inverse='_set_dst')
    dst_name = fields.Char(compute='_get_dst')

    _number_uniq = Constraint('UNIQUE(number)', 'This extension number is already defined!')

    @api.model
    def _dst_exten_field(self, dst):
        if dst._name == 'connect.user':
            return 'voicetel_exten'
        return 'exten' if 'exten' in dst._fields else None

    def _link_dst(self, dst, exten):
        if not dst:
            return
        field_name = self._dst_exten_field(dst)
        if field_name:
            dst[field_name] = exten

    def _get_name(self):
        for rec in self:
            try:
                rec.name = '{} <{}>'.format(rec.number, rec.dst.name if rec.dst else '')
            except Exception:
                logger.exception('Exten name error:')
                rec.name = 'See Odoo Error Log'

    @api.depends('model')
    def _get_model_friendly(self):
        for rec in self:
            try:
                rec.model_friendly = dict(
                    self.env[self._name]._fields['dst'].selection).get(rec.model)
            except Exception:
                logger.exception('Exten Model friendly error:')
                rec.model_friendly = ''

    @api.model_create_multi
    def create(self, vals_list):
        for vals in vals_list:
            self._check_number_available(vals.get('number'))
        res = super().create(vals_list)
        for record in res:
            if record.dst:
                self._link_dst(record.dst, record)
        return res

    def write(self, vals):
        moving = ('model' in vals) or ('res_id' in vals)
        previous = [(rec, rec._stored_dst()) for rec in self] if moving else []
        res = super().write(vals)
        for rec, dst in previous:
            if dst and dst != rec._stored_dst():
                self._link_dst(dst, False)
        for rec in self:
            if rec.dst:
                self._link_dst(rec.dst, rec)
        return res

    def _stored_dst(self):
        self.ensure_one()
        if not self.model or not self.res_id or self.model not in self.env:
            return None
        return self.env[self.model].browse(self.res_id).exists()

    @api.model
    def _check_number_available(self, number):
        if not number:
            return
        taken = self.search([('number', '=', number)], limit=1)
        if not taken:
            return
        raise ValidationError(
            'Extension {} already exists. Pick another number or edit that '
            'extension.'.format(number))

    def unlink(self):
        for rec in self:
            if rec.dst:
                self._link_dst(rec.dst, False)
        return super().unlink()

    def _get_dst(self):
        for rec in self:
            if rec.model and rec.model in self.env:
                try:
                    rec.dst = '%s,%s' % (rec.model, rec.res_id or 0)
                    rec.dst_name = self.env[rec.model]._description
                except ValueError:
                    logger.exception('Exten dst error:')
                    rec.dst = None
                    rec.dst_name = None
            else:
                rec.dst = None
                rec.dst_name = None

    def _set_dst(self):
        for rec in self:
            dst = rec.dst
            if dst:
                rec.write({'model': dst._name, 'res_id': dst.id})
                self._link_dst(dst, rec)
            else:
                rec.write({'model': False, 'res_id': False})

    @api.model
    def create_extension(self, rec, dst_model, current_exten=None):
        exten = current_exten
        if exten is None:
            exten = rec.exten if 'exten' in rec._fields else False
        return {
            'type': 'ir.actions.act_window',
            'res_model': self._name,
            'view_mode': 'form',
            'res_id': exten.id if exten else False,
            'target': 'new' if not exten else 'current',
            'context': {'default_dst': '{},{}'.format(dst_model, rec.id)},
        }

    def render(self, request=None, params=None):
        self.ensure_one()
        if not self.dst:
            response = VoiceResponse()
            response.say('Extension not configured!')
            return response.to_xml()
        params = dict(params or {})
        params['ExtenID'] = self.id
        params['ExtenNumber'] = self.number
        if self.dst._name == 'connect.user':
            return self.dst.render_voicetel(request=dict(request or {}), params=params)
        return self.dst.render(request=dict(request or {}), params=params)
