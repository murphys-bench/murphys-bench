# One machine = one Device record (Mike's ruling, Aug 15 2026). The Asset model
# is merged back into Device and deleted. An Asset carried exactly one thing a
# Device does not: the contract ("covered by") link — that link moves onto
# Device. Everything promotion used to throw away (credentials, specs, OS,
# repair-intake fields) stays on the one record.
#
# Data movement, in order, BEFORE any column is dropped:
#   1. An asset that came from a promoted device: the two are MERGED. A value the
#      asset holds wins where they differ, but a blank asset field never erases
#      what the device kept, and a device value that is displaced is preserved
#      in notes rather than dropped. Nothing a person typed is lost. The device
#      is un-retired and the coverage link carried over; it keeps its own
#      device_type and serial.
#   2. An asset created directly (no device behind it): a Device row is built
#      for it, asset_type mapped onto a device type; its identifier becomes the
#      serial when that would not collide, otherwise it is kept in notes.
#   3. Any work order that carried only the asset link gains the device link,
#      so no repair history goes unreachable when the asset column is dropped.
#
# Reverse is a noop by design: the merge cannot be un-merged from schema state
# alone. A production rollback is a database restore (update.sh's rollback
# path), never a reverse migration.

from django.db import migrations, models
import django.db.models.deletion


ASSET_TYPE_TO_DEVICE_TYPE = {
    'workstation': 'desktop',
    'server': 'server',
    'network': 'network',
    'mobile': 'mobile',
    'printer': 'printer',
    'other': 'other',
}


def merge_assets_into_devices(apps, schema_editor):
    Asset = apps.get_model('core', 'Asset')
    Device = apps.get_model('core', 'Device')
    WorkOrder = apps.get_model('core', 'WorkOrder')

    for asset in Asset.objects.all().select_related('client', 'contract'):
        source = Device.objects.filter(promoted_to_asset=asset).order_by('id').first()
        if source is not None:
            # Merge, never overwrite blindly. The asset was the record people
            # edited after promotion, so a value it HOLDS wins; but a blank on
            # the asset never erases what the device kept (on prod, the one
            # divergent pair was exactly that shape: device had model+notes,
            # asset had neither). When both hold different text, the device's
            # value is preserved in notes rather than discarded, so nothing a
            # person typed is lost by this migration.
            preserved = []
            for field in ('name', 'manufacturer', 'model'):
                a_val = (getattr(asset, field) or '').strip()
                d_val = (getattr(source, field) or '').strip()
                if a_val and a_val != d_val:
                    if d_val:
                        preserved.append(f'{field} before merge: {d_val}')
                    setattr(source, field, a_val)
            a_notes = (asset.notes or '').strip()
            d_notes = (source.notes or '').strip()
            if a_notes and a_notes != d_notes:
                source.notes = (d_notes + '\n\n' + a_notes) if d_notes else a_notes
            if preserved:
                source.notes = ((source.notes or '').rstrip() + '\n\n' if source.notes else '') \
                    + 'Merged from managed asset. ' + '; '.join(preserved)
            source.is_active = asset.is_active
            source.contract = asset.contract
            source.save()
            device = source
        else:
            serial = (asset.identifier or '').strip() or None
            notes = asset.notes or ''
            if serial and Device.objects.filter(serial_number=serial).exists():
                notes = (notes + '\n' if notes else '') + f'Identifier: {serial}'
                serial = None
            device = Device.objects.create(
                client=asset.client,
                name=asset.name,
                device_type=ASSET_TYPE_TO_DEVICE_TYPE.get(asset.asset_type, 'other'),
                serial_number=serial,
                manufacturer=asset.manufacturer,
                model=asset.model,
                notes=notes,
                contract=asset.contract,
                is_active=asset.is_active,
            )
        WorkOrder.objects.filter(asset=asset, device__isnull=True).update(device=device)


class Migration(migrations.Migration):

    dependencies = [
        ('core', '0106_device_types'),
    ]

    operations = [
        migrations.AddField(
            model_name='device',
            name='contract',
            field=models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name='devices', to='core.contract'),
        ),
        migrations.RunPython(merge_assets_into_devices, migrations.RunPython.noop),
        migrations.RemoveField(
            model_name='device',
            name='promoted_to_asset',
        ),
        migrations.RemoveField(
            model_name='workorder',
            name='asset',
        ),
        migrations.DeleteModel(
            name='Asset',
        ),
    ]
