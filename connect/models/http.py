from odoo import models


class IrHttp(models.AbstractModel):
    _inherit = 'ir.http'

    def session_info(self):
        result = super().session_info()
        result['connect_enable_whatsapp'] = bool(
            self.env['connect.settings'].sudo().get_param('enable_whatsapp', True)
        )
        return result
