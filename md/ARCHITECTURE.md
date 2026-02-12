# AG-PROJ01: CONSTRUCTION SUPPLY ECOSYSTEM
## Business Architecture & Intelligence Design

**Date:** February 12, 2026  
**System Type:** Website-Only, Single-Shop, Stage-Based Opportunity Engine  
**Core Entities:** Projects (Leads/Sites) + Workers (Network)

---

## 1️⃣ DATABASE ARCHITECTURE ANALYSIS

### ✅ CURRENT SCHEMA (IMPLEMENTED)

Your database is **well-structured** and **business-focused**. Here's the entity relationship breakdown:

#### **MASTER DATA LAYER**
```
ConstructionStage
├─ code, name, sequence_order
├─ default_margin_priority ← Revenue optimization
└─ Drives: Stage-based entry logic

LeadStatus
├─ code, name, sequence_order
├─ is_final, is_won, is_lost ← Funnel tracking
└─ Separated from construction stage ✓

WorkerRole (Electrician, Plumber, etc.)
RequirementStatus (Open, Assigned, Closed)
UrgencyLevel (High, Medium, Low with priority_score)
CreditTransactionType (Payment, Refund, etc.)
```

#### **PROJECT CORE**
```
Project (Central Hub)
├─ project_code ← Unique identifier
├─ Location: full_address, pincode, city, lat/long ✓
├─ Client: client_name, phone
├─ Financial: estimated_total_value
├─ FK: current_stage → ConstructionStage
├─ FK: lead_status → LeadStatus
├─ FK: referred_by_worker → Worker (Referral tracking)
└─ expected_completion_date

ProjectStage (Stage Breakdown)
├─ FK: project, stage
├─ estimated_stage_value ← Opportunity per stage
├─ captured_stage_revenue ← What you won
├─ expected_margin_percentage
└─ is_completed, completed_date
```

**INTELLIGENCE:** This enables mid-stage entry and remaining opportunity calculation.

#### **WORKER CORE**
```
Worker
├─ worker_code
├─ name, phone
├─ FK: role → WorkerRole
├─ primary_pincode ← Geo-matching
├─ joined_date, active_status

WorkerProject (Collaboration Mapping)
├─ FK: worker, project
├─ FK: role (role in THIS project)
├─ revenue_generated ← Direct impact tracking
├─ FK: referred_by_worker → Worker (Network graph)
└─ Unique(worker, project)
```

**INTELLIGENCE:** Supports many-to-many worker-project relationships + referral chains.

#### **WORKER DEMAND ENGINE**
```
ProjectWorkerRequirement (Demand)
├─ FK: project, role, urgency, status
└─ required_from_date

WorkerAssignment (Supply Match)
├─ FK: requirement (OneToOne)
├─ FK: worker
├─ assigned_date, completion_date
└─ revenue_impact
```

**SEPARATION:** ✓ Demand engine separate from collaboration tracking (as required)

#### **REVENUE ENGINE**
```
ProjectRevenueTransaction
├─ FK: project, stage, worker
├─ invoice_number
├─ revenue_amount, cost_amount, margin_amount
└─ transaction_date (indexed)
```

**INTELLIGENCE:** Links revenue to project, stage, and worker simultaneously.

#### **CREDIT CONTROL**
```
WorkerCreditLedger
├─ FK: worker, project, transaction_type
├─ debit, credit, running_balance
├─ due_date, is_settled
└─ Ledger pattern ✓
```

---

## 2️⃣ STAGE-BASED OPPORTUNITY ENGINE

### ✅ WHAT YOU HAVE
- `ConstructionStage.sequence_order` → Stage progression
- `ProjectStage` per project → Stage-wise value estimation
- `calculate_remaining_opportunity()` → Sum(estimated - captured)

### 🔥 BUSINESS LOGIC SPECIFICATIONS

#### **A. CURRENT STAGE DETECTION**
```
Logic: Project.current_stage
Rule: Update when ProjectStage.is_completed = True for a stage
Trigger: Revenue capture or manual update
```

#### **B. PREDICT REMAINING STAGES**
```
Algorithm:
  1. Get Project.current_stage.sequence_order
  2. Fetch all ConstructionStage WHERE sequence_order > current
  3. Return List[Stage] ordered by sequence_order
  
Use Case: Show client "What's left in your project"
```

#### **C. ESTIMATE REMAINING REVENUE**
```
Current Implementation: ✓ Already done
Formula: SUM(estimated_stage_value - captured_stage_revenue)
Enhancement Opportunity: Add stage-based decay factor
  → If stage passed 50%, reduce remaining estimate by 20%
```

