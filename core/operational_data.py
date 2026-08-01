"""The single registry of what counts as OPERATIONAL data.

Two commands need to agree on this and used to keep separate hand-maintained
lists of it:

  * `seed_demo_data` asks "has a human done real work on this install?" before it
    injects demo records, so it must know every model whose existence is evidence
    of use.
  * `reset_operational_data` asks "what do I delete on a clean cutover?", so it
    must know every operational model AND a safe deletion order.

Those are different questions over the same set, which is exactly why two lists
drifted apart: adding a model meant remembering two files, and nothing failed if
you only remembered one. A seeder that misses a model injects demo data into a
live shop; a reset that misses one leaves real records behind while telling the
user everything was wiped. Both have happened.

So the set is declared ONCE here and each command derives its own view of it.
`test_operational_registry_covers_every_operational_model` fails if a new model
is added to core.models without being classified here, which is the part that
makes this a registry rather than a third list.

Adding a model: add one entry below. `evidence=True` if its existence proves a
human used the install. `delete_order` if the reset must delete it explicitly —
leave it None for anything that reliably cascades from another entry.
"""
from dataclasses import dataclass
from typing import Optional


@dataclass(frozen=True)
class OperationalModel:
    """One operational model and how each command should treat it."""

    # Dotted path resolved lazily, so importing this module never drags in the
    # model layer (the reset command imports models inside handle() for the same
    # reason — the module must stay loadable if models move around).
    model: str

    # Shown in the reset command's count report. Entries appear in the order
    # declared here, which is why this is display order rather than delete order.
    label: str

    # True when the row existing at all means a human has used this install.
    # The seeder refuses to inject demo data if any of these is non-empty.
    evidence: bool = False

    # Restricts what counts as evidence, e.g. the auto-created Unsorted client.
    evidence_filter: Optional[dict] = None

    # Lower deletes first. None = cascades from another entry, so the reset must
    # NOT delete it explicitly (it is still counted for the report).
    delete_order: Optional[int] = None


# Declared in the order the reset command reports counts.
REGISTRY = (
    # ── Client-shaped records ───────────────────────────────────────────────
    # The Unsorted/Unverified bucket is created automatically for inbound
    # triage, so it is never evidence that a human has used this install.
    OperationalModel('core.Client', 'Clients', evidence=True,
                     evidence_filter={'is_unsorted': False}, delete_order=90),
    OperationalModel('core.Contact', 'Contacts'),  # cascades from Client
    OperationalModel('core.Device', 'Devices', evidence=True, delete_order=45),
    OperationalModel('core.Ticket', 'Tickets', evidence=True),  # cascades from Client
    OperationalModel('core.WorkOrder', 'Work Orders', evidence=True, delete_order=40),
    OperationalModel('core.Mileage', 'Mileage entries', delete_order=35),

    # ── Attachments and field values ────────────────────────────────────────
    # ⚠ Attachment FILES are unlinked by the reset command only after its
    # transaction commits; deleting them alongside the rows made a rollback
    # restore rows whose files were already gone.
    OperationalModel('core.Attachment', 'Attachments (+ files)', delete_order=10),
    OperationalModel('core.CustomFieldValue', 'Custom-field values', delete_order=20),

    # ── Logs ────────────────────────────────────────────────────────────────
    # Order matters here in a way it does not elsewhere: a log that records
    # deletions has to be wiped after the things it would record.
    OperationalModel('core.EmailSendLog', 'Email send logs', delete_order=21),
    OperationalModel('core.InboundEmailLog', 'Inbound email logs', delete_order=22),
    # ⚠ MUST DELETE LAST (delete_order 95, after Client at 90). auditlog records
    # the reset's OWN deletions: Ticket, TicketReply, WorkOrder and WorkOrderNote
    # are registered with it, and cascade deletes fire it too. Wiping the log
    # partway through therefore left fresh entries behind naming the very records
    # that were being destroyed — on a real cutover, four rows carrying live
    # client names and ticket subjects, on a box the command had just reported
    # clean. Found by running the real wipe on mb-test; the dry run cannot show
    # it, because nothing is deleted and so nothing is logged.
    OperationalModel('auditlog.LogEntry', 'Audit-log entries', delete_order=95),
    OperationalModel('core.DeviceCredentialAccessLog', 'Device cred access logs',
                     delete_order=24),

    # ── Money-shaped records ────────────────────────────────────────────────
    # None of these cascade reliably from Client: a counter sale and a lead need
    # no client at all, and an Estimate can anchor to a Prospect instead.
    OperationalModel('core.Sale', 'Sales (counter/recurring)', evidence=True,
                     delete_order=60),
    OperationalModel('core.Estimate', 'Estimates (+ options)', evidence=True,
                     delete_order=55),
    OperationalModel('core.EstimateOption', 'Estimate options', delete_order=54),
    OperationalModel('core.Prospect', 'Prospects', evidence=True, delete_order=65),
    OperationalModel('core.Contract', 'Managed contracts', evidence=True,
                     delete_order=70),
    OperationalModel('core.Asset', 'Managed assets', evidence=True, delete_order=75),
    OperationalModel('core.PaymentChargeAttempt', 'Card-charge attempts',
                     delete_order=50),
    OperationalModel('core.Notification', 'Notifications', delete_order=80),
    # LineItem is a host-agnostic GenericForeignKey row. Its hosts are gone by
    # this point, so anything left is an orphan by definition.
    OperationalModel('core.LineItem', 'Priced line items', delete_order=85),
)

