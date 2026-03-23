from django.core.management.base import BaseCommand

from apps.subscriptions.models import Plan
from utils.plans import PLAN_CATALOG


class Command(BaseCommand):
    help = 'Seed Plan table from PLAN_CATALOG constant'

    def handle(self, *args, **options):
        for entry in PLAN_CATALOG:
            plan, created = Plan.objects.update_or_create(
                id=entry['id'],
                defaults={
                    'display_name':  entry['display_name'],
                    'description':   entry['description'],
                    'price_monthly': entry['price_monthly'],
                    'price_annual':  entry['price_annual'],
                    'popular':       entry['popular'],
                    'highlights':    entry['highlights'],
                },
            )
            verb = 'Created' if created else 'Updated'
            self.stdout.write(f'{verb}: {plan}')
        self.stdout.write(self.style.SUCCESS('Plans seeded successfully.'))
