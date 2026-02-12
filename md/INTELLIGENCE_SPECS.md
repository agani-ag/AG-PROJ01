# BUSINESS INTELLIGENCE SPECIFICATIONS
## Calculation Formulas, Scoring Models & Decision Rules

**System:** AG-PROJ01 Construction Supply Ecosystem  
**Purpose:** Intelligence layer specifications for implementation  
**Date:** February 12, 2026

---

## 1. WORKER SCORING MODELS

### 1.1 INFLUENCE SCORE

**Purpose:** Measure worker's overall business impact and network value

**Formula Components:**

```python
influence_score = (
    project_referral_score * 0.40 +
    revenue_generation_score * 0.30 +
    network_expansion_score * 0.20 +
    tenure_score * 0.10
)
```

**Detailed Calculations:**

```python
# Component 1: Project Referral Score
projects_referred = Project.objects.filter(referred_by_worker=worker).count()
project_referral_score = min(projects_referred * 10, 100)  # Cap at 100

# Component 2: Revenue Generation Score
total_revenue = ProjectRevenueTransaction.objects.filter(
    worker=worker
).aggregate(Sum('revenue_amount'))['total'] or 0
revenue_generation_score = min(total_revenue / 10000, 100)  # Cap at 100

# Component 3: Network Expansion Score
# Workers this worker has brought into collaboration network
network_size = WorkerProject.objects.filter(
    referred_by_worker=worker
).values('worker').distinct().count()
network_expansion_score = min(network_size * 5, 100)  # Cap at 100

# Component 4: Tenure Score
from datetime import date
days_active = (date.today() - worker.joined_date).days
months_active = days_active / 30
tenure_score = min(months_active * 2, 100)  # Cap at 100

# Final Score (0-100 scale)
influence_score = (
    project_referral_score * 0.40 +
    revenue_generation_score * 0.30 +
    network_expansion_score * 0.20 +
    tenure_score * 0.10
)
```

**Interpretation:**
- 80-100: Star performer (High influence, prioritize retention)
- 60-79: Strong performer (Valuable, invest in growth)
- 40-59: Average performer (Standard relationship)
- 0-39: Low performer (Risk of churn, needs activation)

---

### 1.2 LOYALTY SCORE

**Purpose:** Measure worker's commitment and referral activity

**Formula:**

```python
# Method 1: Referral-Based (Current Implementation)
total_projects_worked = WorkerProject.objects.filter(worker=worker).count()
projects_referred = Project.objects.filter(referred_by_worker=worker).count()

if total_projects_worked > 0:
    loyalty_score = (projects_referred / total_projects_worked) * 100
else:
    loyalty_score = 0

# Method 2: Enhanced (Recommended)
# Add recency and frequency factors
recent_referrals = Project.objects.filter(
    referred_by_worker=worker,
    created_at__gte=timezone.now() - timedelta(days=180)
).count()

referral_frequency = projects_referred / max(months_active, 1)

loyalty_score = (
    (projects_referred / max(total_projects_worked, 1)) * 50 +  # Ratio component
    min(recent_referrals * 10, 30) +                            # Recency component
    min(referral_frequency * 10, 20)                            # Frequency component
)
```

**Interpretation:**
- 70-100: Highly loyal (Active referrer, engaged)
- 40-69: Moderately loyal (Occasional referrer)
- 0-39: Low loyalty (Passive worker, activation needed)

---

### 1.3 RELIABILITY SCORE (Credit-Based)

**Purpose:** Assess payment reliability and credit risk

**Formula:**

```python
# Get all credit transactions for worker
ledger_entries = WorkerCreditLedger.objects.filter(worker=worker)

total_transactions = ledger_entries.count()
if total_transactions == 0:
    return 100  # New worker, no history = neutral/good

# Calculate settlement metrics
settled_on_time = ledger_entries.filter(
    is_settled=True,
    # Assume we add settled_date field
).count()

overdue_count = ledger_entries.filter(
    is_settled=False,
    due_date__lt=timezone.now().date()
).count()

# Payment consistency
payment_consistency = (settled_on_time / total_transactions) * 100

# Penalty for overdue
overdue_penalty = min(overdue_count * 15, 50)  # Max 50 point penalty

# Current balance factor
current_balance = ledger_entries.order_by('-id').first().running_balance
if current_balance < -50000:
    balance_penalty = 30
elif current_balance < -20000:
    balance_penalty = 15
else:
    balance_penalty = 0

reliability_score = max(
    payment_consistency - overdue_penalty - balance_penalty,
    0
)  # Floor at 0
```

