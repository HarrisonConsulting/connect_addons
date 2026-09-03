# -*- coding: utf-8 -*-
"""Audio-layer wiring for extensions.

An extension speaks system messages and forms an edge in the audio
reachability graph through its polymorphic destination pointer.
"""

from odoo import models


class Exten(models.Model):
    _name = 'connect.exten'
    _inherit = ['connect.exten', 'connect.tts.mixin',
                'connect.audio.referrer.mixin']

    # Polymorphic destination pointer — model + res_id together define the edge.
    _audio_reachability_fields = ('model', 'res_id')
