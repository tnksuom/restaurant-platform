"""Fill an EMPTY development database with realistic demo data.

    python manage.py seed_demo --password 'Demo-Password-2026'

Refuses to run when DEBUG is off or when any staff already exist.
All history is created through the same services the app uses, with the clock
moved back in time, so balances, snapshots and audit rows are all genuine.
"""
import io
from contextlib import contextmanager
from datetime import datetime, timedelta
from decimal import Decimal
from unittest import mock

from django.conf import settings
from django.core.files.uploadedfile import SimpleUploadedFile
from django.core.management.base import BaseCommand, CommandError
from django.db import transaction
from django.utils import timezone
from PIL import Image, ImageDraw

from checklists import services as cs
from checklists.models import ChecklistIssue, ResponseStatus
from core import audit
from core.models import Restaurant
from inventory import services as inv
from inventory.models import MovementType
from staff import services as staff_services
from staff.models import Employee, Role
from timeclock import services as ts


@contextmanager
def at(moment):
    with mock.patch("django.utils.timezone.now", return_value=moment):
        yield


def fridge_photo():
    img = Image.new("RGB", (960, 720), (218, 222, 226))
    d = ImageDraw.Draw(img)
    d.rectangle((140, 40, 820, 700), fill=(236, 239, 241), outline=(120, 128, 136), width=8)
    d.rectangle((170, 70, 790, 670), outline=(90, 98, 106), width=14)
    d.rectangle((760, 280, 790, 470), fill=(70, 76, 84))
    for x, y in ((170, 300), (190, 340), (175, 380)):  # torn seal
        d.line((x, y, x + 60, y + 25), fill=(160, 40, 40), width=10)
    d.rectangle((40, 40, 330, 120), fill=(255, 255, 255))
    d.text((60, 70), "Walk-in fridge 6.5 C", fill=(20, 20, 20))
    buf = io.BytesIO()
    img.save(buf, "JPEG", quality=88)
    return SimpleUploadedFile("fridge-door-seal.jpg", buf.getvalue(), content_type="image/jpeg")


