from datetime import date, datetime
from decimal import Decimal

from django.db import models

from .models import FinanceAuditLog


def _json_value(value):
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    if isinstance(value, Decimal):
        return str(value)
    if isinstance(value, (date, datetime)):
        return value.isoformat()
    if isinstance(value, models.Model):
        return str(value)
    return str(value)


def snapshot(instance, fields):
    values = {}
    for field_name in fields:
        value = getattr(instance, field_name)
        display_method = getattr(instance, f"get_{field_name}_display", None)
        if callable(display_method):
            value = display_method()
        values[field_name] = _json_value(value)
    return values


def compare_snapshots(before, after):
    changes = {}
    for field_name in sorted(set(before) | set(after)):
        old_value = before.get(field_name)
        new_value = after.get(field_name)
        if old_value != new_value:
            changes[field_name] = {"before": old_value, "after": new_value}
    return changes


def record_finance_audit(*, actor, action, instance, changes=None):
    model = instance._meta
    actor_label = ""
    if actor is not None:
        actor_label = actor.get_full_name() or actor.get_username()

    return FinanceAuditLog.objects.create(
        actor=actor if getattr(actor, "pk", None) else None,
        actor_label=actor_label,
        action=action,
        entity_type=model.label_lower,
        entity_label=str(model.verbose_name),
        object_id=str(instance.pk),
        object_repr=str(instance)[:255],
        changes=changes or {},
    )
