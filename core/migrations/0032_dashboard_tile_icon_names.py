from django.db import migrations

EMOJI_TO_ICON = {
    '\U0001f3ab': 'ticket',   # 🎫
    '⚙️': 'cog',    # ⚙️
    '⏳': 'clock',        # ⏳
    '\U0001f534': 'alert',    # 🔴
    '\U0001f527': 'cog',      # 🔧 (no wrench in Heroicons v1 — cog is closest)
    '✅': 'check',        # ✅
    '\U0001f4cb': 'list',     # 📋
}


def update_icons(apps, schema_editor):
    DashboardTile = apps.get_model('core', 'DashboardTile')
    for tile in DashboardTile.objects.all():
        name = EMOJI_TO_ICON.get(tile.icon)
        if name:
            tile.icon = name
            tile.save(update_fields=['icon'])


def revert_icons(apps, schema_editor):
    icon_to_emoji = {v: k for k, v in EMOJI_TO_ICON.items()}
    DashboardTile = apps.get_model('core', 'DashboardTile')
    for tile in DashboardTile.objects.all():
        emoji = icon_to_emoji.get(tile.icon)
        if emoji:
            tile.icon = emoji
            tile.save(update_fields=['icon'])


class Migration(migrations.Migration):
    dependencies = [
        ('core', '0031_encrypt_credential_fields'),
    ]

    operations = [
        migrations.RunPython(update_icons, revert_icons),
    ]
