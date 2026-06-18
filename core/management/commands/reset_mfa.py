"""Break-glass: clear a user's two-factor devices from the command line.

This is the actual lockout-recovery path for the single-operator internal
deployment — if the sole admin loses their authenticator, no one can reset it
from the web UI, so this command exists. Every reset is recorded in
MFAResetLog (source='cli', actor=None) just like the web path.

Usage:
    python manage.py reset_mfa <username> [--note "..."]
"""

import getpass
import os

from django.core.management.base import BaseCommand, CommandError

from core.models import User, reset_user_mfa


def _shell_identity():
    """Best-effort 'who ran this' for an unauthenticated CLI reset.

    There is no MB user to attribute, so we stamp the OS account and (if this is
    an SSH session) the source connection onto the audit record — turning an
    anonymous reset into a traceable one. SSH_CONNECTION = 'cIP cPort sIP sPort'.
    """
    parts = [f'os-user {getpass.getuser()}']
    conn = os.environ.get('SSH_CONNECTION') or os.environ.get('SSH_CLIENT')
    if conn:
        parts.append(f'from {conn.split()[0]}')
    return ', '.join(parts)


class Command(BaseCommand):
    help = "Clear (reset) a user's two-factor devices for lost-device recovery."

    def add_arguments(self, parser):
        parser.add_argument('username', help='Username of the account to reset.')
        parser.add_argument('--note', default='', help='Optional reason, stored on the audit record.')

    def handle(self, *args, **options):
        username = options['username']
        try:
            target = User.objects.get(username=username)
        except User.DoesNotExist:
            raise CommandError(f'No user with username "{username}".')

        # No authenticated user on the CLI path — stamp the shell identity so the
        # highest-risk reset path is still traceable, not anonymous.
        identity = _shell_identity()
        reason = options['note'].strip()
        note = f'{identity}; {reason}' if reason else identity

        log, count = reset_user_mfa(target, actor=None, source='cli', note=note)
        self.stdout.write(self.style.SUCCESS(
            f'MFA reset for {target.get_full_name() or target.username}: '
            f'{count} device(s) cleared. Audit log #{log.pk} written. '
            'They will be prompted to re-enroll on next login.'
        ))
