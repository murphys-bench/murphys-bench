from django.db import migrations, models


def grant_close_to_existing_roles(apps, schema_editor):
    """Every role that exists before v0.12.0 could close tickets in practice.

    The flag was displayed but never enforced, so a role with the box unchecked
    still closed tickets all day. v0.12.0 starts enforcing it. Leaving the stored
    values alone would mean a shop that upgrades wakes up to technicians who can
    no longer resolve anything, having changed no setting themselves. Backfilling
    True preserves what each shop actually had; turning it off is now a choice an
    operator makes on purpose.
    """
    Role = apps.get_model('core', 'Role')
    Role.objects.update(can_close_tickets=True)


def noop_reverse(apps, schema_editor):
    """Deliberately does nothing.

    Reversing cannot know which roles were False by intent and which were False
    only because nothing read the flag, and guessing would silently revoke a
    permission an operator had granted. The field default reverts; stored values
    stay as the operator left them.
    """


class Migration(migrations.Migration):

    dependencies = [
        ('core', '0101_sitesettings_install_initialized_at'),
    ]

    operations = [
        migrations.AlterField(
            model_name='role',
            name='can_close_tickets',
            field=models.BooleanField(default=True, help_text='Resolve and close tickets.'),
        ),
        migrations.RunPython(grant_close_to_existing_roles, noop_reverse),
    ]