#### **D. STAGE-BASED BUNDLE SUGGESTIONS**
```
Business Rule Logic:

IF current_stage = "CIVIL":
  → Suggest Bundle: [Electrical Materials + Plumbing Materials]
  → Discount: 8%
  
IF current_stage = "ELECTRICAL":
  → Suggest Bundle: [Tile + Sanitary + Paint]
  → Discount: 5%

IF current_stage = "FINAL_FINISHING":
  → Suggest: [Premium Paint + Hardware + Lighting]
  → Margin: High (last-mile capture)

Storage: Create `StageBundle` model or rules engine table
```

#### **E. REVENUE CAPTURE RATIO**
```
Current Implementation: ✓ calculate_capture_ratio() exists
Formula: (Captured Revenue / Estimated Total) * 100
Enhancement: Calculate per-stage capture ratio
  → Which stages have highest leakage?
```

#### **F. PARTIAL ENTRY LOGIC**
```
Scenario: Project started at "CIVIL" but you enter at "ELECTRICAL"

Rules:
1. Mark previous stages (FOUNDATION, CIVIL) as is_completed=True
2. Set captured_stage_revenue = 0 for past stages
3. Focus opportunity calculation on current + remaining stages
4. Flag: "late_entry_project" for reporting

Query Pattern:
  WHERE lead_status = "WON"
  AND SUM(past_stages.captured_revenue) = 0
  → These are late-entry wins
```

---

## 3️⃣ WORKER DEMAND & ASSIGNMENT ARCHITECTURE

### ✅ CURRENT SCHEMA: WELL-DESIGNED
```
ProjectWorkerRequirement → WorkerAssignment (One-To-One)
```

### 🔥 BUSINESS LOGIC ENHANCEMENTS

#### **A. WORKER AVAILABILITY LOGIC**
```
Current: match_workers() in assignment_service.py
  → Filters by: role, pincode, active_status

Enhancement: Availability Scoring
  active_projects = Worker.assignments.filter(completion_date__isnull=True).count()
  
  availability_score = 100 - (active_projects * 20)
  
  IF availability_score < 40:
    → Mark worker as "overloaded"
    → Don't suggest for new assignments
```

#### **B. PINCODE MATCHING INTELLIGENCE**
```
Current: Exact match (primary_pincode = project.pincode)

Enhancement: Radius Matching
  1. Try exact pincode match first
  2. If no workers: Expand to neighboring pincodes (geodistance)
  3. Use lat/long for radius-based search (10km, 20km)
  
  Requires: Pincode master table with lat/long OR real-time geo calculation
```

#### **C. DEMAND ANALYTICS**
```
Query Patterns Needed:

1. Most In-Demand Role Per Pincode:
   ProjectWorkerRequirement
   GROUP BY role_id, project__pincode
   COUNT(*)
   → Where to recruit next?

2. Unfilled Requirements (Supply Gap):
   SELECT * FROM ProjectWorkerRequirement
   WHERE status != "ASSIGNED"
   AND required_from_date < TODAY + 7 days
   → Urgency alerts

3. Average Time To Assign:
   AVG(WorkerAssignment.assigned_date - Requirement.created_at)
   → System efficiency metric
```

#### **D. ASSIGNMENT IMPACT TRACKING**
```
Current: WorkerAssignment.revenue_impact (field exists but calculation missing)

Logic:
  When assignment.completion_date is set:
    1. Get all ProjectRevenueTransaction for that project
       WHERE transaction_date BETWEEN assigned_date AND completion_date
       AND stage = project.current_stage during assignment
    
    2. SUM(revenue_amount) → Attribute to assignment.revenue_impact
    
  Use Case: "Workers who drive revenue vs. those who don't"
```

---

## 4️⃣ WORKER NETWORK INTELLIGENCE

### ✅ CURRENT FOUNDATION
- `Project.referred_by_worker` → Project referral source
- `WorkerProject.referred_by_worker` → Network linkage
- `calculate_loyalty_score()` → Basic loyalty calculation

### 🔥 INTELLIGENCE MODEL SPECIFICATIONS

#### **A. INFLUENCE SCORE FORMULA**
```
Components:
  1. Projects Referred (Weight: 40%)
  2. Direct Revenue Generated (Weight: 30%)
  3. Network Size (Workers referred) (Weight: 20%)
  4. Tenure (joined_date) (Weight: 10%)

Calculation:
  projects_referred_score = Worker.referred_projects.count() * 10
  revenue_score = calculate_worker_revenue(worker) / 10000
  network_score = WorkerProject.filter(referred_by_worker=worker).count() * 5
  tenure_score = (today - worker.joined_date).days / 30  # months
  
  influence_score = (
    projects_referred_score * 0.4 +
    revenue_score * 0.3 +
    network_score * 0.2 +
    tenure_score * 0.1
  )

Storage: Compute on-demand OR cache in Worker model (influence_score field)
```

