import ast
import re
from pathlib import Path

from django.conf import settings
from django.test import TestCase
from django.urls import reverse
from django.utils import translation

from core.testing import make_staff

LOCALES = [code for code, _ in settings.LANGUAGES if code != "en"]
PLACEHOLDERS = re.compile(r"%\(\w+\)s|%s|%d|<[^>]+>")


class Entry:
    def __init__(self):
        self.msgctxt = self.msgid_plural = None
        self.msgid = self.msgstr = ""
        self.msgstr_plural = {}
        self.fuzzy = False


def catalog(lang):
    """Minimal .po reader (the project has no polib dependency). Skips the header entry."""
    path = Path(settings.LOCALE_PATHS[0]) / lang / "LC_MESSAGES" / "django.po"
    entries, cur, field = [], None, None

    def unquote(text):
        return ast.literal_eval(text)

    for line in path.read_text(encoding="utf-8").splitlines() + [""]:
        if not line.strip():
            if cur is not None and cur.msgid:
                entries.append(cur)
            cur, field = None, None
            continue
        if cur is None:
            cur = Entry()
        if line.startswith("#,") and "fuzzy" in line:
            cur.fuzzy = True
        elif line.startswith("#"):
            continue
        elif line.startswith('"'):
            value = unquote(line)
            if isinstance(field, int):
                cur.msgstr_plural[field] += value
            else:
                setattr(cur, field, (getattr(cur, field) or "") + value)
        else:
            key, _, rest = line.partition(" ")
            m = re.fullmatch(r"msgstr\[(\d+)\]", key)
            if m:
                field = int(m.group(1))
                cur.msgstr_plural[field] = unquote(rest)
            else:
                field = key
                setattr(cur, field, unquote(rest))
    return entries


class CatalogTests(TestCase):
    def test_every_string_is_translated_with_matching_placeholders(self):
        for lang in LOCALES:
            po = catalog(lang)
            self.assertGreater(len(po), 700)
            for e in po:
                self.assertFalse(e.fuzzy, (lang, e.msgid))
                if e.msgid_plural:
                    self.assertTrue(e.msgstr_plural and all(e.msgstr_plural.values()), (lang, e.msgid))
                    for form in e.msgstr_plural.values():
                        self.assertEqual(sorted(PLACEHOLDERS.findall(form)),
                                         sorted(PLACEHOLDERS.findall(e.msgid_plural)), (lang, e.msgid))
                else:
                    self.assertTrue(e.msgstr, (lang, e.msgid))
                    self.assertEqual(sorted(PLACEHOLDERS.findall(e.msgstr)),
                                     sorted(PLACEHOLDERS.findall(e.msgid)), (lang, e.msgid))

    def test_compiled_catalogs_match_the_po_files(self):
        """Fails when a .po file was edited but compilemessages was not run."""
        for lang in LOCALES:
            with translation.override(lang):
                for e in catalog(lang):
                    if e.msgid_plural:
                        continue
                    if e.msgctxt:
                        got = translation.pgettext(e.msgctxt, e.msgid)
                    else:
                        got = translation.gettext(e.msgid)
                    self.assertEqual(got, e.msgstr, (lang, e.msgid))


class LanguageSwitchTests(TestCase):
    def setUp(self):
        self.r, self.owner, self.manager, self.anna, self.ben = make_staff()

    def test_anonymous_visitor_can_switch_the_public_site(self):
        resp = self.client.post(reverse("set_language"), {"language": "fi", "next": "/menu/"})
        self.assertRedirects(resp, "/menu/", fetch_redirect_response=False)
        page = self.client.get("/menu/")
        self.assertContains(page, '<html lang="fi"')
        self.assertContains(page, "Ruokalista")
        self.assertContains(page, 'value="vi"')

    def test_sign_in_page_in_vietnamese(self):
        self.client.post(reverse("set_language"), {"language": "vi", "next": "/login/"})
        self.assertContains(self.client.get("/login/"), "Đăng nhập")

    def test_staff_app_follows_the_chosen_language(self):
        self.client.force_login(self.anna)
        self.client.post(reverse("set_language"), {"language": "fi", "next": "/app/"})
        page = self.client.get("/app/")
        self.assertContains(page, "Tänään")
        self.assertContains(page, "Valikko")  # navigation menu, not the food menu
        self.client.post(reverse("set_language"), {"language": "vi", "next": "/app/"})
        self.assertContains(self.client.get("/app/"), "Hôm nay")

    def test_unsafe_next_is_ignored(self):
        resp = self.client.post(reverse("set_language"), {"language": "fi", "next": "https://evil.example/"})
        self.assertNotIn("evil", resp["Location"])
