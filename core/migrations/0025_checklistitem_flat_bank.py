from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('core', '0024_batch11_canned_responses_redesign'),
    ]

    operations = [
        # Drop the old table and recreate clean.
        # Existing checklist items had no device-type scope and were tied to
        # repair-type templates; the new flat bank starts empty so the admin
        # can populate it intentionally.
        migrations.DeleteModel(
            name='ChecklistItem',
        ),
        migrations.CreateModel(
            name='ChecklistItem',
            fields=[
                ('id', models.AutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('name', models.CharField(max_length=255)),
                ('device_types', models.JSONField(blank=True, default=list)),
                ('sort_order', models.IntegerField(default=0)),
                ('is_active', models.BooleanField(default=True)),
                ('created_at', models.DateTimeField(auto_now_add=True)),
            ],
            options={
                'db_table': 'checklist_items',
                'ordering': ['sort_order', 'name'],
            },
        ),
    ]