#### **B. COLLABORATION STRENGTH**
```
Scenario: Worker A and Worker B worked together on multiple projects

Query Pattern:
  SELECT wp1.worker_id as worker_a, wp2.worker_id as worker_b, COUNT(*) as projects_together
  FROM WorkerProject wp1
  JOIN WorkerProject wp2 ON wp1.project_id = wp2.project_id
  WHERE wp1.worker_id < wp2.worker_id  -- Avoid duplicates
  GROUP BY wp1.worker_id, wp2.worker_id
  HAVING COUNT(*) > 2
  
Use Case:
  - Suggest worker pairs for complex projects
  - Understand network clusters
```

#### **C. REFERRAL CHAIN DEPTH**
```
Scenario: Worker A referred Worker B, who referred Worker C

Graph Traversal:
  WorkerProject.referred_by_worker forms a directed graph
  
  Depth-1: Direct referrals
  Depth-2: Referrals of referrals
  
Analytics:
  - Who has the deepest network?
  - Multi-level referral attribution
```

#### **D. REVENUE PER WORKER (DIRECT VS NETWORK)**
```
Direct Revenue:
  SUM(ProjectRevenueTransaction.revenue_amount WHERE worker=X)

Network Revenue (Attributed):
  Projects referred by worker X:
    SUM(revenue) for Projects WHERE referred_by_worker=X
    
  Workers referred by worker X in collaborations:
    SUM(revenue) for Projects linked to workers in referral chain

Metric: "Direct Impact" vs "Network Leverage"
```

---

## 5️⃣ REVENUE OPTIMIZATION FRAMEWORK

### ✅ CURRENT IMPLEMENTATION
- `ProjectRevenueTransaction` captures: revenue, cost, margin
- `ConstructionStage.default_margin_priority` exists
- `record_transaction()` updates stage captured revenue

### 🔥 OPTIMIZATION INTELLIGENCE

#### **A. MARGIN PRIORITIZATION**
```
Rule Engine:

High-Margin Stages:
  - FINAL_FINISHING (Paint, Hardware): Margin 35-40%
  - ELECTRICAL (Automation, Premium): Margin 30%
  - SANITARY (Branded): Margin 25%

Low-Margin Stages:
  - FOUNDATION (Cement, Steel): Margin 8-12%
  - CIVIL (Structure): Margin 10-15%

Strategy:
  Focus on winning HIGH-MARGIN stages even if late entry
  Accept FOUNDATION/CIVIL loss if you can win ELECTRICAL onwards

Implementation:
  Stage scoring: ConstructionStage.default_margin_priority
  Lead prioritization: Sort opportunities by stage margin potential
```

#### **B. CROSS-SELL LOGIC**
```
Trigger: When ProjectRevenueTransaction is recorded for a stage

Cross-Sell Rules:
  IF stage = "ELECTRICAL" AND revenue > 500K:
    → Suggest: Automation Bundle, Smart Lighting
    
  IF stage = "PLUMBING" AND project.city = "Premium Area":
    → Suggest: Water Filtration, Premium Fittings
    
  IF stage = "TILES" AND captured_revenue > estimated * 0.8:
    → Client is buying more than expected
    → Upsell: Designer tiles, Epoxy grouting
```

#### **C. LATE-ENTRY REVENUE RECOVERY**
```
Scenario: Project entered at ELECTRICAL, lost FOUNDATION/CIVIL

Recovery Strategy:
  1. Mark lost stages revenue as "unrecoverable"
  2. Calculate "recoverable opportunity" = remaining stages only
  3. Apply aggressive margin to compensate
  4. Bundle remaining stages for higher capture
  
Metric: Late Entry Recovery Rate
  = (Captured from remaining stages) / (Estimated for remaining stages)
```

#### **D. REVENUE LEAKAGE DETECTION**
```
Leakage Patterns:

1. Stage Completed But Zero Revenue:
   ProjectStage WHERE is_completed=True AND captured_stage_revenue=0
   → Client bought elsewhere

2. Estimated vs Captured Gap > 30%:
   IF (estimated_stage_value - captured_stage_revenue) / estimated > 0.3
   → Price competition or requirement mismatch

3. Project WON But No Transactions > 30 Days:
   Project WHERE lead_status="WON" 
   AND NOT EXISTS (revenue_transactions in last 30 days)
   → Deal slipping
```

