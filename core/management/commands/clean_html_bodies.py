"""
Management command: clean_html_bodies

One-off cleanup for inbound mail that was stored as raw HTML markup before
fetch_inbound_email learned to convert HTML-only emails to text. Finds
Ticket.description and TicketReply.content values that look like HTML and
rewrites them to readable plain text using the same converter the live
inbound pipeline now uses.

Dry-run by default (reports what WOULD change, touches nothing).
Pass --apply to write the changes.

    venv/bin/python manage.py clean_html_bodies            # preview
    venv/bin/python manage.py clean_html_bodies --apply    # rewrite
"""

import re

from django.core.management.base import BaseCommand
from django.db import transaction

from core.management.commands.fetch_inbound_email import _html_to_text
from core.models import Ticket, TicketReply

# Conservative HTML detector: a body that contains a recognisable structural tag.
# Avoids touching ordinary text that merely contains a stray "<" (e.g. "load < 5").
_HTML_TAG_RE = re.compile(
    r'<\s*(html|body|table|tr|td|th|div|p|span|br|font|head|style|a|b|strong)\b',
    re.IGNORECASE,
)


def _looks_like_html(text):
    return bool(text) and bool(_HTML_TAG_RE.search(text))


class Command(BaseCommand):
    help = 'Convert already-stored raw-HTML ticket descriptions/replies to plain text.'

    def add_arguments(self, parser):
        parser.add_argument(
            '--apply', action='store_true',
            help='Actually write the conversions. Without this flag it is a dry run.',
        )

    def handle(self, *args, **options):
        apply = options['apply']
        mode = 'APPLY' if apply else 'DRY RUN'
        self.stdout.write(f'[{mode}] Scanning for raw-HTML bodies...')

        ticket_targets = [
            t for t in Ticket.objects.all().only('id', 'ticket_number', 'description')
            if _looks_like_html(t.description)
        ]
        reply_targets = [
            r for r in TicketReply.objects.all().only('id', 'ticket_id', 'content')
            if _looks_like_html(r.content)
        ]

        for t in ticket_targets:
            converted = _html_to_text(t.description)
            self.stdout.write(
                self.style.WARNING(
                    f'  Ticket {t.ticket_number}: '
                    f'{len(t.description)} chars HTML -> {len(converted)} chars text'
                )
            )
            if apply:
                t.description = converted

        for r in reply_targets:
            converted = _html_to_text(r.content)
            self.stdout.write(
                self.style.WARNING(
                    f'  Reply #{r.id} (ticket {r.ticket_id}): '
                    f'{len(r.content)} chars HTML -> {len(converted)} chars text'
                )
            )
            if apply:
                r.content = converted

        total = len(ticket_targets) + len(reply_targets)
        if not total:
            self.stdout.write(self.style.SUCCESS('  No raw-HTML bodies found. Nothing to do.'))
            return

        if not apply:
            self.stdout.write(
                f'[DRY RUN] {len(ticket_targets)} ticket(s) and {len(reply_targets)} '
                f'reply(ies) would be converted. Re-run with --apply to write.'
            )
            return

        with transaction.atomic():
            for t in ticket_targets:
                t.save(update_fields=['description'])
            for r in reply_targets:
                r.save(update_fields=['content'])

        self.stdout.write(
            self.style.SUCCESS(
                f'Done — converted {len(ticket_targets)} ticket(s) and '
                f'{len(reply_targets)} reply(ies).'
            )
        )
