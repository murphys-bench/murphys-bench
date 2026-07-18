"""
Management command: dump_schema

Regenerate docs/database-schema.md from the live models in core/models.py.
This is the introspection helper the schema doc's header refers to — run it
after any model change instead of hand-editing field rows:

    venv/bin/python manage.py dump_schema > docs/database-schema.md

Output is deterministic (models and fields in a stable order) so a regenerate
produces a clean diff limited to real schema changes.
"""
import datetime
import re
from pathlib import Path

from django.apps import apps
from django.conf import settings
from django.contrib.contenttypes.fields import GenericForeignKey, GenericRelation
from django.core.management.base import BaseCommand
from django.db.models import ForeignKey, ManyToManyField, OneToOneField


def _is_encrypted(field):
    return "Encrypted" in type(field).__name__


def _model_description(model):
    """The class docstring, whitespace-collapsed. None for Django's auto docstring."""
    doc = (model.__doc__ or "").strip()
    if not doc or doc.startswith(model.__name__ + "("):
        return None
    return " ".join(doc.split())


def _field_rows(model):
    rows = []
    for field in model._meta.get_fields():
        # Skip auto-created reverse relations; keep own fields + M2M + generic.
        if field.auto_created and not field.concrete:
            continue
        if isinstance(field, GenericForeignKey):
            rows.append((field.name, "GenericForeignKey", ""))
            continue
        if isinstance(field, (GenericRelation, ManyToManyField)):
            target = field.related_model.__name__ if field.related_model else "?"
            extra = [
                name
                for name, on in (("null", getattr(field, "null", False)),
                                 ("blank", getattr(field, "blank", False)))
                if on
            ]
            note = "→ " + target + "".join(", " + e for e in extra)
            rows.append((field.name, "ManyToManyField", note))
            continue

        notes = []
        if getattr(field, "primary_key", False):
            notes.append("PK")
        if isinstance(field, (ForeignKey, OneToOneField)):
            notes.append("→ " + field.related_model.__name__)
        if getattr(field, "null", False):
            notes.append("null")
        if getattr(field, "blank", False):
            notes.append("blank")
        if getattr(field, "unique", False) and not getattr(field, "primary_key", False):
            notes.append("unique")
        choices = getattr(field, "choices", None)
        if choices:
            keys = [str(c[0]) for c in choices if c[0] != ""]
            notes.append("choices: " + "/".join(keys))
        if _is_encrypted(field):
            notes.append("\U0001f512 encrypted")
        rows.append((field.name, field.get_internal_type(), ", ".join(notes)))
    return rows


def _latest_migration():
    """Highest-numbered core migration, e.g. '0095'. '????' if none found."""
    mig_dir = Path(settings.BASE_DIR) / "core" / "migrations"
    highest = -1
    for path in mig_dir.glob("[0-9][0-9][0-9][0-9]_*.py"):
        m = re.match(r"(\d{4})_", path.name)
        if m:
            highest = max(highest, int(m.group(1)))
    return f"{highest:04d}" if highest >= 0 else "????"


class Command(BaseCommand):
    help = "Regenerate docs/database-schema.md from the live models (writes to stdout)."

    def handle(self, *args, **options):
        models = sorted(apps.get_app_config("core").get_models(), key=lambda m: m.__name__)
        migration = _latest_migration()
        out = []
        w = out.append

        w("# Murphy's Bench Database Schema\n")
        w("**Version**: 2.1  ")
        w(f"**Last Updated**: {datetime.date.today():%B %-d, %Y}  ")
        w("**Database**: SQLite (production and dev — a single file, no DB server)  ")
        w(f"**Migrations**: through {migration}  \n")
        w("> This reference is **generated from `core/models.py`** (the live models). It is the")
        w("> field-level companion to `docs/bookstack/07-data-model-and-settings-reference.md`")
        w("> (the conceptual map). Regenerate after model changes with")
        w("> `manage.py dump_schema > docs/database-schema.md` — don't hand-edit field rows.\n")
        w("\U0001f512 = encrypted at rest (AES-256 via django-encrypted-model-fields).\n")
        w(f"**{len(models)} models.** Alphabetical.\n")
        w("---\n")

        for model in models:
            w(f"## {model.__name__}")
            desc = _model_description(model)
            if desc:
                w(f"_{desc}_")
            w(f"`db_table = {model._meta.db_table}`\n")
            w("| Field | Type | Notes |")
            w("|---|---|---|")
            for name, itype, notes in _field_rows(model):
                w(f"| {name} | {itype} | {notes} |")
            w("")

        self.stdout.write("\n".join(out))
