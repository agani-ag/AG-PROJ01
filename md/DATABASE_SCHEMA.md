# DATABASE SCHEMA: Entity Relationship Documentation

## ENTITY RELATIONSHIP OVERVIEW

```
┌─────────────────────────────────────────────────────────────────┐
│                     MASTER DATA LAYER                            │
└─────────────────────────────────────────────────────────────────┘

ConstructionStage          LeadStatus               WorkerRole
├─ code                    ├─ code                  ├─ code
├─ name                    ├─ name                  ├─ name
├─ sequence_order          ├─ sequence_order        └─ is_active
├─ default_margin_priority ├─ is_final
└─ is_active               ├─ is_won
                           └─ is_lost

RequirementStatus          UrgencyLevel             CreditTransactionType
├─ code                    ├─ code                  ├─ code
└─ name                    ├─ name                  └─ name
                           └─ priority_score


┌─────────────────────────────────────────────────────────────────┐
│                    PROJECT ECOSYSTEM                             │
└─────────────────────────────────────────────────────────────────┘

                             Project
                    ┌────────────────────────┐
                    │ project_code (PK)      │
                    │ name, client_name      │
                    │ phone, full_address    │
                    │ pincode, city          │◄─────┐
                    │ latitude, longitude    │      │
                    │ estimated_total_value  │      │
                    │ expected_completion    │      │
                    │                        │      │
                    │ FK: current_stage      │──────┼─→ ConstructionStage
                    │ FK: lead_status        │──────┼─→ LeadStatus
                    │ FK: referred_by_worker │──────┼─→ Worker (REFERRAL)
                    └────────────────────────┘      │
                              │                     │
                              │                     │
                    ┌─────────┴─────────┐           │
                    │                   │           │
                    ▼                   ▼           │
            ProjectStage      ProjectRevenueTransaction
          ┌──────────────┐    ┌────────────────────┐
          │ FK: project  │    │ FK: project        │
          │ FK: stage    ├───→│ FK: stage          │
          │ estimated    │    │ FK: worker         │──→ Worker
          │ captured     │    │ invoice_number     │
          │ margin_%     │    │ revenue, cost      │
          │ is_completed │    │ margin_amount      │
          └──────────────┘    │ transaction_date   │
                              └────────────────────┘


┌─────────────────────────────────────────────────────────────────┐
│                    WORKER ECOSYSTEM                              │
└─────────────────────────────────────────────────────────────────┘

                              Worker
                    ┌────────────────────────┐
                    │ worker_code (PK)       │
                    │ name, phone            │
                    │ primary_pincode        │
                    │ joined_date            │
                    │ active_status          │
                    │ FK: role               │──→ WorkerRole
                    └────────────────────────┘
                              │
                              │
                    ┌─────────┴─────────────────┐
                    │                           │
                    ▼                           ▼
              WorkerProject            WorkerCreditLedger
          ┌──────────────────┐      ┌────────────────────┐
          │ FK: worker       │      │ FK: worker         │
          │ FK: project      │◄─────│ FK: project        │
          │ FK: role         │      │ FK: trans_type     │
          │ revenue_generated│      │ debit, credit      │
          │ FK: referred_by  │─┐    │ running_balance    │
          └──────────────────┘ │    │ due_date           │
                               │    │ is_settled         │
                        ┌──────┘    └────────────────────┘
                        │
                        └──→ Worker (Referral Graph)


┌─────────────────────────────────────────────────────────────────┐
│                  DEMAND-SUPPLY ENGINE                            │
└─────────────────────────────────────────────────────────────────┘

                 ProjectWorkerRequirement
                ┌────────────────────────┐
                │ FK: project            │◄───── Project
                │ FK: role               │◄───── WorkerRole
                │ required_from_date     │
                │ FK: urgency            │◄───── UrgencyLevel
                │ FK: status             │◄───── RequirementStatus
                └────────────────────────┘
                          │
                          │ OneToOne
                          ▼
                  WorkerAssignment
                ┌────────────────────────┐
                │ FK: requirement        │
                │ FK: worker             │◄───── Worker
                │ assigned_date          │
                │ completion_date        │
                │ revenue_impact         │
                └────────────────────────┘
```

---

## RELATIONSHIP CARDINALITY

### **PROJECT RELATIONSHIPS**

| From              | Relationship | To                      | Cardinality | Purpose                        |
|-------------------|--------------|-------------------------|-------------|--------------------------------|
| Project           | has          | ConstructionStage       | Many-to-One | Current stage tracking         |
| Project           | has          | LeadStatus              | Many-to-One | Sales funnel position          |
| Project           | has many     | ProjectStage            | One-to-Many | Stage breakdown per project    |
| Project           | has many     | ProjectRevenueTransaction | One-to-Many | Revenue capture               |
| Project           | referred_by  | Worker                  | Many-to-One | Referral source                |
| Project           | has many     | WorkerProject           | One-to-Many | Workers involved               |
| Project           | has many     | ProjectWorkerRequirement| One-to-Many | Worker demand                  |

