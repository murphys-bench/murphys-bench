from django.db import migrations


def retire_closed_work_orders(apps, schema_editor):
    """Move any 'closed' work order to 'completed' and retire the status.

    A work order finishes as 'completed'. 'closed' sat between 'completed' and
    'cancelled' and behaved like neither: the Register settled it, Reports counted
    it finished, the work order list called it active, and a linked ticket was
    never told the work was done. It had already been switched off in
    Settings → Statuses — SCS's own server has had it inactive with zero work
    orders holding it — but the views still accepted it from a posted form, which
    is how it lasted long enough to become a permission bypass.

    'completed' is the right destination, not 'cancelled': these are jobs that
    were finished, not jobs that never happened.

    ⚠ SCOPED TO WORK ORDERS. Tickets close, legitimately and often, and keep their
    own 'closed' status — hence entity_type='workorder' on the StatusDefinition
    query and the WorkOrder model on the row update. Widening either would retire
    a status that half the ticket workflow depends on.
    """
    WorkOrder = apps.get_model('core', 'WorkOrder')
    StatusDefinition = apps.get_model('core', 'StatusDefinition')

    WorkOrder.objects.filter(status='closed').update(status='completed')
    StatusDefinition.objects.filter(entity_type='workorder', slug='closed').delete()


def restore_closed_work_order_status(apps, schema_editor):
    """Put the status definition back, but not the rows.

    Which work orders were 'closed' before the forward migration is not recorded
    anywhere, and inventing an answer would move finished jobs back into a state
    the app no longer agrees about. The definition returns inactive, matching how
    every install that had made the decision was already configured.
    """
    StatusDefinition = apps.get_model('core', 'StatusDefinition')
    StatusDefinition.objects.update_or_create(
        entity_type='workorder', slug='closed',
        defaults=dict(label='Closed', color='#F3F4F6', is_system=True,
                      sort_order=50, is_active=False),
    )


class Migration(migrations.Migration):

    dependencies = [
        ('core', '0103_role_view_all_tickets_preserve_pool'),
    ]

    # ⚠ No AlterField here on purpose. WorkOrder.status carries no `choices=` — it
    # is a plain indexed CharField, because migration 0036 deliberately moved status
    # definitions into the StatusDefinition table so shops can add their own (SCS
    # runs a custom 'parts_hold'). STATUS_CHOICES on the model is now only a
    # constant other code reads, so editing it needs no schema change. An AlterField
    # written here first would have re-added the choices 0036 removed AND dropped
    # the field's db_index along with them.
    operations = [
        migrations.RunPython(retire_closed_work_orders, restore_closed_work_order_status),
    ]
