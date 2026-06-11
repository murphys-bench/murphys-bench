"""
Management command: reset_operational_data

The clean cutover wipe — for the day you switch from OSTicket to Murphy's Bench
for real and want the demo/test data gone, while keeping everything you've
configured.

DELETES (operational data):
  Clients (cascades to Contacts, Devices, Tickets, replies, locks, links,
  Work Orders, notes, items, work-performed, invoices), Mileage, Attachments
  (rows AND files on disk), Custom-field VALUES, email send/receive logs, the
  audit-log history, device-credential access logs, and all non-superuser users.

KEEPS (configuration + you):
  SiteSettings, Roles, Status definitions, Help Topics, SLA Plans, Repair Types
  & categories, Checklists & items, Canned Responses, Quick Labor items, Email
  Templates & Signatures, Dashboard Tiles, Custom-field DEFINITIONS, KB
  articles/categories, Org Credentials (+ their access log), blocked/suppressed
  senders, Tech Skills, system queues, and all superuser accounts.

SAFE BY DEFAULT: running with no flags is a DRY RUN — it only prints counts.
To actually delete, pass the exact confirmation phrase:

    python manage.py reset_operational_data --confirm "DELETE ALL OPERATIONAL DATA"

Everything runs inside a single transaction, so a failure rolls back cleanly.
NEVER use `manage.py flush` for this — that destroys configuration too.
"""
from django.core.management.base import BaseCommand
from django.db import transaction

CONFIRM_PHRASE = 'DELETE ALL OPERATIONAL DATA'


class Command(BaseCommand):
    help = 'Delete operational data (clients, tickets, work orders, etc.) while keeping configuration. Dry-run by default.'

    def add_arguments(self, parser):
        parser.add_argument(
            '--confirm', default='',
            help=f'Type "{CONFIRM_PHRASE}" exactly to actually delete. Omit for a dry run.',
        )
        parser.add_argument(
            '--keep-users', default='',
            help='Comma-separated usernames to keep in addition to all superusers.',
        )

    def handle(self, *args, **options):
        # Imported here so the module loads even if models move around.
        from auditlog.models import LogEntry
        from core.models import (
            Client, Contact, Device, Ticket, WorkOrder, Mileage, Attachment,
            CustomFieldValue, EmailSendLog, InboundEmailLog,
            DeviceCredentialAccessLog, User,
        )

        keep_users = {u.strip() for u in options['keep_users'].split(',') if u.strip()}
        users_to_delete = User.objects.filter(is_superuser=False).exclude(username__in=keep_users)

        # Snapshot counts up front (used for both dry-run and the post-run report).
        counts = {
            'Clients': Client.objects.count(),
            'Contacts': Contact.objects.count(),
            'Devices': Device.objects.count(),
            'Tickets': Ticket.objects.count(),
            'Work Orders': WorkOrder.objects.count(),
            'Mileage entries': Mileage.objects.count(),
            'Attachments (+ files)': Attachment.objects.count(),
            'Custom-field values': CustomFieldValue.objects.count(),
            'Email send logs': EmailSendLog.objects.count(),
            'Inbound email logs': InboundEmailLog.objects.count(),
            'Audit-log entries': LogEntry.objects.count(),
            'Device cred access logs': DeviceCredentialAccessLog.objects.count(),
            'Non-superuser users': users_to_delete.count(),
        }

        confirmed = options['confirm'] == CONFIRM_PHRASE

        self.stdout.write('')
        self.stdout.write(self.style.MIGRATE_HEADING(
            'Would DELETE the following operational data:' if not confirmed
            else 'DELETING the following operational data:'
        ))
        for label, n in counts.items():
            self.stdout.write(f'  {n:>6}  {label}')

        kept = {
            'Superusers kept': User.objects.filter(is_superuser=True).count(),
            'Roles kept': self._safe_count('Role'),
            'Status definitions kept': self._safe_count('StatusDefinition'),
            'Help topics kept': self._safe_count('HelpTopic'),
            'SLA plans kept': self._safe_count('SLAPlan'),
            'Repair types kept': self._safe_count('RepairType'),
            'Email templates kept': self._safe_count('EmailTemplate'),
            'Custom-field DEFINITIONS kept': self._safe_count('CustomField'),
            'KB articles kept': self._safe_count('KBArticle'),
            'Org credentials kept': self._safe_count('OrgCredential'),
        }
        self.stdout.write('')
        self.stdout.write(self.style.MIGRATE_HEADING('Keeping (configuration):'))
        for label, n in kept.items():
            self.stdout.write(f'  {n:>6}  {label}')
        self.stdout.write('')

        if not confirmed:
            self.stdout.write(self.style.WARNING(
                'DRY RUN — nothing was deleted. To actually delete, re-run with:\n'
                f'  --confirm "{CONFIRM_PHRASE}"'
            ))
            return

        with transaction.atomic():
            # Attachments: delete files from storage first, then the rows
            # (GenericFK rows are not cascade-deleted, and files outlive rows).
            for att in Attachment.objects.all():
                try:
                    if att.file:
                        att.file.delete(save=False)
                except Exception:
                    pass  # missing file shouldn't block the wipe
            Attachment.objects.all().delete()

            # GenericFK / SET_NULL survivors that won't cascade from Client
            CustomFieldValue.objects.all().delete()
            EmailSendLog.objects.all().delete()
            InboundEmailLog.objects.all().delete()
            LogEntry.objects.all().delete()
            DeviceCredentialAccessLog.objects.all().delete()
            Mileage.objects.all().delete()

            # Clients cascade to contacts, devices, tickets, WOs and everything under them
            Client.objects.all().delete()

            # Non-superuser users last (their personal queues cascade with them)
            users_to_delete.delete()

        self.stdout.write(self.style.SUCCESS(
            'Done. Operational data wiped; configuration and superusers preserved.'
        ))

    def _safe_count(self, model_name):
        from django.apps import apps
        try:
            return apps.get_model('core', model_name).objects.count()
        except Exception:
            return 0