### **WORKER RELATIONSHIPS**

| From              | Relationship | To                      | Cardinality | Purpose                        |
|-------------------|--------------|-------------------------|-------------|--------------------------------|
| Worker            | has          | WorkerRole              | Many-to-One | Worker specialization          |
| Worker            | on many      | WorkerProject           | One-to-Many | Project collaborations         |
| Worker            | has many     | WorkerCreditLedger      | One-to-Many | Credit history                 |
| Worker            | has many     | WorkerAssignment        | One-to-Many | Assignments received           |
| Worker            | referred      | Project                 | One-to-Many | Projects referred              |
| Worker A          | referred      | Worker B                | Many-to-One | Referral network (via WorkerProject) |

### **REVENUE & STAGE RELATIONSHIPS**

| From              | Relationship | To                      | Cardinality | Purpose                        |
|-------------------|--------------|-------------------------|-------------|--------------------------------|
| ProjectStage      | belongs to   | Project                 | Many-to-One | Stage in this project          |
| ProjectStage      | is a         | ConstructionStage       | Many-to-One | Which stage type               |
| ProjectRevenueTransaction | for  | Project               | Many-to-One | Revenue for which project      |
| ProjectRevenueTransaction | at   | ConstructionStage     | Many-to-One | Revenue at which stage         |
| ProjectRevenueTransaction | by   | Worker                | Many-to-One | Worker who generated revenue   |

### **DEMAND-SUPPLY RELATIONSHIPS**

| From              | Relationship | To                      | Cardinality | Purpose                        |
|-------------------|--------------|-------------------------|-------------|--------------------------------|
| ProjectWorkerRequirement | for | Project               | Many-to-One | Requirement for which project  |
| ProjectWorkerRequirement | needs | WorkerRole           | Many-to-One | Which type of worker needed    |
| WorkerAssignment  | fulfills     | ProjectWorkerRequirement | One-to-One | Assignment fills requirement |
| WorkerAssignment  | assigns      | Worker                  | Many-to-One | Which worker assigned          |

---

## KEY DATA PATTERNS

### **1. REFERRAL GRAPH**

```
Pattern: Worker → Projects → Worker (Recursive Network)

Example:
  Worker A refers Project X
  Worker B works on Project X (collaboration)
  Worker B refers Worker C for Project Y
  
Traversal:
  Project.referred_by_worker → Worker
  WorkerProject.referred_by_worker → Worker (in collaboration context)
```

### **2. STAGE PROGRESSION**

```
Pattern: Sequential Stage Tracking

Flow:
  FOUNDATION (seq=1) → CIVIL (seq=2) → ELECTRICAL (seq=3) → ...
  
Implementation:
  ConstructionStage.sequence_order defines progression
  Project.current_stage = pointer to current position
  ProjectStage = breakdown for each stage
```

### **3. LEDGER PATTERN**

```
Pattern: Running Balance Ledger (Debit/Credit)

Flow:
  Transaction 1: Debit 5000, Balance = 5000
  Transaction 2: Credit 2000, Balance = 3000
  Transaction 3: Debit 1000, Balance = 4000
  
Implementation:
  WorkerCreditLedger stores running_balance
  Each entry references previous balance
```

### **4. OPPORTUNITY TRACKING**

```
Pattern: Estimated vs. Captured Value

Per Stage:
  Estimated: What you COULD win at this stage
  Captured: What you ACTUALLY won
  Remaining: estimated - captured
  
Aggregation:
  Project-level remaining opportunity = SUM(all stages remaining)
```

### **5. DEMAND-SUPPLY MATCH**

```
Pattern: Requirement → Assignment (One-to-One)

Flow:
  1. Create ProjectWorkerRequirement (status=OPEN)
  2. Match Worker (by role, pincode, availability)
  3. Create WorkerAssignment (requirement link)
  4. Update requirement status=ASSIGNED
  
Rule: One requirement = One assignment (no double-assign)
```

---

## INDEXING STRATEGY

### **HIGH-FREQUENCY QUERIES (Need Indexes)**

```sql
-- Project filtering by location
INDEX: Project.pincode
INDEX: Project.lead_status_id
INDEX: Project.current_stage_id

-- Revenue aggregations
INDEX: ProjectRevenueTransaction.transaction_date
INDEX: ProjectRevenueTransaction.project_id, stage_id

-- Worker matching
INDEX: Worker.primary_pincode
INDEX: Worker.role_id
INDEX: Worker.active_status

-- Credit tracking
INDEX: WorkerCreditLedger.worker_id
INDEX: WorkerCreditLedger.is_settled
INDEX: WorkerCreditLedger.due_date
```

### **UNIQUE CONSTRAINTS**

