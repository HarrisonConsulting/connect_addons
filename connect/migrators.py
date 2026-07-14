# -*- coding: utf-8 -*-

import logging
from abc import ABC, abstractmethod
from dataclasses import dataclass, field

logger = logging.getLogger(__name__)


@dataclass
class MigrationResult:
    name: str
    created: list = field(default_factory=list)
    rebound: list = field(default_factory=list)
    skipped: list = field(default_factory=list)
    errors: list = field(default_factory=list)
    warnings: list = field(default_factory=list)


class Migrator(ABC):
    name = None

    def __init__(self, env):
        self.env = env

    @abstractmethod
    def run(self, dest_client, dry_run):
        """Reconcile Odoo records against the destination account.

        Must match by natural key (never by SID: SIDs are account-scoped) and
        perform zero writes when dry_run is True.
        """


class NumberMigrator(Migrator):
    name = 'numbers'

    def run(self, dest_client, dry_run):
        result = MigrationResult(name=self.name)
        dest_numbers = {
            k.phone_number: k for k in dest_client.incoming_phone_numbers.list()
        }
        records = self.env['connect.number'].search([
            ('sid', '!=', False), ('is_ignored', '=', False)])
        for rec in records:
            dest = dest_numbers.pop(rec.phone_number, None)
            try:
                if dest and dest.sid == rec.sid:
                    result.skipped.append(rec.phone_number)
                elif dest:
                    if not dry_run:
                        rec._adopt_sid(dest.sid, dest_client)
                    result.rebound.append(rec.phone_number)
                else:
                    if not dry_run:
                        created = dest_client.incoming_phone_numbers.create(
                            phone_number=rec.phone_number)
                        rec._adopt_sid(created.sid, dest_client)
                    result.created.append(rec.phone_number)
            except Exception as e:
                logger.exception('Number migration failed for %s:', rec.phone_number)
                message = str(e)
                if not dest:
                    # Twilio refuses to create a number owned by another
                    # account; Twilio-compatible providers (VoiceML) accept it.
                    message += (' — number could not be created on the target '
                                'account. Transfer it via the Twilio console/'
                                'support, then re-run.')
                result.errors.append((rec.phone_number, message))
        for phone_number in dest_numbers:
            result.warnings.append(
                '{} exists only on the target account and will be imported '
                'by SYNC after cutover.'.format(phone_number))
        return result


class DomainMigrator(Migrator):
    name = 'domains'

    def run(self, dest_client, dry_run):
        result = MigrationResult(name=self.name)
        dest_domains = {k.domain_name: k for k in dest_client.sip.domains.list()}
        records = self.env['connect.domain'].search([])
        for rec in records:
            dest = dest_domains.pop(rec.domain_name, None)
            if dest and dest.sid == rec.sid:
                result.skipped.append(rec.friendly_name)
            elif dest:
                result.rebound.append(rec.friendly_name)
            else:
                result.created.append(rec.friendly_name)
        for domain_name in dest_domains:
            result.warnings.append(
                '{} exists only on the target account and will be imported '
                'by SYNC after cutover.'.format(domain_name))
        if not dry_run and records:
            try:
                # sync() resolves create-vs-adopt-vs-update per record against
                # dest_client itself — same reconcile logic this preview
                # mirrors, not duplicated here. It raises on the first
                # unrecoverable per-domain error rather than isolating
                # failures (existing behavior, unchanged); a raised error here
                # means later domains in the batch were not attempted.
                self.env['connect.domain'].sync(client=dest_client)
            except Exception as e:
                logger.exception('Domain migration failed:')
                result.errors.append(('*', str(e)))
        return result


class TwimlMigrator(Migrator):
    name = 'twiml_apps'

    def run(self, dest_client, dry_run):
        result = MigrationResult(name=self.name)
        dest_apps = {k.friendly_name: k for k in dest_client.applications.list()}
        records = self.env['connect.twiml'].search([('sid', '!=', False)])
        for rec in records:
            dest = dest_apps.pop(rec.name, None)
            if dest and dest.sid == rec.sid:
                result.skipped.append(rec.name)
            elif dest:
                result.rebound.append(rec.name)
            else:
                result.created.append(rec.name)
        for friendly_name in dest_apps:
            result.warnings.append(
                '{} exists only on the target account and will be imported '
                'by SYNC after cutover.'.format(friendly_name))
        if not dry_run and records:
            try:
                # Same non-isolating-failure caveat as DomainMigrator: a
                # raised error here means later apps in the batch were not
                # attempted (existing connect.twiml.sync() behavior).
                self.env['connect.twiml'].sync(client=dest_client)
            except Exception as e:
                logger.exception('TwiML app migration failed:')
                result.errors.append(('*', str(e)))
        return result
