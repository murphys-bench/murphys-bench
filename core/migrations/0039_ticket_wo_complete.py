from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('core', '0038_ticket_needs_response'),
    ]

    operations = [
        migrations.AddField(
            model_name='ticket',
            name='wo_complete',
            field=models.BooleanField(default=False, db_index=True),
        ),
    ]