**Interpretation:**
- 85-100: Excellent (Extend credit freely)
- 70-84: Good (Standard credit terms)
- 50-69: Moderate risk (Demand advance payment)
- 0-49: High risk (Cash only or block)

---

### 1.4 AVAILABILITY SCORE

**Purpose:** Determine if worker is available for new assignments

**Formula:**

```python
# Count active assignments (not completed)
active_assignments = WorkerAssignment.objects.filter(
    worker=worker,
    completion_date__isnull=True
).count()

# Base availability score
base_score = 100 - (active_assignments * 25)  # Each assignment reduces by 25

# Adjust for assignment type (if we add urgency tracking)
# High-urgency assignments should impact availability more

availability_score = max(base_score, 0)  # Floor at 0
```

**Business Rules:**

```python
if availability_score >= 75:
    status = "AVAILABLE"  # Prioritize for new assignments
elif availability_score >= 50:
    status = "LIMITED"    # Can take low-urgency assignments
elif availability_score >= 25:
    status = "BUSY"       # Only critical/high-value assignments
else:
    status = "OVERLOADED" # Don't assign new work
```

**Interpretation:**
- 100: Idle (No active assignments)
- 75: Available (1 assignment, can take more)
- 50: Limited (2 assignments, selective)
- 25: Busy (3 assignments, critical only)
- 0: Overloaded (4+ assignments, blocked)

---

## 2. PROJECT OPPORTUNITY CALCULATIONS

### 2.1 REMAINING OPPORTUNITY

**Purpose:** Calculate total revenue still available to capture from a project

**Formula:**

```python
def calculate_remaining_opportunity(project):
    stages = ProjectStage.objects.filter(project=project)
    
    remaining = 0
    for stage in stages:
        stage_remaining = stage.estimated_stage_value - stage.captured_stage_revenue
        remaining += max(stage_remaining, 0)  # Don't count negative (over-captured)
    
    return remaining
```

**Enhanced Version (Stage Decay Factor):**

```python
def calculate_remaining_opportunity_with_decay(project):
    current_stage_seq = project.current_stage.sequence_order
    stages = ProjectStage.objects.filter(project=project).select_related('stage')
    
    remaining = 0
    for ps in stages:
        stage_remaining = ps.estimated_stage_value - ps.captured_stage_revenue
        
        # Apply decay factor for current/near stages
        if ps.stage.sequence_order == current_stage_seq:
            # Current stage: assume 50% captured already (even if not recorded)
            decay_factor = 0.50
        elif ps.stage.sequence_order == current_stage_seq + 1:
            # Next stage: high probability
            decay_factor = 0.90
        elif ps.stage.sequence_order > current_stage_seq:
            # Future stages: full potential
            decay_factor = 1.0
        else:
            # Past stages: zero (already passed)
            decay_factor = 0
        
        remaining += max(stage_remaining * decay_factor, 0)
    
    return remaining
```

---

### 2.2 CAPTURE RATIO

**Purpose:** Measure how much of estimated value has been captured

**Formula:**

```python
# Project-level capture ratio
def calculate_capture_ratio(project):
    total_estimated = project.estimated_total_value
    
    if total_estimated == 0:
        return 0
    
    total_captured = ProjectRevenueTransaction.objects.filter(
        project=project
    ).aggregate(Sum('revenue_amount'))['total'] or 0
    
    return (total_captured / total_estimated) * 100

# Stage-level capture ratio
def calculate_stage_capture_ratio(project_stage):
    if project_stage.estimated_stage_value == 0:
        return 0
    
    return (
        project_stage.captured_stage_revenue / 
        project_stage.estimated_stage_value
    ) * 100
```

