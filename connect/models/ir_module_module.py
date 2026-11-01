import logging

from odoo import api, fields, models
from odoo.exceptions import ValidationError

from .license import ODUIST_MODULES

_logger = logging.getLogger(__name__)


class IrModuleModule(models.Model):
    _inherit = "ir.module.module"

    oduist_license_status = fields.Char(
        string="License Status",
        compute="_compute_oduist_license_status",
    )

    oduist_module_purchased = fields.Boolean(
        string="Module Purchased",
        compute="_compute_oduist_license_status",
    )

    oduist_module_price = fields.Char(
        string="Module Price",
        readonly=True,
    )

    oduist_module_show_price = fields.Char(
        string="Price",
        compute="_compute_oduist_license_status",
    )

    def _compute_oduist_license_status(self):
        License = self.env["oduist.license"]
        for rec in self:
            status = License.get_license_status(rec.name)
            rec.oduist_license_status = ""
            rec.oduist_module_purchased = False
            rec.oduist_module_show_price = ""
            if status["status"] == "private_integration":
                rec.oduist_license_status = "Private integration"
            if status["status"] == "production":
                order_id = status.get("order_id", "")
                rec.oduist_license_status = f"Purchase Order: {order_id}"
                rec.oduist_module_purchased = True
                rec.oduist_module_show_price = ""
            elif status["status"] == "trial_active":
                rec.oduist_license_status = f"Trial: {status['days_left']} days left"
                rec.oduist_module_purchased = False
                rec.oduist_module_show_price = rec.oduist_module_price or ""
            elif status["status"] == "trial_expired":
                rec.oduist_license_status = "Trial Expired"
                rec.oduist_module_purchased = False
                rec.oduist_module_show_price = rec.oduist_module_price or ""
            elif status["status"] == "demo":
                rec.oduist_license_status = "Demo"
                rec.oduist_module_purchased = True
                rec.oduist_module_show_price = ""

    def buy_oduist_license(self):
        """Buy license for this specific module."""
        self.ensure_one()
        if self.name not in ODUIST_MODULES:
            raise ValidationError("Only Oduist catalog products can be purchased here.")
        License = self.env["oduist.license"]
        token = License.sudo().get_param("license_token")
        if not token:
            License.update_license_status()
        return License.buy_licenses([self.name])
