from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('core', '0062_inbound_dedup'),
    ]

    operations = [
        migrations.AlterField(
            model_name='ticket',
            name='source',
            field=models.CharField(
                choices=[
                    ('email', 'Email'),
                    ('phone', 'Phone'),
                    ('web', 'Web Form'),
                    ('rmm', 'RMM System'),
                    ('system', 'System Alert'),
                ],
                default='email',
                max_length=20,
            ),
        ),
        migrations.AlterField(
            model_name='notification',
            name='kind',
            field=models.CharField(
                choices=[
                    ('tech_message', 'Tech Message'),
                    ('system_alert', 'System Alert'),
                ],
                default='tech_message',
                max_length=30,
            ),
        ),
    ]
