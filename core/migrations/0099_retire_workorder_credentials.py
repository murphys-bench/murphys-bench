"""Retire the WorkOrder-level credential fields; the Device is the single store.

Two encrypted credential stores existed (WorkOrder.* and Device.*). They were read by
different pages and could never agree. This moves anything held at the WO level onto the
WO's device, then drops the WO fields.

Deliberately conservative:
  * The device's OWN value always wins — it is the master record. A differing WO value is
    appended to the device's credential_notes rather than discarded.
  * device_pin has no Device equivalent, so it is folded into credential_notes.
  * A WO carrying credentials with NO device has nowhere to put them -> raise, do not
    silently drop secrets. (None exist on SCS prod: only WO-00007, which has device #4.)
"""
from django.db import migrations


def _append_note(existing, addition):
    existing = (existing or '').rstrip()
    return f'{existing}\n{addition}' if existing else addition


def merge_credentials(wo_creds, device_creds, wo_number):
    """Pure merge — kept separate from the ORM so it can be unit-tested.

    wo_creds:     dict(username, password, pin, notes) from the WorkOrder
    device_creds: dict(username, password, notes) from the Device
    Returns the Device's resulting dict. The device's own value always wins;
    anything that would be lost is carried into notes rather than discarded.
    """
    out = {k: (device_creds.get(k) or '') for k in ('username', 'password', 'notes')}
    carried = []

    for key in ('username', 'password'):
        incoming = (wo_creds.get(key) or '').strip()
        if not incoming:
            continue
        if not out[key].strip():
            out[key] = incoming
        elif out[key].strip() != incoming:
            carried.append(f'{key.capitalize()} from {wo_number}: {incoming}')

    if (wo_creds.get('pin') or '').strip():
        carried.append(f'PIN from {wo_number}: {wo_creds["pin"].strip()}')
    if (wo_creds.get('notes') or '').strip():
        carried.append(f'Notes from {wo_number}: {wo_creds["notes"].strip()}')

    for line in carried:
        out['notes'] = _append_note(out['notes'], line)
    return out


def move_credentials_to_device(apps, schema_editor):
    WorkOrder = apps.get_model('core', 'WorkOrder')
    stranded = []

    for wo in WorkOrder.objects.all().iterator():
        wo_creds = {
            'username': (wo.device_username or '').strip(),
            'password': (wo.device_password or '').strip(),
            'pin': (wo.device_pin or '').strip(),
            'notes': (wo.credential_notes or '').strip(),
        }
        if not any(wo_creds.values()):
            continue

        if wo.device_id is None:
            stranded.append(wo.work_order_number)
            continue

        device = wo.device
        merged = merge_credentials(wo_creds, {
            'username': device.device_username,
            'password': device.device_password,
            'notes': device.credential_notes,
        }, wo.work_order_number)

        device.device_username = merged['username']
        device.device_password = merged['password']
        device.credential_notes = merged['notes']
        device.save(update_fields=['device_username', 'device_password', 'credential_notes'])

    if stranded:
        raise RuntimeError(
            'Cannot retire WorkOrder credentials: these work orders hold credentials but have '
            'no device to move them to: ' + ', '.join(stranded) + '. Attach a device to each '
            '(or clear the credentials) and re-run the migration.'
        )


def noop_reverse(apps, schema_editor):
    """Irreversible in practice — the WO columns are gone and values were merged."""
    pass


class Migration(migrations.Migration):

    dependencies = [
        ('core', '0098_devicecredentialaccesslog_job_context'),
    ]

    operations = [
        migrations.RunPython(move_credentials_to_device, noop_reverse),
        migrations.RemoveField(model_name='workorder', name='device_username'),
        migrations.RemoveField(model_name='workorder', name='device_password'),
        migrations.RemoveField(model_name='workorder', name='device_pin'),
        migrations.RemoveField(model_name='workorder', name='credential_notes'),
    ]