**Interpretation:**
- 90-100%: Excellent capture (Won almost everything)
- 70-89%: Good capture (Strong performance)
- 50-69%: Moderate capture (Competitive pressure)
- 30-49%: Poor capture (Lost significant value)
- 0-29%: Failed capture (Project essentially lost)

---

### 2.3 STAGE PRIORITY SCORE

**Purpose:** Rank which stages to focus on for maximum business value

**Formula:**

```python
def calculate_stage_priority(project, stage):
    # Get stage details
    ps = ProjectStage.objects.get(project=project, stage=stage)
    
    # Component 1: Revenue Potential (40%)
    revenue_potential_score = min(ps.estimated_stage_value / 10000, 100)
    
    # Component 2: Margin Percentage (30%)
    margin_score = ps.expected_margin_percentage
    
    # Component 3: Master Margin Priority (20%)
    margin_priority_score = (stage.default_margin_priority / 10) * 100
    
    # Component 4: Capture Status (10%)
    # Higher score if not yet captured (opportunity still available)
    capture_ratio = calculate_stage_capture_ratio(ps)
    capture_opportunity_score = 100 - capture_ratio
    
    priority_score = (
        revenue_potential_score * 0.40 +
        margin_score * 0.30 +
        margin_priority_score * 0.20 +
        capture_opportunity_score * 0.10
    )
    
    return priority_score
```

**Business Application:**
Sort project stages by priority_score DESC → Focus on high-value, high-margin, uncaptured stages first.

---

## 3. REVENUE OPTIMIZATION RULES

### 3.1 MARGIN PRIORITIZATION

**Decision Table:**

| Stage               | Typical Margin | Priority | Strategy                          |
|---------------------|----------------|----------|-----------------------------------|
| FOUNDATION          | 8-12%          | 2        | Win only if full project          |
| CIVIL/STRUCTURE     | 10-15%         | 3        | Competitive bidding               |
| ELECTRICAL          | 25-30%         | 9        | High focus, premium positioning   |
| PLUMBING            | 20-25%         | 8        | Strong focus, worker network key  |
| TILES/FLOORING      | 22-28%         | 8        | Partner with tile workers         |
| SANITARY/FIXTURES   | 25-30%         | 9        | Premium brands, high margin       |
| PAINTING            | 30-35%         | 10       | Always pursue, easy to capture    |
| FINAL FINISHING     | 35-40%         | 10       | Highest margin, minimal cost      |

**Implementation:**

```python
# Stored in ConstructionStage.default_margin_priority (1-10 scale)

def get_high_margin_stages():
    return ConstructionStage.objects.filter(
        default_margin_priority__gte=8,
        is_active=True
    ).order_by('-default_margin_priority')

def should_pursue_stage(stage, project_details):
    if stage.default_margin_priority >= 8:
        return True, "High margin stage"
    
    # Even low-margin stages acceptable if:
    # - Project is brand new (win full lifecycle)
    # - Project value very high (volume compensates)
    if project_details['is_new_project']:
        return True, "New project - capture full lifecycle"
    
    if project_details['estimated_total_value'] > 5000000:  # 50 lakh
        return True, "High-value project - volume compensates"
    
    return False, "Low margin, not strategic"
```

---

### 3.2 CROSS-SELL TRIGGER RULES

**Trigger Logic:**

```python
def generate_cross_sell_recommendations(revenue_transaction):
    suggestions = []
    
    stage_code = revenue_transaction.stage.code
    revenue = revenue_transaction.revenue_amount
    project = revenue_transaction.project
    
    # Rule 1: Electrical stage → Automation upsell
    if stage_code == 'ELECTRICAL' and revenue > 300000:
        suggestions.append({
            'product_category': 'HOME_AUTOMATION',
            'reason': 'High-value electrical project',
            'estimated_value': revenue * 0.15,
            'probability': 0.60
        })
    
    # Rule 2: Plumbing done → Water treatment
    if stage_code == 'PLUMBING' and project.city in ['BANGALORE', 'CHENNAI']:
        suggestions.append({
            'product_category': 'WATER_FILTRATION',
            'reason': 'Hard water area - filtration need',
            'estimated_value': 80000,
            'probability': 0.45
        })
    
    # Rule 3: Tiles → Grouting & sealants
    if stage_code == 'TILES':
        suggestions.append({
            'product_category': 'PREMIUM_GROUTING',
            'reason': 'Always needed with tiles',
            'estimated_value': revenue * 0.08,
            'probability': 0.75
        })
    
    # Rule 4: Painting → Hardware/Fixtures
    if stage_code == 'PAINTING':
        suggestions.append({
            'product_category': 'DOOR_HARDWARE',
            'reason': 'Final finishing items',
            'estimated_value': 120000,
            'probability': 0.50
        })
    
    return suggestions
```

