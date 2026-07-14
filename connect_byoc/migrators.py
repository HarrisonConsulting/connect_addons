# -*- coding: utf-8 -*-

import logging
from odoo.addons.connect.migrators import Migrator, MigrationResult

logger = logging.getLogger(__name__)


class BYOCMigrator(Migrator):
    name = 'byoc'
    title = 'BYOC Trunks'

    def run(self, dest_client, dry_run):
        result = MigrationResult(name=self.name)
        records = self.env['connect.byoc'].search([])
        if not records:
            return result
        try:
            # Twilio-compatible providers may not implement Voice v1 (see the
            # guard in connect.byoc.create_twilio_byoc) — a bare list() here
            # would otherwise take down every OTHER migrator's results too,
            # since the wizard runs one Migrator per resource type.
            dest_trunk_list = dest_client.voice.v1.byoc_trunks.list()
            dest_trunk_names = [t.friendly_name for t in dest_trunk_list]
            dest_trunks = {t.friendly_name: t for t in dest_trunk_list}
            dest_domain_sids = (
                {d.sid for d in dest_client.sip.domains.list()}
                if not dry_run else set())
        except Exception as e:
            logger.exception('BYOC migration could not list target trunks:')
            result.errors.append((
                '*', 'Could not list BYOC trunks on the target account: {}. '
                'The target may not implement the Twilio Voice v1 API '
                '(ConnectionPolicies/ByocTrunks) required for BYOC.'.format(e)))
            return result
        names = records.mapped('friendly_name')
        duplicates = {k for k in names if names.count(k) > 1}
        for friendly_name in sorted(duplicates):
            result.errors.append((
                friendly_name,
                'duplicate BYOC trunk name — the friendly name is the '
                'migration natural key; rename the trunks so each is unique '
                'and run again.'))
        dest_duplicates = {k for k in dest_trunk_names
                            if dest_trunk_names.count(k) > 1}
        for friendly_name in sorted(dest_duplicates):
            result.notices.append(
                '{} already exists more than once on the target account — '
                'adopted one of them arbitrarily, since Twilio does not '
                'require trunk names to be unique. Check the target '
                'account manually.'.format(friendly_name))
        for rec in records.filtered(
                lambda r: r.friendly_name not in duplicates):
            dest = dest_trunks.pop(rec.friendly_name, None)
            # Classify before create_twilio_byoc() rebinds rec.sid to the
            # target account; classification is presentation only — the real
            # run always performs the full idempotent reconcile so a retry
            # heals a previous partial failure.
            if dest and dest.sid == rec.sid:
                bucket = result.skipped
            elif dest:
                bucket = result.rebound
            else:
                bucket = result.created
            try:
                if dry_run:
                    if rec.sip_username:
                        result.notices.append(
                            'A new SIP password will be generated for {} if '
                            'its credential is missing on the target account '
                            '(the original is not recoverable).'.format(
                                rec.sip_username))
                else:
                    if rec.domain and rec.domain.sid not in dest_domain_sids:
                        result.errors.append((
                            rec.friendly_name,
                            'SIP domain {} was not migrated — resolve the '
                            'SIP Domains errors above and run again.'.format(
                                rec.domain_name)))
                        continue
                    rec.create_twilio_byoc(dest_client)
                    password = rec._reconcile_sip_credential(dest_client)
                    if password:
                        notice = (
                            'NEW password generated for {}, the original is '
                            'not recoverable — update your carrier: '
                            '{}'.format(rec.sip_username, password))
                        result.notices.append(notice)
                        # The wizard is a TransientModel and gets vacuumed —
                        # this is the one place this unrecoverable value is
                        # guaranteed to still be readable afterward.
                        logger.warning(
                            'BYOC migration: %s (record %s)', notice, rec.id)
                bucket.append(rec.friendly_name)
            except Exception as e:
                logger.exception(
                    'BYOC migration failed for %s:', rec.friendly_name)
                result.errors.append((rec.friendly_name, str(e)))
        for friendly_name in dest_trunks:
            result.warnings.append(
                '{} exists only on the target account and will be imported '
                'by SYNC after cutover.'.format(friendly_name))
        return result
