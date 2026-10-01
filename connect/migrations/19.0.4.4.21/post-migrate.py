"""Give each stored provider selection a shared voice system.

Selections stay on connect.user. Two users on the same adapter share one
connect.tool. Provider modules load after this script, so an adapter key
they own is inserted directly when it is not a selection value yet.
"""

import logging

from odoo import SUPERUSER_ID, api

_logger = logging.getLogger(__name__)


def migrate(cr, version):
    if not version:
        return
    env = api.Environment(cr, SUPERUSER_ID, {})
    users = env['connect.user'].sudo().with_context(active_test=False).search([
        '|',
        ('originate_provider', '!=', False),
        ('message_provider', '!=', False),
    ])
    tools = {}
    for user in users:
        voice = user.originate_provider or False
        message = user.message_provider or False
        if voice and message and voice == message:
            _assign(env, user, _tool_for(env, tools, voice), 'both', 10)
            continue
        if voice:
            _assign(env, user, _tool_for(env, tools, voice), 'voice', 10)
        if message:
            _assign(
                env, user, _tool_for(env, tools, message), 'message',
                20 if voice else 10,
            )


def _tool_for(env, tools, key):
    if key in tools:
        return tools[key]
    Tool = env['connect.tool'].sudo().with_context(active_test=False)
    tool = Tool.search([('adapter', '=', key)], limit=1)
    if not tool:
        label = _adapter_label(env, key)
        if key in set(Tool._fields['adapter'].get_values(env)):
            tool = Tool.create({'name': label, 'adapter': key})
        else:
            # The module that adds this adapter key has not loaded yet.
            env.flush_all()
            env.cr.execute(
                """
                INSERT INTO connect_tool
                    (name, adapter, active, company_id,
                     create_uid, write_uid, create_date, write_date)
                VALUES (%s, %s, TRUE, %s, %s, %s,
                        NOW() AT TIME ZONE 'UTC', NOW() AT TIME ZONE 'UTC')
                RETURNING id
                """,
                (label, key, env.company.id or None, SUPERUSER_ID, SUPERUSER_ID),
            )
            tool = Tool.browse(env.cr.fetchone()[0])
        _logger.info('voice system %s for adapter %s', label, key)
    tools[key] = tool
    return tool


def _adapter_label(env, key):
    User = env['connect.user']
    for field_name in ('originate_provider', 'message_provider'):
        for value, label in User._fields[field_name]._description_selection(env):
            if value == key and label:
                return label
    env.cr.execute(
        """
        SELECT s.name->>'en_US'
          FROM ir_model_fields_selection s
          JOIN ir_model_fields f ON f.id = s.field_id
         WHERE f.model = 'connect.user'
           AND f.name IN ('originate_provider', 'message_provider')
           AND s.value = %s
         LIMIT 1
        """,
        (key,),
    )
    row = env.cr.fetchone()
    if row and row[0]:
        return row[0]
    return key


def _assign(env, user, tool, use, sequence):
    Assignment = env['connect.user.tool'].sudo()
    if Assignment.search_count([
        ('user_id', '=', user.id),
        ('tool_id', '=', tool.id),
    ]):
        return
    # The mirror would clear a key whose module is not loaded yet.
    Assignment.with_context(connect_skip_provider_mirror=True).create({
        'user_id': user.id,
        'tool_id': tool.id,
        'role': 'active',
        'use': use,
        'sequence': sequence,
    })
    _logger.info(
        'assigned voice system %s to connect.user %s as %s',
        tool.id, user.id, use,
    )
