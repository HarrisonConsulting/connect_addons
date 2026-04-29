import logging

from . import controllers
from . import models
from . import wizard

_logger = logging.getLogger(__name__)


def post_init_hook(env):
    """Seed connect.audio.is_reachable after install/upgrade.

    XML data loads go through the ORM — the referrer mixin catches them —
    but only once their module finishes loading. Calling the BFS at the end
    of install/upgrade means fresh databases and upgraded instances both
    land with is_reachable correctly populated across every audio + reference,
    rather than waiting for the next routing write to trigger it.
    """
    try:
        env['connect.audio']._refresh_reachability()
    except Exception as e:
        _logger.warning('post_init reachability refresh failed: %s', e)
