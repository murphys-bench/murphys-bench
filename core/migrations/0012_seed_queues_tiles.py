from django.db import migrations


def seed_queues_and_tiles(apps, schema_editor):
    TicketQueue = apps.get_model('core', 'TicketQueue')
    DashboardTile = apps.get_model('core', 'DashboardTile')

    # System queues
    TicketQueue.objects.bulk_create([
        TicketQueue(
            name='All Open',
            owner=None,
            filter_criteria={'status': ['new', 'open', 'in_progress', 'waiting_on_customer']},
            sort_field='created_at',
            sort_direction='desc',
        ),
        TicketQueue(
            name='Unassigned',
            owner=None,
            filter_criteria={'status': ['new', 'open'], 'assigned_to': None},
            sort_field='created_at',
            sort_direction='asc',
        ),
        TicketQueue(
            name='Overdue',
            owner=None,
            filter_criteria={'overdue': True},
            sort_field='due_at',
            sort_direction='asc',
        ),
    ])

    # Dashboard tiles — ticket row
    ticket_tiles = [
        ('My Open Tickets', ['new', 'open', 'in_progress', 'waiting_on_customer'], '/tickets/?assigned_to=me', 'all', '🎫', 0),
        ('In Progress', ['in_progress'], '/tickets/?status=in_progress', 'all', '⚙️', 1),
        ('Waiting on Customer', ['waiting_on_customer'], '/tickets/?status=waiting_on_customer', 'all', '⏳', 2),
        ('Overdue', [], '/tickets/?overdue=1', 'all', '🔴', 3),
    ]
    for label, statuses, url, visible, icon, order in ticket_tiles:
        DashboardTile.objects.create(
            row='ticket',
            label=label,
            status_filter=statuses,
            link_url=url,
            sort_order=order,
            is_active=True,
            visible_to=visible,
            icon=icon,
        )

    # Dashboard tiles — work order row
    wo_tiles = [
        ('My Open Work Orders', ['new', 'assigned', 'in_progress'], '/work-orders/?assigned_to=me', 'all', '🔧', 0),
        ('In Progress', ['in_progress'], '/work-orders/?status=in_progress', 'all', '⚙️', 1),
        ('Completed', ['completed'], '/work-orders/?status=completed', 'all', '✅', 2),
        ('All Open', ['new', 'assigned', 'in_progress'], '/work-orders/', 'admin', '📋', 3),
    ]
    for label, statuses, url, visible, icon, order in wo_tiles:
        DashboardTile.objects.create(
            row='workorder',
            label=label,
            status_filter=statuses,
            link_url=url,
            sort_order=order,
            is_active=True,
            visible_to=visible,
            icon=icon,
        )


def unseed(apps, schema_editor):
    TicketQueue = apps.get_model('core', 'TicketQueue')
    DashboardTile = apps.get_model('core', 'DashboardTile')
    TicketQueue.objects.filter(owner=None).delete()
    DashboardTile.objects.all().delete()


class Migration(migrations.Migration):
    dependencies = [
        ('core', '0011_batch6_queues_dashboard'),
    ]

    operations = [
        migrations.RunPython(seed_queues_and_tiles, unseed),
    ]