---

### 3.3 BUNDLE SUGGESTION ENGINE

**Business Logic:**

```python
def suggest_stage_bundle(project_stage):
    stage_code = project_stage.stage.code
    estimated_value = project_stage.estimated_stage_value
    
    bundles = {
        'ELECTRICAL': {
            'name': 'Complete Electrical Package',
            'components': [
                'Wiring (copper)',
                'Switches & Sockets (premium)',
                'LED lighting fixtures',
                'MCB/distribution board'
            ],
            'discount_percentage': 8,
            'minimum_order_value': 200000,
            'margin_impact': 'Neutral'  # Volume compensates discount
        },
        
        'PLUMBING': {
            'name': 'Full Plumbing Solution',
            'components': [
                'CPVC pipes & fittings',
                'Sanitary fixtures (washbasin, WC)',
                'Taps & accessories',
                'Water heater'
            ],
            'discount_percentage': 6,
            'minimum_order_value': 150000,
            'margin_impact': 'Positive'  # Lock-in fixtures (high margin)
        },
        
        'FINISHING': {
            'name': 'Premium Finishing Bundle',
            'components': [
                'Premium emulsion paint',
                'Door hardware (handles, locks)',
                'Mirrors',
                'Curtain rods'
            ],
            'discount_percentage': 5,
            'minimum_order_value': 100000,
            'margin_impact': 'High'  # All high-margin items
        }
    }
    
    if stage_code in bundles and estimated_value >= bundles[stage_code]['minimum_order_value']:
        return bundles[stage_code]
    
    return None
```

---

## 4. WORKER MATCHING ALGORITHMS

### 4.1 REQUIREMENT MATCHING SCORE

**Purpose:** Find best worker for a requirement

**Formula:**

```python
def calculate_match_score(worker, requirement):
    scores = {}
    
    # Component 1: Role Match (40%)
    if worker.role == requirement.role:
        scores['role'] = 100
    else:
        scores['role'] = 0
    
    # Component 2: Location Match (30%)
    if worker.primary_pincode == requirement.project.pincode:
        scores['location'] = 100
    else:
        # Could enhance with geodistance calculation
        scores['location'] = 0
    
    # Component 3: Availability (20%)
    availability = calculate_availability_score(worker)
    scores['availability'] = availability
    
    # Component 4: Past Performance (10%)
    worker_revenue = calculate_worker_revenue(worker)
    if worker_revenue > 500000:
        scores['performance'] = 100
    elif worker_revenue > 200000:
        scores['performance'] = 70
    elif worker_revenue > 50000:
        scores['performance'] = 40
    else:
        scores['performance'] = 20
    
    match_score = (
        scores['role'] * 0.40 +
        scores['location'] * 0.30 +
        scores['availability'] * 0.20 +
        scores['performance'] * 0.10
    )
    
    return match_score, scores  # Return breakdown for transparency
```

**Usage:**

```python
def find_best_workers(requirement, limit=5):
    candidates = Worker.objects.filter(
        role=requirement.role,
        active_status=True
    )
    
    scored_workers = []
    for worker in candidates:
        score, breakdown = calculate_match_score(worker, requirement)
        scored_workers.append({
            'worker': worker,
            'score': score,
            'breakdown': breakdown
        })
    
    # Sort by score DESC
    scored_workers.sort(key=lambda x: x['score'], reverse=True)
    
    return scored_workers[:limit]
```