class Command(BaseCommand):
    help = "Create demo staff, inventory, checklists and shifts in an empty development or demo database."

    def add_arguments(self, parser):
        parser.add_argument("--password", required=True, help="Password for every demo account")

    def handle(self, *args, **opts):
        if not (settings.DEBUG or settings.DEMO_MODE):
            raise CommandError("seed_demo only runs with DJANGO_DEBUG=true or DEMO_MODE=true.")
        if Employee.objects.exists():
            raise CommandError("The database already has staff. Use an empty database for demo data.")
        with transaction.atomic():
            self.build(opts["password"])
        self.stdout.write(self.style.SUCCESS("Demo data created. Sign in as owner, mia, linh, eero or sara."))

    def build(self, pw):
        tz_name = settings.DEFAULT_RESTAURANT_TIME_ZONE
        r = Restaurant.objects.create(name="Bistro Demo", time_zone=tz_name)
        tz = r.tz
        real_now = timezone.now()
        today = real_now.astimezone(tz).date()

        def local(day_offset, hour, minute=0):
            d = today + timedelta(days=day_offset)
            return datetime(d.year, d.month, d.day, hour, minute, tzinfo=tz)

        start = local(-8, 8)
        with at(start):
            owner = Employee.objects.create_user(r, "owner", "Olli Owner", Role.OWNER, password=pw)
            audit.record(None, "employee.owner_provisioned", owner, {"via": "seed_demo"})
            people = {}
            for login, name, role, phone in [
                ("mia", "Mia Virtanen", Role.MANAGER, "040 111 2233"),
                ("linh", "Linh Nguyen", Role.EMPLOYEE, "044 555 0101"),
                ("eero", "Eero Korhonen", Role.EMPLOYEE, ""),
                ("sara", "Sara Nieminen", Role.EMPLOYEE, "050 987 6543"),
                ("pekka", "Pekka Laine", Role.EMPLOYEE, ""),
            ]:
                emp = staff_services.create_employee(owner, name, login, role, phone)
                emp.set_password(pw)
                emp.save()
                people[login] = emp
            staff_services.create_employee(owner, "Tuan Tran", "tuan", Role.EMPLOYEE)  # invited, no password yet
        mia, linh, eero, sara, pekka = (people[k] for k in ("mia", "linh", "eero", "sara", "pekka"))

        # ---------------- inventory
        items = {}
        with at(start + timedelta(minutes=30)):
            for name, cat, unit, opening, minimum, supplier in [
                ("Jasmine rice", "ingredient", "kg", "40", "10", "Kespro"),
                ("Rice noodles", "ingredient", "kg", "16", "5", "Asia Market Oy"),
                ("Fish sauce", "ingredient", "L", "8", "4", "Asia Market Oy"),
                ("Chicken thigh", "ingredient", "kg", "20", "8", "Kespro"),
                ("Tofu", "ingredient", "kg", "10", "5", "Asia Market Oy"),
                ("Fresh herbs", "ingredient", "kg", "4", "2", "Local farm"),
                ("Coconut milk", "ingredient", "pcs", "60", "12", "Kespro"),
                ("Limes", "ingredient", "pcs", "100", "30", "Kespro"),
                ("Lager 0.33", "drink", "pcs", "144", "48", "Olvi"),
                ("Takeaway boxes", "packaging", "pcs", "500", "200", "Pakkaus Oy"),
                ("Dish detergent", "cleaning_supply", "L", "12", "4", "Kiilto"),
            ]:
                items[name] = inv.create_item(
                    mia, name=name, category=cat, unit=unit, opening_quantity=opening,
                    minimum_quantity=minimum, supplier_name=supplier,
                )
        usage = [
            ("Jasmine rice", "4.5"), ("Rice noodles", "1.8"), ("Chicken thigh", "2.4"), ("Tofu", "1.1"),
            ("Fresh herbs", "0.2"), ("Coconut milk", "5"), ("Limes", "9"), ("Lager 0.33", "20"),
            ("Takeaway boxes", "35"), ("Dish detergent", "0.9"), ("Fish sauce", "0.6"),
        ]
        staff_cycle = [linh, eero, sara]
        for day in range(-7, 0):
            actor = staff_cycle[day % 3]
            with at(local(day, 21, 30)):
                for name, q in usage:
                    inv.post_movement(actor, items[name].pk, MovementType.USAGE, q)
        with at(local(-4, 10, 5)):
            inv.post_movement(linh, items["Chicken thigh"].pk, MovementType.RECEIPT, "15", note="Kespro delivery 88213")
            inv.post_movement(linh, items["Jasmine rice"].pk, MovementType.RECEIPT, "20", note="Kespro delivery 88213")
            inv.post_movement(linh, items["Lager 0.33"].pk, MovementType.RECEIPT, "96", note="Olvi order 4471")
        with at(local(-2, 11, 20)):
            inv.post_movement(eero, items["Fresh herbs"].pk, MovementType.WASTE, "0.4", waste_reason="EXPIRED",
                              note="Coriander wilted")
            inv.post_movement(sara, items["Lager 0.33"].pk, MovementType.WASTE, "2", waste_reason="DAMAGED",
                              note="Dropped crate")
        with at(local(-1, 22, 40)):
            inv.post_movement(mia, items["Limes"].pk, MovementType.ADJUSTMENT, "-3", note="Weekly count: 3 fewer than recorded")

        # ---------------- checklists
        C, T, P = "CONFIRMATION", "TEMPERATURE", "PASS_FAIL"
        with at(start + timedelta(hours=1)):
            opening = cs.save_template(mia, None, title="Opening", kind="OPENING", tasks=[
                {"text": "Unlock back door and switch on lights", "response_type": C, "required": True},
                {"text": "Walk-in fridge temperature", "response_type": T, "required": True,
                 "equipment_or_location": "Walk-in fridge", "min_temperature": Decimal("0"), "max_temperature": Decimal("5")},
                {"text": "Freezer temperature", "response_type": T, "required": True,
                 "equipment_or_location": "Chest freezer", "min_temperature": Decimal("-25"), "max_temperature": Decimal("-18")},
                {"text": "Hand-wash station has soap and paper", "response_type": P, "required": True},
                {"text": "Check morning delivery against order", "response_type": C, "required": False},
            ])
            closing = cs.save_template(mia, None, title="Closing", kind="CLOSING", tasks=[
                {"text": "Grill and wok burners cleaned", "response_type": P, "required": True},
                {"text": "Fryer oil checked", "response_type": P, "required": True},
                {"text": "Walk-in fridge temperature", "response_type": T, "required": True,
                 "equipment_or_location": "Walk-in fridge", "min_temperature": Decimal("0"), "max_temperature": Decimal("5")},
                {"text": "Floors mopped", "response_type": C, "required": True},
                {"text": "Doors locked and alarm on", "response_type": C, "required": True},
            ])
            cs.save_template(mia, None, title="Weekly deep clean", kind="CLEANING", tasks=[
                {"text": "Clean extractor hood filters", "response_type": C, "required": True},
                {"text": "Descale dishwasher", "response_type": C, "required": True},
            ])

        def fill(template, day, hour, who, answers, submit=True, base=None):
            base = base or local(day, hour)
            with at(base):
                run, _ = cs.start_run(who, template.pk)
            tasks = list(run.tasks.select_related("response").order_by("position", "id"))
            for i, (task, ans) in enumerate(zip(tasks, answers)):
                if ans is None:
                    continue
                status, value, note = ans
                with at(base + timedelta(minutes=2 + i)):
                    cs.save_response(who, task.response.pk, status, temperature=value, note=note)
            if submit:
                with at(base + timedelta(minutes=10)):
                    cs.submit_run(who, run.pk)
            return run

        ok = (ResponseStatus.PASS, None, "")
        for day in range(-7, 0):
            who = staff_cycle[day % 3]
            fill(opening, day, 9, who, [ok, (ResponseStatus.PASS, "3.2", ""), (ResponseStatus.PASS, "-20.5", ""), ok, ok])
            closer = staff_cycle[(day + 1) % 3]
            if day == -3:
                run = fill(closing, day, 22, closer, [ok, (ResponseStatus.FAIL, None, "Oil dark, needs changing"),
                                                     (ResponseStatus.PASS, "4.1", ""), ok, ok])
                issue = ChecklistIssue.objects.get(run=run)
                with at(local(-2, 10, 15)):
                    cs.resolve_issue(mia, issue.pk, "Oil changed before lunch service")
            elif day == -1:
                run = fill(closing, day, 22, closer, [ok, ok, (ResponseStatus.PASS, "6.5", "Door was left ajar"), ok, ok],
                           submit=False)
                resp = run.responses.get(task__text="Walk-in fridge temperature")
                with at(local(-1, 22, 8)):
                    cs.report_issue(closer, resp.pk, "Door seal on the walk-in fridge is torn at the bottom left",
                                    fridge_photo())
                with at(local(-1, 22, 12)):
                    cs.submit_run(closer, run.pk)
            else:
                fill(closing, day, 22, closer, [ok, ok, (ResponseStatus.PASS, "3.8", ""), ok, ok])
        # today's opening: in progress
        now_local = real_now.astimezone(tz)
        begin = now_local - timedelta(minutes=12)
        if begin.date() == today:
            fill(opening, 0, 0, linh, [ok, (ResponseStatus.PASS, "3.4", ""), None, None, None], submit=False, base=begin)

        # ---------------- shifts
        pattern = {linh: (9, 17), eero: (15, 23), sara: (11, 19), mia: (10, 18)}
        for day in range(-7, 0):
            for emp, (h_in, h_out) in pattern.items():
                if (day + emp.pk) % 5 == 0 or (day == -1 and emp in (eero, sara)):
                    continue  # day off (Eero and Sara are on the late shift that is still running)
                with at(local(day, h_in, 2)):
                    ts.clock_in(emp)
                with at(local(day, h_in + 4, 0)):
                    ts.start_break(emp)
                with at(local(day, h_in + 4, 30)):
                    ts.end_break(emp)
                end_hour = h_out
                with at(local(day, end_hour, 6) if end_hour < 24 else local(day + 1, end_hour - 24, 6)):
                    ts.clock_out(emp)
        # Pekka worked earlier in the week, then left
        with at(local(-6, 12)):
            ts.clock_in(pekka)
        with at(local(-6, 16)):
            ts.clock_out(pekka)
        with at(local(-5, 9)):
            staff_services.deactivate(owner, pekka)
        # A forgotten clock-out, corrected by Mia
        with at(local(-2, 10)):
            s = ts.clock_in(sara) if not ts.open_shift(sara) else ts.open_shift(sara)
        # (sara already has a closed shift for -2 if not day off; make this a separate early shift)
        s.refresh_from_db()
        with at(local(-1, 9, 30)):
            ts.correct_shift(mia, s, clock_in_at=local(-2, 7, 0), clock_out_at=local(-2, 10, 30), breaks=[],
                             reason="Sara forgot to clock out after the early prep shift; confirmed with her")
        # Currently on shift: Eero on an evening shift that runs past midnight, now on break
        start_eve = local(-1, 17) if now_local.hour < 8 else local(0, max(now_local.hour - 5, 0))
        if not ts.open_shift(eero):
            with at(start_eve):
                ts.clock_in(eero)
            with at(real_now - timedelta(minutes=8)):
                ts.start_break(eero)
        if not ts.open_shift(sara):
            with at(start_eve - timedelta(hours=1)):
                ts.clock_in(sara)

        self.build_public(r, mia, today)

    # ---------------------------------------------------------------- public website and menu

    def build_public(self, r, manager, today):
        """Example public content. Everything is clearly placeholder, to be replaced with the real restaurant's."""
        from datetime import time as dtime

        from menu import services as ms
        from website import services as ws
        from website.models import OpeningHours, PublicProfile

        store = ms.public_store()
        profile = PublicProfile.objects.create(
            restaurant=r,
            tagline="Vietnamese street food and family recipes (example text)",
            introduction=(
                "This is example text for the demo restaurant. Replace it with a few sentences about your "
                "restaurant: who cooks, what you are known for, and why guests come back."
            ),
            street_address="Kauppakatu 12", postal_code="40100", city="Jyväskylä", country="Finland",
            phone="+358 40 000 0000", email="hello@example.com",
            logo_key=store.write(placeholder_logo(), "png"),
            hero_key=store.write(placeholder_photo("Bistro Demo", (1600, 1200), (46, 94, 74), bowls=3), "jpg"),
            hero_alt="Example photo: bowls of noodle soup on a wooden table",
        )
        for weekday in range(5):
            OpeningHours.objects.create(restaurant=r, weekday=weekday, opens=dtime(10, 30), closes=dtime(21, 0))
        OpeningHours.objects.create(restaurant=r, weekday=5, opens=dtime(12, 0), closes=dtime(2, 0))
        OpeningHours.objects.create(restaurant=r, weekday=6, opens=dtime(12, 0), closes=dtime(18, 0))

        ws.save_promotion(manager, None, title="Lunch deal: phở + iced coffee 15 €",
                          body="Weekdays 10:30–14:00. Example promotion.", starts_on=today - timedelta(days=5),
                          ends_on=today + timedelta(days=25), is_published=True)
        ws.save_promotion(manager, None, title="Summer terrace", body="This one has ended, so it no longer shows.",
                          starts_on=today - timedelta(days=90), ends_on=today - timedelta(days=10), is_published=True)
        ws.save_promotion(manager, None, title="Christmas menu", body="Starts later, so it is not shown yet.",
                          starts_on=today + timedelta(days=60), ends_on=None, is_published=True)

        menu = [
            ("Starters", [
                ("Gỏi cuốn", "Fresh rice paper rolls with shrimp, herbs and peanut sauce.", "8.50",
                 ["CRUSTACEANS", "PEANUTS", "SOY"], ["G", "M"], True, (212, 140, 90)),
                ("Chả giò", "Crispy fried spring rolls with pork and glass noodles.", "8.90",
                 ["GLUTEN", "EGGS", "FISH"], ["M"], True, None),
                ("Edamame", "Steamed soybeans with sea salt.", "5.50", ["SOY"], ["VE", "G"], True, None),
            ]),
            ("Noodle soups", [
                ("Phở bò", "Slow-cooked beef broth, rice noodles, sliced beef, herbs and lime.", "14.50",
                 ["FISH", "SOY"], ["G", "M"], True, (150, 82, 44)),
                ("Phở gà", "Chicken broth with rice noodles, chicken and herbs.", "13.90",
                 ["FISH"], ["G", "M"], True, None),
                ("Phở chay", "Vegetable broth with tofu, mushrooms and herbs.", "13.50",
                 ["SOY"], ["VE", "G"], False, None),  # sold out today
            ]),
            ("Rice and noodles", [
                ("Bún chả", "Grilled pork patties, rice vermicelli, herbs and nước chấm.", "15.90",
                 ["FISH", "SOY", "SESAME"], ["M"], True, (120, 70, 40)),
                ("Cơm tấm", "Broken rice with grilled pork chop and fried egg.", "15.50",
                 ["EGGS", "FISH", "SOY"], ["M", "G"], True, None),
            ]),
            ("Drinks", [
                ("Cà phê sữa đá", "Vietnamese iced coffee with condensed milk.", "4.90", ["MILK"], ["V", "G"], True, None),
                ("Lime soda", "Fresh lime, soda and a little sugar.", "3.90", [], ["VE", "G"], True, None),
            ]),
        ]
        for cat_name, dishes in menu:
            category = ms.create_category(manager, cat_name)
            for name, desc, price, allergens, diet, available, colour in dishes:
                photo = None
                if colour:
                    photo = SimpleUploadedFile(f"{name}.jpg", placeholder_photo(name, (1200, 900), colour), "image/jpeg")
                ms.save_item(manager, None, {
                    "name": name, "category": category.pk, "description": desc, "price": price,
                    "allergens": allergens, "diet_labels": diet, "status": "PUBLISHED",
                    "is_available": available, "when_unavailable": "MARK",
                }, photo=photo)
        drafts = ms.create_category(manager, "Desserts")
        ms.save_item(manager, None, {
            "name": "Chè ba màu", "category": drafts.pk, "description": "Three-colour dessert. Draft: not public yet.",
            "price": "6.50", "status": "DRAFT", "is_available": True, "when_unavailable": "MARK",
        })


