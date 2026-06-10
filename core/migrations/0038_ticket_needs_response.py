from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('core', '0037_ticket_contact_fk'),
    ]

    operations = [
        migrations.AddField(
            model_name='ticket',
            name='needs_response',
            field=models.BooleanField(default=False, db_index=True),
        ),
    ]
