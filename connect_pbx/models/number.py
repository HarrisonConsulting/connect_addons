# -*- coding: utf-8 -*-
"""Audio-layer wiring for phone numbers.

Inbound DIDs are the entry points the audio reachability BFS walks from.
"""

from odoo import models


class Number(models.Model):
    _name = 'connect.number'
    _inherit = ['connect.number', 'connect.audio.referrer.mixin']

    # Inbound DIDs are entry points for the reachability BFS. Any change to
    # the destination polymorphism flips what's reachable.
    _audio_reachability_fields = ('destination', 'user', 'callflow', 'twiml')
