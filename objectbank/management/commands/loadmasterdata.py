from django.core.management.base import BaseCommand
from django.db import transaction
from ...models import (
    ConstructionStage, LeadStatus, WorkerRole,
    RequirementStatus, UrgencyLevel, CreditTransactionType
)


class Command(BaseCommand):
    help = "Loads all master table data (lookup/reference tables)"

    def add_arguments(self, parser):
        parser.add_argument(
            '--reset',
            action='store_true',
            help='Delete existing master data before loading',
        )

    @transaction.atomic
    def handle(self, *args, **options):
        if options['reset']:
            self.stdout.write(self.style.WARNING('Resetting master tables...'))
            ConstructionStage.objects.all().delete()
            LeadStatus.objects.all().delete()
            WorkerRole.objects.all().delete()
            RequirementStatus.objects.all().delete()
            UrgencyLevel.objects.all().delete()
            CreditTransactionType.objects.all().delete()
            self.stdout.write(self.style.SUCCESS('✓ Master tables cleared'))

        # =====================================================
        # CONSTRUCTION STAGES
        # =====================================================
        self.stdout.write('\n📋 Loading Construction Stages...')
        
        construction_stages = [
            {
                'code': 'SITE_PREP',
                'name': 'Site Preparation',
                'sequence_order': 1,
                'description': 'Land clearing, leveling, and initial site work',
                'default_margin_priority': 3
            },
            {
                'code': 'FOUNDATION',
                'name': 'Foundation Work',
                'sequence_order': 2,
                'description': 'Excavation, footings, and foundation laying',
                'default_margin_priority': 5
            },
            {
                'code': 'PLINTH',
                'name': 'Plinth Beam',
                'sequence_order': 3,
                'description': 'Plinth level beams and DPC work',
                'default_margin_priority': 4
            },
            {
                'code': 'COLUMNS',
                'name': 'Column Casting',
                'sequence_order': 4,
                'description': 'Ground floor column work',
                'default_margin_priority': 4
            },
            {
                'code': 'SLAB_GF',
                'name': 'Ground Floor Slab',
                'sequence_order': 5,
                'description': 'Ground floor slab and beam work',
                'default_margin_priority': 5
            },
            {
                'code': 'BRICKWORK',
                'name': 'Brickwork Masonry',
                'sequence_order': 6,
                'description': 'Wall construction with bricks/blocks',
                'default_margin_priority': 4
            },
            {
                'code': 'ROOF',
                'name': 'Roof Slab',
                'sequence_order': 7,
                'description': 'Terrace/roof slab casting',
                'default_margin_priority': 5
            },
            {
                'code': 'PLASTER',
                'name': 'Plastering',
                'sequence_order': 8,
                'description': 'Internal and external wall plastering',
                'default_margin_priority': 3
            },
            {
                'code': 'FLOORING',
                'name': 'Flooring Work',
                'sequence_order': 9,
                'description': 'Tiles, marble, or concrete flooring',
                'default_margin_priority': 4
            },
            {
                'code': 'ELECTRICAL',
                'name': 'Electrical Work',
                'sequence_order': 10,
                'description': 'Wiring, switches, and electrical fittings',
                'default_margin_priority': 3
            },
            {
                'code': 'PLUMBING',
                'name': 'Plumbing Work',
                'sequence_order': 11,
                'description': 'Water supply and sanitation piping',
                'default_margin_priority': 3
            },
            {
                'code': 'DOORS_WINDOWS',
                'name': 'Doors & Windows',
                'sequence_order': 12,
                'description': 'Door and window frame installation',
                'default_margin_priority': 3
            },
            {
                'code': 'PAINTING',
                'name': 'Painting',
                'sequence_order': 13,
                'description': 'Interior and exterior painting',
                'default_margin_priority': 2
            },
            {
                'code': 'FINISHING',
                'name': 'Finishing Work',
                'sequence_order': 14,
                'description': 'False ceiling, final fixtures, and fittings',
                'default_margin_priority': 3
            },
            {
                'code': 'HANDOVER',
                'name': 'Handover & Cleanup',
                'sequence_order': 15,
                'description': 'Final cleanup and project handover',
                'default_margin_priority': 1
            },
        ]

        for stage_data in construction_stages:
            stage, created = ConstructionStage.objects.get_or_create(
                code=stage_data['code'],
                defaults=stage_data
            )
            if created:
                self.stdout.write(f"  ✓ Created: {stage.name}")
            else:
                self.stdout.write(f"  ⊙ Exists: {stage.name}")

        # =====================================================
        # LEAD STATUS
        # =====================================================
        self.stdout.write('\n🎯 Loading Lead Statuses...')
        
        lead_statuses = [
            {
                'code': 'NEW',
                'name': 'New Lead',
                'sequence_order': 1,
                'is_final': False,
                'is_won': False,
                'is_lost': False
            },
            {
                'code': 'CONTACTED',
                'name': 'Contacted',
                'sequence_order': 2,
                'is_final': False,
                'is_won': False,
                'is_lost': False
            },
            {
                'code': 'SITE_VISIT',
                'name': 'Site Visit Done',
                'sequence_order': 3,
                'is_final': False,
                'is_won': False,
                'is_lost': False
            },
            {
                'code': 'QUOTE_SENT',
                'name': 'Quotation Sent',
                'sequence_order': 4,
                'is_final': False,
                'is_won': False,
                'is_lost': False
            },
            {
                'code': 'NEGOTIATION',
                'name': 'Under Negotiation',
                'sequence_order': 5,
                'is_final': False,
                'is_won': False,
                'is_lost': False
            },
            {
                'code': 'WON',
                'name': 'Won - Active Project',
                'sequence_order': 6,
                'is_final': True,
                'is_won': True,
                'is_lost': False
            },
            {
                'code': 'LOST_PRICE',
                'name': 'Lost - Price Issue',
                'sequence_order': 7,
                'is_final': True,
                'is_won': False,
                'is_lost': True
            },
            {
                'code': 'LOST_COMPETITOR',
                'name': 'Lost - Competitor Won',
                'sequence_order': 8,
                'is_final': True,
                'is_won': False,
                'is_lost': True
            },
            {
                'code': 'LOST_TIMING',
                'name': 'Lost - Timing Mismatch',
                'sequence_order': 9,
                'is_final': True,
                'is_won': False,
                'is_lost': True
            },
            {
                'code': 'CANCELLED',
                'name': 'Cancelled by Client',
                'sequence_order': 10,
                'is_final': True,
                'is_won': False,
                'is_lost': True
            },
        ]

        for status_data in lead_statuses:
            status, created = LeadStatus.objects.get_or_create(
                code=status_data['code'],
                defaults=status_data
            )
            if created:
                self.stdout.write(f"  ✓ Created: {status.name}")
            else:
                self.stdout.write(f"  ⊙ Exists: {status.name}")

        # =====================================================
        # WORKER ROLES
        # =====================================================
        self.stdout.write('\n👷 Loading Worker Roles...')
        
        worker_roles = [
            {'code': 'MASON', 'name': 'Mason', 'is_active': True},
            {'code': 'CARPENTER', 'name': 'Carpenter', 'is_active': True},
            {'code': 'PLUMBER', 'name': 'Plumber', 'is_active': True},
            {'code': 'ELECTRICIAN', 'name': 'Electrician', 'is_active': True},
            {'code': 'PAINTER', 'name': 'Painter', 'is_active': True},
            {'code': 'WELDER', 'name': 'Welder', 'is_active': True},
            {'code': 'TILE_LAYER', 'name': 'Tile Layer', 'is_active': True},
            {'code': 'STEEL_FIXER', 'name': 'Steel Fixer', 'is_active': True},
            {'code': 'CONCRETE_WORKER', 'name': 'Concrete Worker', 'is_active': True},
            {'code': 'EXCAVATOR_OP', 'name': 'Excavator Operator', 'is_active': True},
            {'code': 'CRANE_OP', 'name': 'Crane Operator', 'is_active': True},
            {'code': 'HELPER', 'name': 'General Helper', 'is_active': True},
            {'code': 'SUPERVISOR', 'name': 'Site Supervisor', 'is_active': True},
            {'code': 'FOREMAN', 'name': 'Foreman', 'is_active': True},
            {'code': 'CONTRACTOR', 'name': 'Sub-Contractor', 'is_active': True},
        ]

        for role_data in worker_roles:
            role, created = WorkerRole.objects.get_or_create(
                code=role_data['code'],
                defaults=role_data
            )
            if created:
                self.stdout.write(f"  ✓ Created: {role.name}")
            else:
                self.stdout.write(f"  ⊙ Exists: {role.name}")

        # =====================================================
        # REQUIREMENT STATUS
        # =====================================================
        self.stdout.write('\n📌 Loading Requirement Statuses...')
        
        requirement_statuses = [
            {'code': 'OPEN', 'name': 'Open - Unfilled'},
            {'code': 'ASSIGNED', 'name': 'Assigned'},
            {'code': 'IN_PROGRESS', 'name': 'Work In Progress'},
            {'code': 'COMPLETED', 'name': 'Completed'},
            {'code': 'CANCELLED', 'name': 'Cancelled'},
        ]

        for status_data in requirement_statuses:
            status, created = RequirementStatus.objects.get_or_create(
                code=status_data['code'],
                defaults=status_data
            )
            if created:
                self.stdout.write(f"  ✓ Created: {status.name}")
            else:
                self.stdout.write(f"  ⊙ Exists: {status.name}")

        # =====================================================
        # URGENCY LEVELS
        # =====================================================
        self.stdout.write('\n⚡ Loading Urgency Levels...')
        
        urgency_levels = [
            {'code': 'LOW', 'name': 'Low Priority', 'priority_score': 1},
            {'code': 'MEDIUM', 'name': 'Medium Priority', 'priority_score': 3},
            {'code': 'HIGH', 'name': 'High Priority', 'priority_score': 5},
            {'code': 'URGENT', 'name': 'Urgent', 'priority_score': 8},
            {'code': 'CRITICAL', 'name': 'Critical', 'priority_score': 10},
        ]

        for urgency_data in urgency_levels:
            urgency, created = UrgencyLevel.objects.get_or_create(
                code=urgency_data['code'],
                defaults=urgency_data
            )
            if created:
                self.stdout.write(f"  ✓ Created: {urgency.name}")
            else:
                self.stdout.write(f"  ⊙ Exists: {urgency.name}")

        # =====================================================
        # CREDIT TRANSACTION TYPES
        # =====================================================
        self.stdout.write('\n💳 Loading Credit Transaction Types...')
        
        credit_transaction_types = [
            {'code': 'CREDIT_ISSUE', 'name': 'Credit Issued'},
            {'code': 'PAYMENT_RECEIVED', 'name': 'Payment Received'},
            {'code': 'PAYMENT_PARTIAL', 'name': 'Partial Payment'},
            {'code': 'CREDIT_ADJUSTMENT', 'name': 'Credit Adjustment'},
            {'code': 'WRITEOFF', 'name': 'Bad Debt Write-off'},
            {'code': 'INTEREST_CHARGE', 'name': 'Interest Charged'},
            {'code': 'LATE_FEE', 'name': 'Late Payment Fee'},
        ]

        for txn_data in credit_transaction_types:
            txn_type, created = CreditTransactionType.objects.get_or_create(
                code=txn_data['code'],
                defaults=txn_data
            )
            if created:
                self.stdout.write(f"  ✓ Created: {txn_type.name}")
            else:
                self.stdout.write(f"  ⊙ Exists: {txn_type.name}")

        # =====================================================
        # SUMMARY
        # =====================================================
        self.stdout.write('\n' + '='*60)
        self.stdout.write(self.style.SUCCESS('✅ MASTER DATA LOADED SUCCESSFULLY!'))
        self.stdout.write('='*60)
        self.stdout.write(f"  🏗️  Construction Stages: {ConstructionStage.objects.count()}")
        self.stdout.write(f"  🎯 Lead Statuses: {LeadStatus.objects.count()}")
        self.stdout.write(f"  👷 Worker Roles: {WorkerRole.objects.count()}")
        self.stdout.write(f"  📌 Requirement Statuses: {RequirementStatus.objects.count()}")
        self.stdout.write(f"  ⚡ Urgency Levels: {UrgencyLevel.objects.count()}")
        self.stdout.write(f"  💳 Credit Transaction Types: {CreditTransactionType.objects.count()}")
        self.stdout.write('='*60 + '\n')
