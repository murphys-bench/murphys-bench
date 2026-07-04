from django.db import migrations, models
import django.core.validators
import django.db.models.deletion


class Migration(migrations.Migration):
    """Rename QuickLaborItem → CatalogItem (labor-only catalog → Products & Services)
    and generalize LineItem.source_labor_item → catalog_item, adding item_type.

    Hand-written as RENAMES (not create/delete) so existing catalog rows and every
    line-item link to them are PRESERVED. Django's autodetector fell back to
    drop-and-recreate, which would have destroyed prod data — do not regenerate."""

    dependencies = [
        ('core', '0076_client_is_managed_client_monthly_amount_and_more'),
    ]

    operations = [
        migrations.RenameModel(
            old_name='QuickLaborItem',
            new_name='CatalogItem',
        ),
        migrations.AlterModelTable(
            name='catalogitem',
            table='catalog_items',
        ),
        migrations.RenameField(
            model_name='catalogitem',
            old_name='label',
            new_name='name',
        ),
        migrations.AddField(
            model_name='catalogitem',
            name='item_type',
            field=models.CharField(
                choices=[('service', 'Service'), ('product', 'Product')],
                db_index=True, default='service', max_length=10,
                help_text='Service (logs a labor line) or Product (logs a part line).',
            ),
        ),
        migrations.RenameField(
            model_name='lineitem',
            old_name='source_labor_item',
            new_name='catalog_item',
        ),
        migrations.AlterModelOptions(
            name='catalogitem',
            options={'ordering': ['category', 'sort_order', 'name']},
        ),
        # Refresh field help_text/target to match the model (no data change).
        migrations.AlterField(
            model_name='catalogitem',
            name='name',
            field=models.CharField(
                max_length=100,
                help_text='Display name, e.g. "Virus / Malware Removal" or "1TB SSD".',
            ),
        ),
        migrations.AlterField(
            model_name='catalogitem',
            name='category',
            field=models.CharField(
                max_length=100, help_text='Groups items by category, e.g. "Software"',
            ),
        ),
        migrations.AlterField(
            model_name='catalogitem',
            name='print_description',
            field=models.TextField(
                blank=True,
                help_text='Client-facing description printed on documents. Leave blank to use the name.',
            ),
        ),
        migrations.AlterField(
            model_name='catalogitem',
            name='default_price',
            field=models.DecimalField(
                blank=True, decimal_places=2, max_digits=10, null=True,
                validators=[django.core.validators.MinValueValidator(0)],
                help_text='Optional. Prefills the price when this item logs a line. Leave blank for no price.',
            ),
        ),
        migrations.AlterField(
            model_name='lineitem',
            name='catalog_item',
            field=models.ForeignKey(
                blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL,
                related_name='line_items', to='core.catalogitem',
            ),
        ),
    ]
