# Device types become operator-editable rows (Mike's ruling, Aug 15 2026:
# "I should be able to freely add, subtract, or modify device types").
# Replaces the hardcoded DEVICE_TYPE_CHOICES and the hand-copied duplicate of
# it in views.py's checklist-items tab. Devices and checklist scoping reference
# types by slug, so existing data needs no rewrite; 'network' is new (routers,
# modems, switches were being logged as 'other').

from django.db import migrations, models


SEED_TYPES = [
    # (slug, label, icon) — sort_order = position * 10
    ('laptop', 'Laptop', 'laptop'),
    ('desktop', 'Desktop', 'desktop'),
    ('server', 'Server', 'server'),
    ('mobile', 'Mobile Phone', 'mobile'),
    ('tablet', 'Tablet', 'tablet'),
    ('printer', 'Printer', 'printer'),
    ('network', 'Network Device', 'wifi'),
    ('other', 'Other', 'question'),
]


def seed_device_types(apps, schema_editor):
    DeviceType = apps.get_model('core', 'DeviceType')
    for i, (slug, label, icon) in enumerate(SEED_TYPES):
        DeviceType.objects.get_or_create(
            slug=slug, defaults={'label': label, 'icon': icon, 'sort_order': i * 10},
        )


class Migration(migrations.Migration):

    dependencies = [
        ('core', '0105_status_operator_selectable'),
    ]

    operations = [
        migrations.CreateModel(
            name='DeviceType',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('slug', models.SlugField(unique=True)),
                ('label', models.CharField(max_length=100)),
                ('icon', models.CharField(default='question', help_text='Icon name from the built-in set (e.g. laptop, desktop, wifi).', max_length=50)),
                ('sort_order', models.IntegerField(default=0)),
            ],
            options={
                'db_table': 'device_types',
                'ordering': ['sort_order', 'label'],
            },
        ),
        migrations.AlterField(
            model_name='device',
            name='device_type',
            field=models.CharField(default='laptop', max_length=50),
        ),
        migrations.RunPython(seed_device_types, migrations.RunPython.noop),
    ]
