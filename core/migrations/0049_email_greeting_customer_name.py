# Swap the greeting variable in existing email template bodies from
# {{ client.name }} (which is the last name under our residential data
# convention) to {{ customer_name }} (contact first name for residential,
# company name for business). Mechanical token rename — only touches the
# body and only that exact token, so any other customization is preserved.
# customer_name falls back to client.name, so behavior is unchanged where
# no contact exists.

from django.db import migrations

OLD = '{{ client.name }}'
NEW = '{{ customer_name }}'


def forward(apps, schema_editor):
    EmailTemplate = apps.get_model('core', 'EmailTemplate')
    for tmpl in EmailTemplate.objects.all():
        if OLD in (tmpl.body_template or ''):
            tmpl.body_template = tmpl.body_template.replace(OLD, NEW)
            tmpl.save(update_fields=['body_template'])


def backward(apps, schema_editor):
    EmailTemplate = apps.get_model('core', 'EmailTemplate')
    for tmpl in EmailTemplate.objects.all():
        if NEW in (tmpl.body_template or ''):
            tmpl.body_template = tmpl.body_template.replace(NEW, OLD)
            tmpl.save(update_fields=['body_template'])


class Migration(migrations.Migration):

    dependencies = [
        ('core', '0048_ticket_assignment_unseen'),
    ]

    operations = [
        migrations.RunPython(forward, backward),
    ]
