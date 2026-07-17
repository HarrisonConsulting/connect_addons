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
    # Distinct from warnings: something the operator must act on before the
    # target account is usable (e.g. a newly generated, unrecoverable
    # secret) rather than routine "will be picked up by SYNC" noise. Render
    # separately and log it — this wizard is a TransientModel that gets
    # vacuumed, so a value shown only in `warnings` can be permanently lost.
    notices: list = field(default_factory=list)


class Migrator(ABC):
    name = None
    title = None

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
    title = 'Numbers'

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
    title = 'SIP Domains'

    def run(self, dest_client, dry_run):
        result = MigrationResult(name=self.name)
        dest_domains = {k.domain_name: k for k in dest_client.sip.domains.list()}
        records = self.env['connect.domain'].search([])
        for rec in records:
            dest = dest_domains.pop(rec.domain_name, None)
            try:
                if dest and dest.sid == rec.sid:
                    if not dry_run:
                        rec.update_twilio_domain(dest_client)
                        if rec.cred_list_sid and rec._should_reconcile_credentials():
                            rec._import_sip_credentials_from_twilio(
                                dest_client, rec.cred_list_sid)
                    result.skipped.append(rec.friendly_name)
                elif dest:
                    # Matched by domain_name but under a different sid —
                    # adopt it directly rather than going through sync()'s
                    # sid-based classification, which wouldn't recognize
                    # this as the same domain in the first place.
                    if not dry_run:
                        rec._import_existing_domain_by_name(dest_client)
                    result.rebound.append(rec.friendly_name)
                else:
                    if not dry_run:
                        try:
                            rec.create_twilio_sip_domain(dest_client)
                        except Exception as e:
                            if 'already exists' in str(e):
                                rec._import_existing_domain_by_name(dest_client)
                            else:
                                raise
                    result.created.append(rec.friendly_name)
            except Exception as e:
                # Per-item isolation: one bad domain no longer aborts every
                # domain after it in the batch (the previous implementation
                # delegated to sync(), which raises on the first
                # unrecoverable error and stops there).
                logger.exception('Domain migration failed for %s:', rec.friendly_name)
                result.errors.append((rec.friendly_name, str(e)))
        for domain_name in dest_domains:
            result.warnings.append(
                '{} exists only on the target account and will be imported '
                'by SYNC after cutover.'.format(domain_name))
        return result


class TwimlMigrator(Migrator):
    name = 'twiml_apps'
    title = 'TwiML Apps'

    def run(self, dest_client, dry_run):
        result = MigrationResult(name=self.name)
        dest_apps = {k.friendly_name: k for k in dest_client.applications.list()}
        records = self.env['connect.twiml'].search([('sid', '!=', False)])
        for rec in records:
            dest = dest_apps.pop(rec.name, None)
            try:
                if dest and dest.sid == rec.sid:
                    if not dry_run:
                        rec.update_twilio_app(dest_client)
                    result.skipped.append(rec.name)
                elif dest:
                    # update_twilio_app() only looks an app up by SID, and
                    # create_twilio_app() has no adopt-by-name fallback
                    # (Twilio doesn't enforce unique friendly_name on
                    # Applications) — calling either naively here would
                    # create a duplicate instead of adopting the one we
                    # already found. Adopt it directly.
                    if not dry_run:
                        rec._adopt_sid(dest.sid, dest_client)
                    result.rebound.append(rec.name)
                else:
                    if not dry_run:
                        rec.create_twilio_app(dest_client)
                    result.created.append(rec.name)
            except Exception as e:
                # Per-item isolation: one bad app no longer aborts every app
                # after it in the batch (the previous implementation
                # delegated to connect.twiml.sync(), which raises on the
                # first unrecoverable error and stops there).
                logger.exception('TwiML app migration failed for %s:', rec.name)
                result.errors.append((rec.name, str(e)))
        for friendly_name in dest_apps:
            result.warnings.append(
                '{} exists only on the target account and will be imported '
                'by SYNC after cutover.'.format(friendly_name))
        return result
