from .models import AuditEvent


def record(actor, action, entity, changes=None, restaurant=None):
    """Append an audit event. `entity` is a model instance."""
    return AuditEvent.objects.create(
        restaurant=restaurant or entity.restaurant,
        actor=actor,
        action=action,
        entity_type=entity._meta.label_lower,
        entity_id=str(entity.pk),
        changes=changes or {},
    )
