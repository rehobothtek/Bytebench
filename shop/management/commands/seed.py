from django.core.management import call_command
from django.core.management.base import BaseCommand


class Command(BaseCommand):
    help = "Loads the starter products, repair types, testimonials, and FAQs (safe to re-run)."

    def handle(self, *args, **options):
        self.stdout.write("Loading starter data...")
        call_command('loaddata', 'initial_data')
        self.stdout.write(self.style.SUCCESS("Done — check /admin/ or the shop page."))
