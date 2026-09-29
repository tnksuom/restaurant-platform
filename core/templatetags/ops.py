from decimal import Decimal

from django import template

from core.timeutils import hm_display, hours_display

register = template.Library()


@register.filter
def qty(value):
    """Decimal quantity without trailing zeros or exponent: 5.500 -> 5.5"""
    if value is None or value == "":
        return ""
    d = Decimal(value)
    text = format(d.normalize(), "f")
    return "0" if text in ("-0", "") else text


@register.filter
def signed_qty(value):
    text = qty(value)
    return f"+{text}" if Decimal(value) > 0 else text


@register.filter
def hours(seconds):
    return "" if seconds is None else hours_display(seconds)


@register.filter
def hm(seconds):
    return "" if seconds is None else hm_display(seconds)


@register.filter
def local(dt, tz):
    """Render an instant in the restaurant's zone: {{ dt|local:restaurant.tz|date:'...' }}"""
    return dt.astimezone(tz) if dt else dt


@register.filter
def euro(value):
    """Price in euros for the active language: '€14.50' in English, '14,50 €' in Finnish/Vietnamese."""
    if value is None or value == "":
        return ""
    from django.utils.translation import get_language

    amount = f"{Decimal(value):.2f}"
    if (get_language() or "en").startswith("en"):
        return f"€{amount}"
    return f"{amount.replace('.', ',')} €"