---

## 6️⃣ CREDIT & RISK CONTROL

### ✅ CURRENT IMPLEMENTATION
- `WorkerCreditLedger` with running balance ✓
- `record_credit()` with ledger pattern ✓

### 🔥 RISK INTELLIGENCE

#### **A. PAYMENT RELIABILITY SCORE**
```
Formula:
  total_credit = SUM(credit) for worker
  total_debit = SUM(debit) for worker
  settled_on_time = COUNT(*) WHERE is_settled=True AND settled_date <= due_date
  total_transactions = COUNT(*)
  
  reliability_score = (settled_on_time / total_transactions) * 100
  
  IF reliability_score > 90: → Excellent, extend credit
  IF reliability_score < 50: → Risk, demand advance payment
```

#### **B. RISK FLAGGING RULES**
```
Auto-Flag Logic:

RED FLAG (High Risk):
  - running_balance < -50000 (50K debt)
  - overdue_payments > 3
  - Last payment > 90 days ago

YELLOW FLAG (Watch):
  - running_balance < -20000
  - overdue_payments = 1-2
  
GREEN (Healthy):
  - running_balance >= 0
  - No overdue
```

#### **C. OUTSTANDING TRACKING**
```
Query Pattern:
  SELECT worker_id, SUM(debit - credit) as outstanding
  FROM WorkerCreditLedger
  WHERE is_settled = False
  GROUP BY worker_id
  HAVING outstanding > 0
  ORDER BY outstanding DESC
  
Alerts:
  - Weekly report: Workers with outstanding > 30K
  - Due date approaching: 7 days before due_date
```

#### **D. LOSS-STAGE ANALYSIS**
```
Correlation Analysis:

Question: Do workers with high credit defaults correlate with lost projects?

Query:
  Workers with overdue credits → Get their Projects
  Check lead_status = "LOST" percentage
  
  IF worker_credit_issues ← Predict project risk
  → Assign better workers OR demand advance payment
```

---

## 7️⃣ ANALYTICS BLUEPRINT

### 🔥 CORE METRICS (Query Specifications)

#### **A. REVENUE PER STAGE**
```
Query:
  SELECT stage.name, SUM(revenue_amount) as total
  FROM ProjectRevenueTransaction
  JOIN ConstructionStage stage ON transaction.stage_id = stage.id
  GROUP BY stage.id, stage.name
  ORDER BY total DESC

Insight: Which stages generate most revenue?
```

#### **B. REVENUE PER WORKER**
```
Current: ✓ calculate_worker_revenue() exists

Enhancement:
  Add ranking: Top 10, Top 20%, Bottom 50%
  Add trend: Revenue per worker per quarter
```

#### **C. REVENUE PER PINCODE**
```
Current: ✓ revenue_per_pincode() exists

Enhancement:
  1. Pincode Heatmap: Which areas are goldmines?
  2. Expansion Priority: Pincodes with high revenue + low worker density
  3. Cluster Analysis: Group nearby pincodes
```

#### **D. STAGE DROP-OFF RATE**
```
Query:
  SELECT stage.name,
    COUNT(DISTINCT ps.project_id) as projects_entered,
    COUNT(DISTINCT CASE WHEN ps.captured_stage_revenue > 0 THEN ps.project_id END) as projects_captured
  FROM ProjectStage ps
  JOIN ConstructionStage stage ON ps.stage_id = stage.id
  GROUP BY stage.id, stage.name

Formula: Drop-off = (projects_entered - projects_captured) / projects_entered * 100

Insight: Which stages have highest loss rate?
```

#### **E. OPPORTUNITY CAPTURE RATIO**
```
Current: ✓ calculate_capture_ratio() per project

Enhancement: System-Wide Capture Ratio
  total_estimated = SUM(Project.estimated_total_value WHERE lead_status="WON")
  total_captured = SUM(ProjectRevenueTransaction.revenue_amount)
  
  capture_ratio = total_captured / total_estimated * 100
  
Target: > 70% capture ratio
```

#### **F. TIME-TO-CLOSE**
```
Query:
  SELECT AVG(
    DATEDIFF(
      (SELECT MAX(created_at) FROM LeadStatus WHERE is_final=True),
      project.created_at
    )
  ) as avg_days_to_close
  FROM Project
  WHERE lead_status IN (SELECT id FROM LeadStatus WHERE is_final=True)

Insight: Sales cycle efficiency
```

