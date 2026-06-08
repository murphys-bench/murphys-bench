from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):

    dependencies = [
        ('core', '0023_add_repairtypecategory'),
    ]

    operations = [
        # Drop the old CannedResponse table entirely and recreate clean
        migrations.DeleteModel(
            name='CannedResponse',
        ),
        migrations.CreateModel(
            name='CannedResponseCategory',
            fields=[
                ('id', models.AutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('stream', models.CharField(choices=[('customer', 'Customer Notes'), ('internal', 'Tech Notes (Internal)')], max_length=20)),
                ('name', models.CharField(max_length=100)),
                ('sort_order', models.PositiveIntegerField(default=0)),
            ],
            options={
                'db_table': 'canned_response_categories',
                'ordering': ['stream', 'sort_order', 'name'],
            },
        ),
        migrations.CreateModel(
            name='CannedResponse',
            fields=[
                ('id', models.AutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('stream', models.CharField(choices=[('customer', 'Customer Notes'), ('internal', 'Tech Notes (Internal)')], max_length=20)),
                ('category', models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name='responses', to='core.cannedresponsecategory')),
                ('label', models.CharField(max_length=100)),
                ('body', models.TextField()),
                ('sort_order', models.PositiveIntegerField(default=0)),
                ('created_at', models.DateTimeField(auto_now_add=True)),
            ],
            options={
                'db_table': 'canned_responses',
                'ordering': ['stream', 'category__sort_order', 'sort_order', 'label'],
            },
        ),
    ]
