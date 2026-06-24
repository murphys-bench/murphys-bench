"""
export_data — portable, no-lock-in export of everything in Murphy's Bench.

Unlike `scripts/mb_backup.sh` (a SQLite snapshot you restore back INTO MB), this
writes neutral CSVs — one file per table — plus the attachment/media files, so
the data is readable by any spreadsheet and importable into another system.
Bus-factor: get all the data out without Claude and without SQLite tooling.

    python manage.py export_data                  # -> backups/mb-export-<ts>.tar.gz
    python manage.py export_data --output /tmp    # choose the destination dir
    python manage.py export_data --include-secrets # write DECRYPTED credentials/tokens

By default, encrypted secret fields (device/org credential passwords & notes,
the Invoice Ninja token, mailbox passwords) are written as "***REDACTED***".
Pass --include-secrets to write the real decrypted values — the export then
contains plaintext secrets, so store it like a password vault.

Fail-loud: any error aborts with a non-zero exit; the partial staging dir is
cleaned up so you never ship a half-written export.
"""
import csv
import os
import shutil
import tarfile
import tempfile
from datetime import datetime

from django.apps import apps
from django.conf import settings
from django.core.management.base import BaseCommand, CommandError

REDACTED = '***REDACTED***'

# Log/audit/transient tables: high churn, low portability value — skipped by default.
SKIP_MODELS = {
    'EmailSendLog', 'InboundEmailLog', 'DeviceCredentialAccessLog',
    'CredentialAccessLog', 'MFAResetLog', 'TicketLock',
}


def _is_secret_field(field):
    """An encrypted-at-rest field (django-encrypted-model-fields)."""
    return type(field).__name__.startswith('Encrypted')


class Command(BaseCommand):
    help = ('Export all data to portable CSVs + a media bundle (tar.gz). '
            'Encrypted secrets are redacted unless --include-secrets is given.')

    def add_arguments(self, parser):
        parser.add_argument(
            '--output', default=None,
            help='Destination directory for the export tarball (default: <project>/backups).',
        )
        parser.add_argument(
            '--include-secrets', action='store_true',
            help='Write DECRYPTED credentials/tokens instead of ***REDACTED*** '
                 '(the export will then contain plaintext secrets).',
        )
        parser.add_argument(
            '--no-media', action='store_true',
            help='Skip bundling the protected/ and media/ file trees (CSVs only).',
        )

    def handle(self, *args, **opts):
        base_dir = settings.BASE_DIR
        out_dir = opts['output'] or os.path.join(base_dir, 'backups')
        os.makedirs(out_dir, exist_ok=True)

        ts = datetime.now().strftime('%Y%m%d-%H%M%S')
        archive = os.path.join(out_dir, f'mb-export-{ts}.tar.gz')

        stage = tempfile.mkdtemp(prefix='mb-export-', dir=out_dir)
        try:
            csv_dir = os.path.join(stage, 'csv')
            os.makedirs(csv_dir)

            models = sorted(
                apps.get_app_config('core').get_models(),
                key=lambda m: m.__name__,
            )
            total_rows = 0
            secret_cols = 0
            exported = []

            for model in models:
                name = model.__name__
                if name in SKIP_MODELS:
                    continue

                fields = list(model._meta.fields)  # concrete local fields (FKs as <name>_id)
                redact = set()
                if not opts['include_secrets']:
                    redact = {f.attname for f in fields if _is_secret_field(f)}
                secret_cols += len(redact)

                rows = 0
                path = os.path.join(csv_dir, f'{name}.csv')
                with open(path, 'w', newline='', encoding='utf-8') as fh:
                    writer = csv.writer(fh)
                    writer.writerow([f.attname for f in fields])
                    for obj in model._default_manager.all().iterator():
                        out = []
                        for f in fields:
                            if f.attname in redact:
                                # Show whether a secret exists without revealing it.
                                val = getattr(obj, f.attname, '')
                                out.append(REDACTED if val else '')
                            else:
                                out.append(f.value_from_object(obj))
                        writer.writerow(out)
                        rows += 1
                total_rows += rows
                exported.append((name, rows))
                self.stdout.write(f'  {name}: {rows} rows')

            # Bundle the attachment/media file trees (turnkey: data + the files it references).
            media_note = 'excluded (--no-media)'
            if not opts['no_media']:
                for sub in ('protected', 'media'):
                    src = os.path.join(base_dir, sub)
                    if os.path.isdir(src):
                        shutil.copytree(src, os.path.join(stage, sub))
                media_note = 'included (protected/ + media/)'

            self._write_readme(stage, ts, exported, total_rows,
                               opts['include_secrets'], media_note)

            with tarfile.open(archive, 'w:gz') as tar:
                tar.add(stage, arcname=f'mb-export-{ts}')

            # Verify the archive is readable before declaring success.
            with tarfile.open(archive, 'r:gz') as tar:
                if not tar.getmembers():
                    raise CommandError('export archive verified empty')
        except Exception as exc:
            shutil.rmtree(stage, ignore_errors=True)
            if os.path.exists(archive):
                os.remove(archive)
            raise CommandError(f'export failed: {exc}')
        else:
            shutil.rmtree(stage, ignore_errors=True)

        size_mb = os.path.getsize(archive) / 1_048_576
        self.stdout.write(self.style.SUCCESS(
            f'\nExport OK: {archive} ({size_mb:.1f} MB) — '
            f'{len(exported)} tables, {total_rows} rows. Media {media_note}.'
        ))
        if opts['include_secrets']:
            self.stdout.write(self.style.WARNING(
                '⚠ This export contains DECRYPTED secrets — store it like a password vault.'
            ))
        elif secret_cols:
            self.stdout.write(
                f'(Encrypted fields redacted. Re-run with --include-secrets to include them.)'
            )

    def _write_readme(self, stage, ts, exported, total_rows, with_secrets, media_note):
        lines = [
            f'Murphy\'s Bench data export — {ts}',
            '',
            'This is a PORTABLE export: one CSV per table (csv/), plus the attachment',
            'and media files. It is for reading/importing elsewhere, NOT for restoring',
            'back into Murphy\'s Bench — for that use a backup tarball + scripts/restore.sh.',
            '',
            f'Tables: {len(exported)}   Total rows: {total_rows}',
            f'Media: {media_note}',
            f'Secrets: {"DECRYPTED (handle as a vault)" if with_secrets else "redacted as ***REDACTED***"}',
            '',
            'CSV notes:',
            '  - Foreign keys are exported as the related row id (column ends in _id).',
            '  - Timestamps are in the server timezone.',
            '',
            'Row counts:',
        ]
        lines += [f'  {name}: {rows}' for name, rows in exported]
        with open(os.path.join(stage, 'README.txt'), 'w', encoding='utf-8') as fh:
            fh.write('\n'.join(lines) + '\n')
