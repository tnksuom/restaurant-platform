from django.core.management.base import BaseCommand, CommandError

from inventory.services import verify_ledger


class Command(BaseCommand):
    help = "Check every item's cached balance against the sum of its stock movements."

    def handle(self, *args, **options):
        mismatches = verify_ledger()
        for item, cached, ledger in mismatches:
            self.stderr.write(f"Item {item.pk} {item.name}: cached {cached} != ledger {ledger}")
        if mismatches:
            raise CommandError(f"{len(mismatches)} item(s) out of balance")
        self.stdout.write(self.style.SUCCESS("All item balances match the ledger."))