---

### 4.2 COLLABORATION STRENGTH

**Purpose:** Identify workers who work well together

**Formula:**

```python
def calculate_collaboration_strength(worker_a, worker_b):
    # Count projects worked together
    projects_together = WorkerProject.objects.filter(
        project_id__in=WorkerProject.objects.filter(
            worker=worker_a
        ).values_list('project_id', flat=True)
    ).filter(
        worker=worker_b
    ).count()
    
    if projects_together == 0:
        return 0
    
    # Get revenue from those projects
    common_project_ids = WorkerProject.objects.filter(
        worker=worker_a
    ).values_list('project_id', flat=True).intersection(
        WorkerProject.objects.filter(
            worker=worker_b
        ).values_list('project_id', flat=True)
    )
    
    joint_revenue = ProjectRevenueTransaction.objects.filter(
        project_id__in=common_project_ids
    ).aggregate(Sum('revenue_amount'))['total'] or 0
    
    # Strength = frequency + success
    collaboration_strength = (
        min(projects_together * 15, 60) +  # Frequency component (max 60)
        min(joint_revenue / 10000, 40)     # Success component (max 40)
    )
    
    return min(collaboration_strength, 100)
```

**Business Application:**

```python
def suggest_worker_pairs(role_a, role_b, pincode):
    # Find pairs who have high collaboration strength
    # Example: Electrician + Plumber pairs that work well together
    
    workers_a = Worker.objects.filter(role__code=role_a, primary_pincode=pincode)
    workers_b = Worker.objects.filter(role__code=role_b, primary_pincode=pincode)
    
    strong_pairs = []
    for wa in workers_a:
        for wb in workers_b:
            strength = calculate_collaboration_strength(wa, wb)
            if strength > 50:
                strong_pairs.append({
                    'worker_a': wa,
                    'worker_b': wb,
                    'strength': strength
                })
    
    return sorted(strong_pairs, key=lambda x: x['strength'], reverse=True)
```

---

## 5. RISK & CREDIT INTELLIGENCE

### 5.1 RISK FLAG AUTOMATION

**Rules Engine:**

```python
def assess_worker_risk(worker):
    risk_flags = []
    risk_level = "GREEN"
    
    # Get latest ledger entry
    latest_ledger = WorkerCreditLedger.objects.filter(
        worker=worker
    ).order_by('-id').first()
    
    if not latest_ledger:
        return "GREEN", []  # No history = no risk
    
    current_balance = latest_ledger.running_balance
    
    # Rule 1: High Outstanding Balance
    if current_balance < -50000:
        risk_flags.append("CRITICAL_DEBT: Outstanding > 50K")
        risk_level = "RED"
    elif current_balance < -20000:
        risk_flags.append("MODERATE_DEBT: Outstanding > 20K")
        if risk_level != "RED":
            risk_level = "YELLOW"
    
    # Rule 2: Overdue Payments
    overdue_count = WorkerCreditLedger.objects.filter(
        worker=worker,
        is_settled=False,
        due_date__lt=timezone.now().date()
    ).count()
    
    if overdue_count > 3:
        risk_flags.append(f"MULTIPLE_OVERDUE: {overdue_count} payments overdue")
        risk_level = "RED"
    elif overdue_count > 0:
        risk_flags.append(f"OVERDUE_PAYMENT: {overdue_count} payment(s) overdue")
        if risk_level == "GREEN":
            risk_level = "YELLOW"
    
    # Rule 3: No Payment Activity (Dormant Debt)
    last_transaction_date = latest_ledger.created_at
    days_since_last_activity = (timezone.now() - last_transaction_date).days
    
    if days_since_last_activity > 90 and current_balance < 0:
        risk_flags.append("DORMANT_DEBT: No activity in 90+ days with outstanding")
        risk_level = "RED"
    
    # Rule 4: Reliability Score Check
    reliability = calculate_reliability_score(worker)
    if reliability < 50:
        risk_flags.append(f"LOW_RELIABILITY: Score {reliability}/100")
        if risk_level == "GREEN":
            risk_level = "YELLOW"
    
    return risk_level, risk_flags
```

