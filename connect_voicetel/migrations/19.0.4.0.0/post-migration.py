# -*- coding: utf-8 -*-
"""Log what the 19.0.4.0.0 upgrade kept and set.

Providers and SIP credentials are not restored from the archive tables:
the new module provisions a fresh domain and credentials, and the
archive is the source for the old-credential revoke ledger.
"""
from odoo.addons.connect_voicetel import hooks


def migrate(cr, version):
    hooks.post_migrate(cr, version)
