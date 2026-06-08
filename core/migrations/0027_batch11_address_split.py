from django.db import migrations, models


def migrate_addresses_forward(apps, schema_editor):
    Client = apps.get_model('core', 'Client')
    for c in Client.objects.exclude(address_street=''):
        c.address_line1 = c.address_street
        c.save(update_fields=['address_line1'])

    SiteSettings = apps.get_model('core', 'SiteSettings')
    for s in SiteSettings.objects.exclude(company_address=''):
        s.company_address_line1 = s.company_address
        s.save(update_fields=['company_address_line1'])


class Migration(migrations.Migration):

    dependencies = [
        ('core', '0026_batch11_site_colors'),
    ]

    operations = [
        # Client: add line1/line2 alongside existing address_street
        migrations.AddField(
            model_name='client',
            name='address_line1',
            field=models.CharField(blank=True, max_length=255, default=''),
            preserve_default=False,
        ),
        migrations.AddField(
            model_name='client',
            name='address_line2',
            field=models.CharField(blank=True, max_length=255, default=''),
            preserve_default=False,
        ),
        # SiteSettings: add line1/line2 alongside existing company_address
        migrations.AddField(
            model_name='sitesettings',
            name='company_address_line1',
            field=models.CharField(blank=True, max_length=255, default='', help_text='Street address'),
            preserve_default=False,
        ),
        migrations.AddField(
            model_name='sitesettings',
            name='company_address_line2',
            field=models.CharField(blank=True, max_length=255, default='', help_text='City, State ZIP'),
            preserve_default=False,
        ),
        # Migrate existing data before removing old columns
        migrations.RunPython(migrate_addresses_forward, migrations.RunPython.noop),
        # Remove old columns
        migrations.RemoveField(model_name='client', name='address_street'),
        migrations.RemoveField(model_name='sitesettings', name='company_address'),
    ]