**Action Rules:**

```python
def get_credit_action(risk_level, worker):
    actions = {
        'GREEN': {
            'credit_allowed': True,
            'max_credit_limit': 100000,
            'payment_terms': 'NET_30',
            'message': 'Good standing - standard credit'
        },
        'YELLOW': {
            'credit_allowed': True,
            'max_credit_limit': 30000,
            'payment_terms': 'NET_15',
            'message': 'Moderate risk - reduced credit limit'
        },
        'RED': {
            'credit_allowed': False,
            'max_credit_limit': 0,
            'payment_terms': 'ADVANCE_ONLY',
            'message': 'High risk - cash/advance payment required'
        }
    }
    
    return actions[risk_level]
```

---

### 5.2 OUTSTANDING TRACKING & ALERTS

**Alert Triggers:**

```python
def generate_credit_alerts():
    alerts = []
    
    # Alert Type 1: High-Value Outstanding
    high_value_outstanding = WorkerCreditLedger.objects.filter(
        is_settled=False
    ).values('worker').annotate(
        outstanding=Sum(F('debit') - F('credit'))
    ).filter(
        outstanding__gt=50000
    )
    
    for entry in high_value_outstanding:
        alerts.append({
            'type': 'HIGH_OUTSTANDING',
            'worker_id': entry['worker'],
            'amount': entry['outstanding'],
            'priority': 'HIGH',
            'action': 'Call for payment plan'
        })
    
    # Alert Type 2: Due Date Approaching (7 days)
    upcoming_due = WorkerCreditLedger.objects.filter(
        is_settled=False,
        due_date__range=[
            timezone.now().date(),
            timezone.now().date() + timedelta(days=7)
        ]
    )
    
    for ledger in upcoming_due:
        alerts.append({
            'type': 'DUE_SOON',
            'worker_id': ledger.worker_id,
            'amount': ledger.debit - ledger.credit,
            'due_date': ledger.due_date,
            'priority': 'MEDIUM',
            'action': 'Send payment reminder'
        })
    
    # Alert Type 3: Overdue (Past Due Date)
    overdue = WorkerCreditLedger.objects.filter(
        is_settled=False,
        due_date__lt=timezone.now().date()
    )
    
    for ledger in overdue:
        days_overdue = (timezone.now().date() - ledger.due_date).days
        alerts.append({
            'type': 'OVERDUE',
            'worker_id': ledger.worker_id,
            'amount': ledger.debit - ledger.credit,
            'days_overdue': days_overdue,
            'priority': 'CRITICAL',
            'action': 'Immediate collection action'
        })
    
    return alerts
```

---

## 6. ANALYTICS CALCULATIONS

### 6.1 PARETO ANALYSIS (80/20 RULE)

**Purpose:** Identify top 20% workers generating 80% revenue

**Algorithm:**

```python
def perform_pareto_analysis():
    # Get all workers with their total revenue
    worker_revenue = []
    workers = Worker.objects.all()
    
    for worker in workers:
        revenue = calculate_worker_revenue(worker)
        if revenue > 0:
            worker_revenue.append({
                'worker': worker,
                'revenue': revenue
            })
    
    # Sort by revenue DESC
    worker_revenue.sort(key=lambda x: x['revenue'], reverse=True)
    
    # Calculate cumulative revenue
    total_revenue = sum(wr['revenue'] for wr in worker_revenue)
    target_revenue = total_revenue * 0.80
    
    cumulative = 0
    top_workers = []
    
    for wr in worker_revenue:
        cumulative += wr['revenue']
        top_workers.append(wr)
        
        if cumulative >= target_revenue:
            break
    
    pareto_percentage = (len(top_workers) / len(worker_revenue)) * 100
    
    return {
        'top_workers': top_workers,
        'count': len(top_workers),
        'percentage': pareto_percentage,
        'revenue_generated': cumulative,
        'total_revenue': total_revenue
    }
```

---

### 6.2 STAGE DROP-OFF RATE

**Purpose:** Identify which stages have highest loss rate

**Formula:**

