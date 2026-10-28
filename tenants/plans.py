"""The plans a new installation starts with. Prices are placeholders: change them in the
platform administration or with a data migration before going live."""
from decimal import Decimal

DEFAULT_PLANS = [
    # code, name, price €/month, max locations, max active staff (None = no limit), website, position
    ("starter", "Starter", Decimal("29.00"), 1, 15, False, 1),
    ("pro", "Pro", Decimal("59.00"), 3, 60, True, 2),
    ("business", "Business", Decimal("99.00"), 10, None, True, 3),
]


def ensure_default_plans(plan_model):
    for code, name, price, locations, staff, website, position in DEFAULT_PLANS:
        plan_model.objects.get_or_create(code=code, defaults={
            "name": name, "monthly_price": price, "max_locations": locations, "max_staff": staff,
            "includes_website": website, "position": position, "is_public": True,
        })
