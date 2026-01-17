import logging
from odoo import models, fields, api
from odoo.exceptions import ValidationError
from odoo.models import Constraint
logger = logging.getLogger(__name__)


class CallSource(models.Model):
    _inherit = 'utm.source'

    phone = fields.Char()

    _phone_uniq = Constraint('UNIQUE(phone)', 'This phone number is already used!')

