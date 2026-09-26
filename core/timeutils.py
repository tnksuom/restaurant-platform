"""Time helpers. Storage is UTC; business logic uses the restaurant's local zone."""
from datetime import datetime, time, timedelta
from datetime import timezone as dt_timezone
from decimal import ROUND_HALF_UP, Decimal

from django.core.exceptions import ValidationError
from django.utils import timezone
from django.utils.translation import gettext as _


def now():
    """Server time. The single source of 'now' so tests can patch it."""
    return timezone.now()


def local_date(dt, tz):
    return dt.astimezone(tz).date()


def business_today(restaurant):
    return local_date(now(), restaurant.tz)


def day_range_utc(date_from, date_to, tz):
    """[start, end) instants covering local calendar dates date_from..date_to inclusive."""
    start = datetime.combine(date_from, time.min, tzinfo=tz)
    end = datetime.combine(date_to + timedelta(days=1), time.min, tzinfo=tz)
    return start, end


def parse_local_datetime(value, tz):
    """Parse an <input type=datetime-local> value as wall-clock time in `tz`.

    Rejects wall-clock times that do not exist because of a daylight-saving jump.
    Ambiguous times (clocks going back) resolve to the first occurrence.
    """
    value = (value or "").strip()
    if not value:
        return None
    for fmt in ("%Y-%m-%dT%H:%M:%S", "%Y-%m-%dT%H:%M"):
        try:
            naive = datetime.strptime(value, fmt)
            break
        except ValueError:
            continue
    else:
        raise ValidationError(_("Enter a valid date and time."))
    aware = naive.replace(tzinfo=tz, fold=0)
    roundtrip = aware.astimezone(dt_timezone.utc).astimezone(tz)
    if roundtrip.replace(tzinfo=None) != naive:
        raise ValidationError(
            _("%(value)s does not exist in the restaurant's time zone (daylight-saving change)."),
            params={"value": value},
        )
    return aware


def to_local_input(dt, tz):
    """Format an instant for an <input type=datetime-local step=1>."""
    if dt is None:
        return ""
    return dt.astimezone(tz).strftime("%Y-%m-%dT%H:%M:%S")


def hours_display(seconds):
    """Exact seconds -> hours rounded to 2 decimals, only at display time."""
    return (Decimal(int(seconds)) / Decimal(3600)).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)


def hm_display(seconds):
    seconds = int(seconds)
    sign = "-" if seconds < 0 else ""
    seconds = abs(seconds)
    return f"{sign}{seconds // 3600}h {(seconds % 3600) // 60:02d}m"