```sql
UNIQUE: Project.project_code
UNIQUE: Worker.worker_code
UNIQUE: (ProjectStage.project_id, ProjectStage.stage_id)
UNIQUE: (WorkerProject.worker_id, WorkerProject.project_id)
UNIQUE: WorkerAssignment.requirement_id (OneToOne)
```

---

## DATA INTEGRITY RULES

### **CASCADE & PROTECTION**

| Model             | Foreign Key         | ON DELETE Rule | Reason                           |
|-------------------|---------------------|----------------|----------------------------------|
| Project           | current_stage       | PROTECT        | Can't delete stage in use        |
| Project           | lead_status         | PROTECT        | Can't delete status in use       |
| Project           | referred_by_worker  | SET_NULL       | Worker deletion shouldn't break  |
| ProjectStage      | project             | CASCADE        | Delete stage if project deleted  |
| ProjectStage      | stage               | PROTECT        | Can't delete master stage        |
| WorkerProject     | worker              | CASCADE        | Remove link if worker deleted    |
| WorkerProject     | project             | CASCADE        | Remove link if project deleted   |
| WorkerCreditLedger| worker              | CASCADE        | Delete ledger if worker deleted  |
| WorkerAssignment  | requirement         | CASCADE        | Assignment tied to requirement   |
| WorkerAssignment  | worker              | PROTECT        | Can't delete assigned worker     |

---

## CALCULATED FIELDS (NOT STORED)

These should be computed via service layer:

```
Worker:
  - influence_score (from referrals, revenue, network)
  - loyalty_score (from referred_projects ratio)
  - reliability_score (from credit ledger)
  - availability_score (from active assignments)
  - total_revenue (from ProjectRevenueTransaction)

Project:
  - remaining_opportunity (from ProjectStage values)
  - capture_ratio (from revenue vs estimated)
  - stage_completion_percentage

ProjectStage:
  - capture_percentage (captured / estimated * 100)
```

**Why not stored?** These are aggregations/calculations that change frequently. Compute on-demand or cache temporarily.

---

## QUERY PATTERN LIBRARY

### **Most Common Queries**

#### **1. Get Projects in a Pincode**
```python
projects = Project.objects.filter(
    pincode='560001',
    lead_status__is_won=False
).select_related('current_stage', 'lead_status')
```

#### **2. Get Available Workers for a Role in a Pincode**
```python
workers = Worker.objects.filter(
    role__code='ELECTRICIAN',
    primary_pincode='560001',
    active_status=True
).exclude(
    assignments__completion_date__isnull=True  # Exclude currently assigned
)
```

#### **3. Calculate Remaining Opportunity for a Project**
```python
from django.db.models import Sum, F

remaining = ProjectStage.objects.filter(
    project=project
).aggregate(
    total=Sum(F('estimated_stage_value') - F('captured_stage_revenue'))
)['total']
```

#### **4. Revenue per Worker (All Time)**
```python
from django.db.models import Sum

worker_revenue = ProjectRevenueTransaction.objects.filter(
    worker=worker
).aggregate(total=Sum('revenue_amount'))['total'] or 0
```

#### **5. Top Revenue-Generating Pincodes**
```python
pincode_revenue = ProjectRevenueTransaction.objects.values(
    'project__pincode'
).annotate(
    total_revenue=Sum('revenue_amount'),
    project_count=Count('project', distinct=True)
).order_by('-total_revenue')
```

#### **6. Worker Referral Network**
```python
# Direct referrals
referred_projects = Project.objects.filter(
    referred_by_worker=worker
).count()

# Workers referred (collaborations)
referred_workers = WorkerProject.objects.filter(
    referred_by_worker=worker
).values('worker').distinct().count()
```

#### **7. Projects with Unfilled Worker Requirements**
```python
unfilled = ProjectWorkerRequirement.objects.filter(
    status__code='OPEN',
    required_from_date__lte=timezone.now() + timedelta(days=7)
).select_related('project', 'role', 'urgency')
```

#### **8. Credit Risk Workers**
```python
from django.db.models import Sum, Q

risky_workers = WorkerCreditLedger.objects.filter(
    is_settled=False
).values('worker').annotate(
    outstanding=Sum(F('debit') - F('credit'))
).filter(
    outstanding__gt=20000  # Over 20K outstanding
)
```

---

## EVOLUTION NOTES

### **PHASE 1 (Current):** Foundation Complete
- All core entities implemented
- Relationships established
- Basic service layer exists

### **PHASE 2 (Next):** Intelligence Layer
- Add calculated score fields (cached)
- Implement analytics aggregation queries
- Build recommendation engine

### **PHASE 3 (Future):** Optimization
- Denormalize for performance (if needed)
- Add materialized views for common aggregations
- Implement caching strategy

---

**Document Status:** Schema Documentation Complete  
**Last Updated:** February 12, 2026  
**Schema Version:** 1.0