```python
def calculate_stage_dropoff():
    stages = ConstructionStage.objects.all()
    dropoff_data = []
    
    for stage in stages:
        # Projects that reached this stage
        projects_entered = ProjectStage.objects.filter(
            stage=stage
        ).values('project').distinct().count()
        
        # Projects that actually captured revenue at this stage
        projects_captured = ProjectStage.objects.filter(
            stage=stage,
            captured_stage_revenue__gt=0
        ).values('project').distinct().count()
        
        # Projects that completely lost this stage (entered but zero revenue)
        projects_lost = projects_entered - projects_captured
        
        if projects_entered > 0:
            dropoff_rate = (projects_lost / projects_entered) * 100
        else:
            dropoff_rate = 0
        
        dropoff_data.append({
            'stage': stage.name,
            'projects_entered': projects_entered,
            'projects_captured': projects_captured,
            'projects_lost': projects_lost,
            'dropoff_rate': dropoff_rate
        })
    
    return sorted(dropoff_data, key=lambda x: x['dropoff_rate'], reverse=True)
```

**Insight:** Stages with >50% dropoff rate → Pricing issue or competitive weakness

---

### 6.3 WORKER ASSIGNMENT IMPACT ON WIN RATE

**Purpose:** Prove that worker network drives conversions

**Analysis:**

```python
def analyze_worker_assignment_impact():
    # Get all projects with final status (won/lost)
    projects = Project.objects.filter(
        lead_status__is_final=True
    )
    
    # Segment 1: Projects WITH worker assignments
    projects_with_workers = projects.filter(
        worker_requirements__assignment__isnull=False
    ).distinct()
    
    won_with_workers = projects_with_workers.filter(
        lead_status__is_won=True
    ).count()
    
    total_with_workers = projects_with_workers.count()
    
    # Segment 2: Projects WITHOUT worker assignments
    projects_without_workers = projects.exclude(
        id__in=projects_with_workers.values_list('id', flat=True)
    )
    
    won_without_workers = projects_without_workers.filter(
        lead_status__is_won=True
    ).count()
    
    total_without_workers = projects_without_workers.count()
    
    # Calculate win rates
    win_rate_with_workers = (
        (won_with_workers / total_with_workers * 100) 
        if total_with_workers > 0 else 0
    )
    
    win_rate_without_workers = (
        (won_without_workers / total_without_workers * 100) 
        if total_without_workers > 0 else 0
    )
    
    impact = win_rate_with_workers - win_rate_without_workers
    
    return {
        'with_workers': {
            'total': total_with_workers,
            'won': won_with_workers,
            'win_rate': win_rate_with_workers
        },
        'without_workers': {
            'total': total_without_workers,
            'won': won_without_workers,
            'win_rate': win_rate_without_workers
        },
        'impact': impact,
        'conclusion': (
            f"Worker assignments {'increase' if impact > 0 else 'decrease'} "
            f"win rate by {abs(impact):.1f} percentage points"
        )
    }
```

---

## 7. IMPLEMENTATION PRIORITY

### HIGH PRIORITY (Implement First)
1. ✅ Influence Score (Worker value assessment)
2. ✅ Reliability Score (Credit risk management)
3. ✅ Remaining Opportunity (Deal sizing)
4. ✅ Risk Flag Automation (Protect business)
5. ✅ Match Score (Worker assignment optimization)

### MEDIUM PRIORITY (Next Phase)
6. ⏳ Stage Priority Score (Focus optimization)
7. ⏳ Cross-Sell Rules (Revenue expansion)
8. ⏳ Bundle Suggestion (Deal sweetening)
9. ⏳ Collaboration Strength (Network intelligence)
10. ⏳ Pareto Analysis (Focus management)

### LOWER PRIORITY (Enhancement)
11. 🔄 Availability Score with ML (Prediction-based)
12. 🔄 Stage Drop-off with Cohort Analysis
13. 🔄 Predictive Lead Scoring
14. 🔄 Referral Chain Visualization

---

**Document Status:** Business Intelligence Specifications Complete  
**Usage:** Implementation reference for service layer development  
**Next Step:** Implement formulas in objectbank/services/ modules
