# -*- coding: utf-8 -*-
"""Moments on a call when recording was paused or stopped.

The provider may or may not honour the request. The mark is written either
way, with the clock time and the milliseconds since the channel opened, so
a later pass can silence our copy of the audio for that span.
"""
from odoo import api, fields, models


class RecordingMark(models.Model):
    _name = 'connect.recording.mark'
    _description = 'Recording mark'
    _order = 'occurred_at, id'

    call = fields.Many2one('connect.call', ondelete='cascade', index=True)
    channel = fields.Many2one('connect.channel', ondelete='cascade', index=True)
    kind = fields.Selection(
        [
            ('start', 'Start'),
            ('pause', 'Pause'),
            ('resume', 'Resume'),
            ('stop', 'Stop'),
        ],
        required=True,
        index=True,
    )
    occurred_at = fields.Datetime(required=True, index=True)
    offset_ms = fields.Integer(
        help='Milliseconds after the channel opened. Realigned to the '
             'recording start when that timestamp is known.',
    )
    provider_applied = fields.Boolean(
        help='The provider API accepted this change. False means our copy '
             'of the audio still contains the span and must be silenced.',
    )
    provider_note = fields.Char()

    @api.model
    def spans_ms(self, marks, duration_ms=None):
        """Closed intervals that must not remain in the kept audio.

        A pause runs until the next resume or stop. A stop runs until the
        recording ends. ``duration_ms`` closes an open span; without it the
        span stays open and the caller treats the end as the end of the file.
        """
        spans = []
        open_at = None
        for mark in marks:
            if mark.kind == 'pause' and open_at is None:
                open_at = mark.offset_ms or 0
            elif mark.kind in ('resume', 'stop') and open_at is not None:
                end = mark.offset_ms or 0
                if end > open_at:
                    spans.append((open_at, end))
                open_at = None
            if mark.kind == 'stop':
                spans.append((mark.offset_ms or 0, duration_ms))
                open_at = None
        if open_at is not None:
            spans.append((open_at, duration_ms))
        return spans
