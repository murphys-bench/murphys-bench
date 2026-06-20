from django.db import migrations


def forwards(apps, schema_editor):
    """Copy every WorkPerformed row into the new generic LineItem model as a
    labor line attached to its work order. Prices stay blank (the price-less
    history can't be backfilled — Mike prices going forward). logged_at is
    preserved with a post-insert update since the field is auto_now_add."""
    WorkPerformed = apps.get_model('core', 'WorkPerformed')
    LineItem = apps.get_model('core', 'LineItem')
    ContentType = apps.get_model('contenttypes', 'ContentType')

    if not WorkPerformed.objects.exists():
        return  # fresh DB (e.g. test build) — nothing to migrate

    # ContentTypes are populated by a post_migrate signal, so they may not exist
    # yet mid-migration — get_or_create rather than get.
    wo_ct, _ = ContentType.objects.get_or_create(app_label='core', model='workorder')

    for wp in WorkPerformed.objects.all():
        label = wp.custom_label or (wp.labor_item.label if wp.labor_item else '—')
        li = LineItem.objects.create(
            content_type=wo_ct,
            object_id=wp.work_order_id,
            kind='labor',
            description=label[:255],
            quantity=1,
            unit_price=None,
            source_labor_item_id=wp.labor_item_id,
            notes=wp.notes,
            logged_by_id=wp.logged_by_id,
        )
        # Preserve the original timestamp (auto_now_add forced "now" on create).
        LineItem.objects.filter(pk=li.pk).update(logged_at=wp.logged_at)


def backwards(apps, schema_editor):
    """Best-effort reverse: drop the labor LineItems that originated from
    WorkPerformed. Part lines and any priced edits made after migration are not
    restored to WorkPerformed (it had neither concept)."""
    LineItem = apps.get_model('core', 'LineItem')
    ContentType = apps.get_model('contenttypes', 'ContentType')
    wo_ct = ContentType.objects.get(app_label='core', model='workorder')
    LineItem.objects.filter(content_type=wo_ct, kind='labor').delete()


class Migration(migrations.Migration):

    dependencies = [
        ('core', '0058_quicklaboritem_default_price_lineitem'),
    ]

    operations = [
        migrations.RunPython(forwards, backwards),
    ]
