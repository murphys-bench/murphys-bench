from django.db import migrations, models


def dedupe_message_ids(apps, schema_editor):
    """Remove pre-existing duplicate Message-ID log rows (keep the earliest)
    so the unique constraint below can be added cleanly. Past overlapping fetch
    runs created a handful of these."""
    InboundEmailLog = apps.get_model('core', 'InboundEmailLog')
    seen = set()
    for row in (InboundEmailLog.objects
                .exclude(message_id='')
                .order_by('created_at', 'id')
                .iterator()):
        if row.message_id in seen:
            row.delete()
        else:
            seen.add(row.message_id)


class Migration(migrations.Migration):

    dependencies = [
        ('core', '0061_client_invoice_ninja_id_and_more'),
    ]

    operations = [
        migrations.AlterField(
            model_name='inboundemaillog',
            name='status',
            field=models.CharField(
                choices=[
                    ('new_ticket', 'Created New Ticket'),
                    ('reply', 'Added Reply to Ticket'),
                    ('duplicate', 'Duplicate — Already Processed'),
                    ('error', 'Processing Error'),
                    ('processing', 'Processing (claimed)'),
                ],
                max_length=20,
            ),
        ),
        migrations.RunPython(dedupe_message_ids, migrations.RunPython.noop),
        migrations.AddConstraint(
            model_name='inboundemaillog',
            constraint=models.UniqueConstraint(
                condition=~models.Q(message_id=''),
                fields=('message_id',),
                name='uniq_inbound_message_id',
            ),
        ),
    ]