#### **G. TOP 20% WORKERS GENERATING 80% REVENUE (PARETO)**
```
Implementation:
  1. Calculate revenue per worker (already exists)
  2. Sort DESC
  3. Running total until 80% of total revenue
  4. Count workers needed → That's your top X%

Use Case:
  - Identify stars
  - Focus retention efforts
  - Model hiring profile
```

#### **H. WORKER ASSIGNMENT IMPACT ON WIN RATE**
```
Hypothesis: Projects with worker assignments have higher win rate

Query:
  WITH assigned_projects AS (
    SELECT DISTINCT project_id FROM ProjectWorkerRequirement
    WHERE status = "ASSIGNED"
  )
  SELECT 
    CASE WHEN p.id IN assigned_projects THEN 'With Worker' ELSE 'No Worker' END as category,
    COUNT(*) as total_projects,
    SUM(CASE WHEN ls.is_won = True THEN 1 ELSE 0 END) as won_projects,
    (SUM(CASE WHEN ls.is_won = True THEN 1 ELSE 0 END) * 100.0 / COUNT(*)) as win_rate
  FROM Project p
  JOIN LeadStatus ls ON p.lead_status_id = ls.id
  GROUP BY category

Insight: Does worker network drive conversions?
```

---

## 8️⃣ CURRENT GAP ANALYSIS

### ✅ STRONGLY IMPLEMENTED
- Database schema (normalized, business-focused)
- Location tracking (pincode, lat/long)
- Stage separation from lead status
- Worker demand engine (separate from collaboration)
- Revenue, credit, referral tracking

### 🔶 PARTIALLY IMPLEMENTED (Logic Exists, Needs Enhancement)
- Loyalty score (basic formula exists)
- Revenue per worker (exists)
- Capture ratio (exists)
- Pincode analytics (basic query exists)

### ❌ NEEDS IMPLEMENTATION (Intelligence Layer)
- Influence score calculation
- Stage-based bundle engine
- Margin prioritization rules
- Cross-sell trigger system
- Risk flagging automation
- Availability scoring
- Referral chain depth analysis
- Leakage detection alerts
- Pareto analysis dashboard
- Worker assignment impact correlation

---

## 9️⃣ EXECUTION ROADMAP (INTELLIGENCE LAYER)

### **PHASE 1: CORE INTELLIGENCE (Weeks 1-2)**
1. Implement influence_score calculation in worker_service.py
2. Build stage-based opportunity predictor in project_service.py
3. Add availability_score logic in assignment_service.py
4. Implement risk_flag automation in credit_service.py

### **PHASE 2: ANALYTICS DASHBOARD (Weeks 3-4)**
1. Build analytics_service.py enhancements:
   - Stage drop-off rate
   - Top 20% workers (Pareto)
   - Pincode heatmap data
   - Time-to-close calculation
2. Create dashboard views

### **PHASE 3: OPTIMIZATION ENGINE (Weeks 5-6)**
1. Bundle suggestion rules engine
2. Cross-sell trigger system
3. Margin-based lead scoring
4. Late-entry recovery calculator

### **PHASE 4: NETWORK INTELLIGENCE (Weeks 7-8)**
1. Referral chain analyzer
2. Collaboration strength calculator
3. Network leverage metrics
4. Worker pairing suggestions

---

## 🔟 KEY RECOMMENDATIONS

### **DO THIS NOW:**
1. ✅ Your schema is excellent — don't change it
2. Add computed fields to models:
   - `Worker.influence_score`
   - `Worker.reliability_score`
   - `Worker.availability_score`
3. Build intelligence layer in services/ (not in models)
4. Create analytics dashboard consuming service metrics
5. Set up scheduled tasks for score recalculation

### **DON'T DO:**
1. ❌ Don't add inventory management (out of scope)
2. ❌ Don't build mobile app (distraction)
3. ❌ Don't add multi-warehouse (no need)
4. ❌ Don't over-engineer authentication (basic works)

### **BUSINESS FOCUS:**
- **Capture opportunity at ANY stage** (your schema supports this)
- **Grow worker network** (referral tracking is solid)
- **Maximize margin** (stage-based intelligence needed)
- **Control credit risk** (foundation exists, add automation)

---

## 📊 FINAL VERDICT

Your current architecture is **85% complete** from a data modeling perspective. The schema is:
- ✅ Business-aligned
- ✅ Normalized correctly
- ✅ Scalable for single-shop model
- ✅ Supports all core requirements

**What's Missing:** Intelligence layer (calculation, scoring, automation, alerts)

**Next Step:** Build service layer intelligence using the logic specifications in this document.

---

**Document Status:** Architecture Review Complete  
**Ready For:** Intelligence Layer Implementation  
**Owner:** AG-PROJ01 Development Team
