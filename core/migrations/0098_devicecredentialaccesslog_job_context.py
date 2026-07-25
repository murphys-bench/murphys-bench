from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):

    dependencies = [
        ('core', '0097_ticketworklog'),
    ]

    operations = [
        migrations.AddField(
            model_name='devicecredentialaccesslog',
            name='ticket',
            field=models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL,
                                    related_name='device_credential_logs', to='core.ticket'),
        ),
        migrations.AddField(
            model_name='devicecredentialaccesslog',
            name='work_order',
            field=models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL,
                                    related_name='device_credential_logs', to='core.workorder'),
        ),
        migrations.AddField(
            model_name='devicecredentialaccesslog',
            name='replaced_existing',
            field=models.BooleanField(default=False),
        ),
    ]