# Every core model NOT in REGISTRY, with the reason it is not there. Classification
# is deliberately TOTAL: `test_operational_registry_classifies_every_model` fails on
# any core model that appears in neither place, so adding a model forces a decision
# about it instead of letting it be silently missed by both commands. That forcing
# function is the whole difference between a registry and a third hand-kept list.
NON_OPERATIONAL = {
    # ── Operational, but cascades from a REGISTRY entry ──────────────────────
    'core.ContactPhone': 'cascades from Contact',
    'core.Invoice': 'cascades from WorkOrder',
    'core.TicketReply': 'cascades from Ticket',
    'core.TicketLink': 'cascades from Ticket',
    'core.TicketLock': 'cascades from Ticket',
    'core.TicketWorkLog': 'cascades from Ticket',
    'core.WorkOrderItem': 'cascades from WorkOrder',
    'core.WorkOrderNote': 'cascades from WorkOrder',
    'core.CustomFieldChoice': 'cascades from CustomField (a definition, kept)',

    # ── Configuration a real shop must not lose on a data reset ─────────────
    # A price list is configuration, so the seeder's sample services survive a
    # reset. This is disclosed in the command output and both docs.
    'core.CatalogItem': 'Products & Services catalog is configuration',
    'core.Role': 'configuration',
    'core.SLAPlan': 'configuration',
    'core.HelpTopic': 'configuration',
    'core.StatusDefinition': 'configuration',
    'core.RepairType': 'configuration',
    'core.RepairTypeCategory': 'configuration',
    'core.Checklist': 'configuration',
    'core.ChecklistItem': 'configuration',
    'core.CannedResponse': 'configuration',
    'core.CannedResponseCategory': 'configuration',
    'core.EmailTemplate': 'configuration',
    'core.EmailSignature': 'configuration',
    'core.DashboardTile': 'configuration',
    'core.CustomField': 'a field DEFINITION is configuration; its VALUES are wiped',
    'core.KBArticle': 'configuration',
    'core.KBCategory': 'configuration',
    'core.TechSkill': 'configuration',
    'core.TicketQueue': 'system queues are configuration; personal ones cascade with users',
    'core.SiteSettings': 'configuration',
    'core.BlockedSender': 'configuration',
    'core.SuppressedAddress': 'configuration',
    'core.OrgCredential': 'configuration',

    # ── Audit trails deliberately kept ──────────────────────────────────────
    # Wiping an access or reset log on request would destroy the record of who
    # read what, which is the entire point of keeping one.
    'core.CredentialAccessLog': 'audit trail, kept with its credentials',
    'core.MFAResetLog': 'security audit trail',

    # ── Handled explicitly ──────────────────────────────────────────────────
    # Non-superusers are deleted, superusers and --keep-users are preserved, so
    # this cannot be expressed as a whole-table wipe.
    'core.User': 'handled explicitly by the reset command (superusers preserved)',
}


def resolve(dotted):
    """Return the model class for an ``app.Model`` string."""
    from django.apps import apps
    app_label, model_name = dotted.split('.')
    return apps.get_model(app_label, model_name)


def evidence_querysets():
    """(label, queryset) for every model whose existence proves real use.

    Used by `seed_demo_data` to decide whether this install already belongs to a
    working shop.
    """
    out = []
    for entry in REGISTRY:
        if not entry.evidence:
            continue
        qs = resolve(entry.model).objects.all()
        if entry.evidence_filter:
            qs = qs.filter(**entry.evidence_filter)
        out.append((entry.label, qs))
    return out


def count_entries():
    """(label, queryset) for every operational model, in report order."""
    return [(entry.label, resolve(entry.model).objects.all()) for entry in REGISTRY]


def deletion_plan():
    """(label, model) for the models the reset deletes explicitly, in order.

    Anything with no `delete_order` is omitted: it cascades from another entry,
    and deleting it explicitly would only risk fighting that cascade.
    """
    deletable = [e for e in REGISTRY if e.delete_order is not None]
    return [(e.label, resolve(e.model))
            for e in sorted(deletable, key=lambda e: e.delete_order)]