def placeholder_photo(label, size, colour, bowls=1):
    """A plain illustration with 'EXAMPLE PHOTO' on it, so nobody mistakes it for the real food."""
    w, h = size
    img = Image.new("RGB", size, colour)
    d = ImageDraw.Draw(img)
    for y in range(h):  # soft vertical shading
        f = 0.75 + 0.35 * y / h
        d.line((0, y, w, y), fill=tuple(min(255, int(c * f)) for c in colour))
    d.rectangle((0, int(h * 0.62), w, h), fill=(110, 78, 52))  # table
    spots = [0.5] if bowls == 1 else [0.25, 0.55, 0.8][:bowls]
    for i, cx in enumerate(spots):
        r = int(min(w, h) * (0.24 if bowls == 1 else 0.16))
        x, y = int(w * cx), int(h * (0.6 if i % 2 == 0 else 0.66))
        d.ellipse((x - r, y - r // 2, x + r, y + r // 2 + r // 3), fill=(245, 242, 235), outline=(210, 205, 195), width=6)
        d.ellipse((x - int(r * .8), y - int(r * .38), x + int(r * .8), y + int(r * .3)), fill=(205, 150, 80))
        for k in range(5):
            d.arc((x - int(r * .6) + k * 9, y - int(r * .25), x + int(r * .3) + k * 9, y + int(r * .2)), 200, 340,
                  fill=(250, 238, 205), width=5)
    d.rectangle((24, 24, 330, 84), fill=(255, 255, 255))
    d.text((40, 36), "EXAMPLE PHOTO", fill=(30, 30, 30), font_size=26)
    d.text((40, 64), label[:28], fill=(90, 90, 90), font_size=14)
    buf = io.BytesIO()
    img.save(buf, "JPEG", quality=85)
    return buf.getvalue()


def placeholder_logo():
    img = Image.new("RGBA", (256, 256), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    d.ellipse((0, 0, 255, 255), fill=(46, 94, 74))
    d.ellipse((18, 18, 237, 237), outline=(250, 247, 241), width=6)
    d.text((128, 128), "BD", fill=(250, 247, 241), font_size=96, anchor="mm")
    buf = io.BytesIO()
    img.save(buf, "PNG")
    return buf.getvalue()
