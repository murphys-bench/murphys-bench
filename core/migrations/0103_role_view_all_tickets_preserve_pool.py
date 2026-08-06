from django.db import migrations


def preserve_pool_visibility(apps, schema_editor):
    """Turn "View All Tickets" OFF on non-admin roles, preserving what they had.

    The seeded Technician role has carried can_view_all_tickets=True since Batch 4,
    which predates the ticket visibility model entirely. When that model arrived
    (own tickets + the unclaimed pool + escalations), nothing revisited the flag,
    because nothing read it — a technician got pool scoping regardless of what the
    box said.

    v0.12.0 starts reading it. Left alone, the stored True would hand every
    technician on every existing install sight of every ticket the moment they
    upgrade, having changed no setting. That is the reverse of the can_close_tickets
    problem in migration 0102 and the more dangerous direction: 0102 would have
    taken away an ability people had, this would grant one they never had.

    ⚠ Admin roles are deliberately skipped. _is_admin() short-circuits ticket
    scoping, so those roles see everything whatever this flag says; writing False
    there would leave the Roles page showing an unticked box next to a role that
    plainly does see all tickets — a lying checkbox, which is the whole defect
    v0.12.0 exists to end.
    """
    Role = apps.get_model('core', 'Role')
    Role.objects.filter(can_manage_settings=False).update(can_view_all_tickets=False)


def noop_reverse(apps, schema_editor):
    """Deliberately does nothing.

    Reversing cannot tell a role that was True by intent from one that was True
    only because the flag was inert, and guessing would silently widen ticket
    visibility. Stored values stay as the operator left them.
    """


class Migration(migrations.Migration):

    dependencies = [
        ('core', '0102_role_can_close_tickets_default_true'),
    ]

    operations = [
        migrations.RunPython(preserve_pool_visibility, noop_reverse),
    ]
