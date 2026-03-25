# -*- coding: utf-8 -*-
"""Upgrade default summary_prompt to use template placeholders."""
import logging

_logger = logging.getLogger(__name__)

OLD_DEFAULT = 'Summarise this phone call'

NEW_DEFAULT = """{number_name} is {number_description}.
Your task is to produce a comprehensive summary of the {direction} call \
from {caller_name} ({caller_number}) to {called_name} ({called_number}) \
with the following transcription:
```
{transcript}
```"""


def migrate(cr, version):
    cr.execute(
        "UPDATE connect_settings SET summary_prompt = %s WHERE summary_prompt = %s",
        (NEW_DEFAULT, OLD_DEFAULT),
    )
    updated = cr.rowcount
    _logger.info('Updated %d settings record(s) from old default summary prompt to template', updated)
