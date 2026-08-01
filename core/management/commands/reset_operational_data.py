"""
Management command: reset_operational_data

The clean cutover wipe — for the day you switch from OSTicket to Murphy's Bench
for real and want the demo/test data gone, while keeping everything you've
configured.

DELETES (operational data):
  Work Orders and Devices explicitly (client is nullable — walk-in rows and
  orphans wouldn't cascade-delete from Client), then Clients (cascades to
  Contacts, Tickets, replies, locks, links, notes, items, work-performed,
  invoices), Mileage, Attachments (rows AND files on disk), Custom-field
  VALUES, email send/receive logs, the audit-log history, device-credential
  access logs, and all non-superuser users. Also Sales, Estimates (+ options),
  Prospects, Contracts, Assets, card-charge attempts, notifications and any
  orphaned line items — these do NOT cascade from Client (a counter sale or a
  prospect need no client at all) and were silently left behind until Jul 30 2026.

KEEPS (configuration + you):
  SiteSettings, Roles, Status definitions, Help Topics, SLA Plans, Repair Types
  & categories, Checklists & items, Canned Responses, Quick Labor items, Email
  Templates & Signatures, Dashboard Tiles, Custom-field DEFINITIONS, KB
  articles/categories, Org Credentials (+ their access log), blocked/suppressed
  senders, Tech Skills, system queues, and all superuser accounts. Also the
  Products & Services catalog — a price list is configuration, so a shop resetting
  test data keeps it. Note demo data seeds five catalog entries, which therefore
  survive this command; review them under Settings if you did not add them.

SAFE BY DEFAULT: running with no flags is a DRY RUN — it only prints counts.
To actually delete, pass the exact confirmation phrase:

    python manage.py reset_operational_data --confirm "DELETE ALL OPERATIONAL DATA"

Everything runs inside a single transaction, so a failure rolls back cleanly.
Attachment FILES are removed only after that transaction commits — otherwise a
rollback would restore rows whose files had already been deleted from storage.
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
        from core.models import Attachment, User
        from core import operational_data

        keep_users = {u.strip() for u in options['keep_users'].split(',') if u.strip()}
        users_to_delete = User.objects.filter(is_superuser=False).exclude(username__in=keep_users)

        # What is operational, and the order it is safe to delete in, both come
        # from core.operational_data — the same registry seed_demo_data reads to
        # decide whether a box is already in real use. These were two separately
        # hand-maintained lists, and adding a model meant remembering both files
        # with nothing failing if you only remembered one.
        counts = {label: qs.count() for label, qs in operational_data.count_entries()}
        counts['Non-superuser users'] = users_to_delete.count()

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
            # Called out explicitly because demo data seeds five catalog entries,
            # and a price list is configuration a real shop must not lose. Users
            # were told "clear it all"; they need to know these remain.
            'Products & Services kept': self._safe_count('CatalogItem'),
            'Quick-labour checklists kept': self._safe_count('Checklist'),
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

        # Attachment FILES are deleted only AFTER the transaction commits. Deleting
        # them inside it was a one-way door in the middle of a reversible operation:
        # a failure further down rolled the rows back, but the files were already
        # gone from storage, leaving restored Attachment rows pointing at nothing.
        # Collect the storage references now, wipe the rows in the transaction, and
        # only unlink the files once the DB change is durable.
        files_to_unlink = [
            (att.file.storage, att.file.name)
            for att in Attachment.objects.all()
            if att.file
        ]

        with transaction.atomic():
            # Ordered by the registry's delete_order: GenericFK and SET_NULL rows
            # first (walk-in work orders, devices, counter sales and leads never
            # cascade from Client), children before parents, Client last because it
            # cascades to contacts, tickets and everything under them.
            for _label, model in operational_data.deletion_plan():
                model.objects.all().delete()

            # Non-superuser users last (their personal queues cascade with them)
            users_to_delete.delete()

        # Committed. The rows are gone for good, so the files can go too. A file
        # that is already missing must not stop the rest from being cleaned up.
        orphaned_files = 0
        for storage, name in files_to_unlink:
            try:
                storage.delete(name)
            except Exception:
                orphaned_files += 1

        if orphaned_files:
            self.stdout.write(self.style.WARNING(
                f'{orphaned_files} attachment file(s) could not be deleted from storage '
                'and may remain on disk. The database rows were removed.'
            ))

        self.stdout.write(self.style.SUCCESS(
            'Done. Operational data wiped; configuration and superusers preserved.'
        ))

    def _safe_count(self, model_name):
        from django.apps import apps
        try:
            return apps.get_model('core', model_name).objects.count()
        except Exception:
            return 0
