from django.db import migrations


def seed_roles(apps, schema_editor):
    Role = apps.get_model('core', 'Role')

    all_perms = dict(
        can_manage_settings=True, can_view_all_tickets=True, can_close_tickets=True,
        can_manage_users=True, can_view_reports=True, can_view_restricted_kb=True,
        can_manage_kb=True, can_create_ticket=True, can_edit_ticket=True,
        can_delete_ticket=True, can_assign_ticket=True, can_reply_internal=True,
        can_reply_customer=True, can_create_workorder=True, can_edit_workorder=True,
        can_close_workorder=True,
    )
    tech_perms = dict(
        can_manage_settings=False, can_view_all_tickets=True, can_close_tickets=True,
        can_manage_users=False, can_view_reports=False, can_view_restricted_kb=False,
        can_manage_kb=False, can_create_ticket=True, can_edit_ticket=True,
        can_delete_ticket=False, can_assign_ticket=True, can_reply_internal=True,
        can_reply_customer=True, can_create_workorder=True, can_edit_workorder=True,
        can_close_workorder=True,
    )

    Role.objects.update_or_create(
        name='Administrator',
        defaults=dict(description='Full access to all features.', is_system=True, **all_perms),
    )
    Role.objects.update_or_create(
        name='Technician',
        defaults=dict(description='Standard technician access.', is_system=True, **tech_perms),
    )


def remove_roles(apps, schema_editor):
    Role = apps.get_model('core', 'Role')
    Role.objects.filter(name__in=['Administrator', 'Technician']).delete()


class Migration(migrations.Migration):

    dependencies = [
        ('core', '0008_batch4_sla_helptopics_kb_roles'),
    ]

    operations = [
        migrations.RunPython(seed_roles, remove_roles),
    ]
