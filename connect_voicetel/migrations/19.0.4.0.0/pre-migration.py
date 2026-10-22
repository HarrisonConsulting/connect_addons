# -*- coding: utf-8 -*-
"""Clean a database coming from the retired shim, the pre-retirement
VoiceTel module or connect_voiceml, before 19.0.4.0.0's models load.

See connect_voicetel/hooks.py for each step. Raw SQL, guarded,
idempotent, archive-before-drop, rename-not-drop.
"""
from odoo.addons.connect_voicetel import hooks


def migrate(cr, version):
    hooks.pre_migrate(cr, version)
