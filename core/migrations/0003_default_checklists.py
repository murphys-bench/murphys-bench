from django.db import migrations


REPAIR_TYPES_AND_CHECKLISTS = [
    {
        'repair_type': 'Laptop Repair',
        'checklist': 'Standard Laptop Repair',
        'items': [
            'Document client-reported issue',
            'Inspect physical condition (screen, ports, chassis)',
            'Check power-on and boot behavior',
            'Run hardware diagnostics (memory, storage, CPU)',
            'Check hard drive health (SMART data)',
            'Test battery and charging',
            'Clean fans and vents',
            'Update OS and drivers',
            'Test all ports and peripherals',
            'Confirm issue resolved with client',
        ],
    },
    {
        'repair_type': 'Desktop Repair',
        'checklist': 'Standard Desktop Repair',
        'items': [
            'Document client-reported issue',
            'Inspect physical condition (case, ports, cables)',
            'Check power-on and boot behavior',
            'Run hardware diagnostics (memory, storage, CPU)',
            'Check hard drive health (SMART data)',
            'Clean dust from case and fans',
            'Check and reseat internal connections',
            'Update OS and drivers',
            'Test all ports and peripherals',
            'Confirm issue resolved with client',
        ],
    },
    {
        'repair_type': 'Network Repair',
        'checklist': 'Standard Network Repair',
        'items': [
            'Document client-reported issue',
            'Map current network setup',
            'Test internet connection speed',
            'Check physical cabling and connections',
            'Reboot modem and router',
            'Check device IP and DNS settings',
            'Check firewall and security settings',
            'Update router/switch firmware if needed',
            'Verify all devices connected properly',
            'Document all changes made',
        ],
    },
    {
        'repair_type': 'Virus/Malware Removal',
        'checklist': 'Virus/Malware Removal',
        'items': [
            'Document symptoms and infection type',
            'Disconnect from network',
            'Run Malwarebytes full scan',
            'Run Windows Defender full scan',
            'Check startup programs and scheduled tasks',
            'Check browser extensions and settings',
            'Review installed programs for unwanted software',
            'Update OS and all software',
            'Advise client on password changes if needed',
            'Final scan — confirm clean',
            'Reconnect to network and verify',
        ],
    },
    {
        'repair_type': 'New Computer Setup',
        'checklist': 'New Computer Setup',
        'items': [
            'Unbox and inspect for damage',
            'Update OS to latest version',
            'Install standard software (Office, browser, etc.)',
            'Configure user accounts',
            'Set up email client if needed',
            'Configure backups',
            'Transfer data from old system if applicable',
            'Install and configure security software',
            'Run final Windows Update / verify all updates',
            'Walkthrough with client',
        ],
    },
    {
        'repair_type': 'Data Recovery',
        'checklist': 'Data Recovery',
        'items': [
            'Assess drive health and failure type',
            'Clone drive image before attempting recovery',
            'Attempt logical recovery with software',
            'Document recovered vs. unrecoverable files',
            'Transfer recovered data to new media',
            'Verify data integrity with client',
            'Advise client on backup strategy going forward',
        ],
    },
]


def create_default_checklists(apps, schema_editor):
    RepairType = apps.get_model('core', 'RepairType')
    Checklist = apps.get_model('core', 'Checklist')
    ChecklistItem = apps.get_model('core', 'ChecklistItem')

    for entry in REPAIR_TYPES_AND_CHECKLISTS:
        repair_type, _ = RepairType.objects.get_or_create(
            name=entry['repair_type'],
            defaults={'is_active': True},
        )
        checklist = Checklist.objects.create(
            repair_type=repair_type,
            name=entry['checklist'],
            is_active=True,
            is_default=True,
        )
        for order, description in enumerate(entry['items'], start=1):
            ChecklistItem.objects.create(
                checklist=checklist,
                description=description,
                sort_order=order,
                is_active=True,
            )


def remove_default_checklists(apps, schema_editor):
    Checklist = apps.get_model('core', 'Checklist')
    Checklist.objects.filter(
        name__in=[e['checklist'] for e in REPAIR_TYPES_AND_CHECKLISTS]
    ).delete()


class Migration(migrations.Migration):

    dependencies = [
        ('core', '0002_alter_ticket_status_ticketreply'),
    ]

    operations = [
        migrations.RunPython(create_default_checklists, remove_default_checklists),
    ]
