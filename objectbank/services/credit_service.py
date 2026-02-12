from django.db import transaction
from django.db.models import Sum, F, Q
from django.utils import timezone
from datetime import timedelta
from ..models import WorkerCreditLedger, Worker


# =====================================================
# CREDIT LEDGER MANAGEMENT
# =====================================================

@transaction.atomic
def record_credit(worker, amount, transaction_type, project=None, due_date=None):
    """Record a credit transaction (debit entry)"""
    last_entry = WorkerCreditLedger.objects.filter(
        worker=worker
    ).order_by("-id").first()

    previous_balance = last_entry.running_balance if last_entry else 0
    new_balance = previous_balance + amount

    return WorkerCreditLedger.objects.create(
        worker=worker,
        project=project,
        transaction_type=transaction_type,
        debit=amount,
        credit=0,
        running_balance=new_balance,
        due_date=due_date,
        is_settled=False
    )


@transaction.atomic
def record_payment(worker, amount, transaction_type, project=None):
    """Record a payment transaction (credit entry)"""
    last_entry = WorkerCreditLedger.objects.filter(
        worker=worker
    ).order_by("-id").first()

    previous_balance = last_entry.running_balance if last_entry else 0
    new_balance = previous_balance - amount

    return WorkerCreditLedger.objects.create(
        worker=worker,
        project=project,
        transaction_type=transaction_type,
        debit=0,
        credit=amount,
        running_balance=new_balance,
        is_settled=True
    )


def get_worker_current_balance(worker):
    """Get worker's current credit balance"""
    latest = WorkerCreditLedger.objects.filter(
        worker=worker
    ).order_by('-id').first()
    
    return latest.running_balance if latest else 0


# =====================================================
# RISK ASSESSMENT
# =====================================================

def assess_worker_risk(worker):
    """
    Assess worker's credit risk level
    Returns: (risk_level, risk_flags)
    risk_level: 'GREEN', 'YELLOW', 'RED'
    """
    risk_flags = []
    risk_level = "GREEN"
    
    # Get latest ledger entry
    latest_ledger = WorkerCreditLedger.objects.filter(
        worker=worker
    ).order_by('-id').first()
    
    if not latest_ledger:
        return "GREEN", [], {}
    
    current_balance = latest_ledger.running_balance
    
    # Rule 1: High Outstanding Balance (Negative = Worker owes money)
    if current_balance < -50000:
        risk_flags.append("CRITICAL_DEBT: Outstanding > ₹50,000")
        risk_level = "RED"
    elif current_balance < -20000:
        risk_flags.append("MODERATE_DEBT: Outstanding > ₹20,000")
        if risk_level != "RED":
            risk_level = "YELLOW"
    
    # Rule 2: Overdue Payments
    overdue_count = WorkerCreditLedger.objects.filter(
        worker=worker,
        is_settled=False,
        due_date__lt=timezone.now().date()
    ).exclude(due_date__isnull=True).count()
    
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
    from .worker_service import calculate_reliability_score
    reliability = calculate_reliability_score(worker)
    if reliability < 50:
        risk_flags.append(f"LOW_RELIABILITY: Score {reliability}/100")
        if risk_level == "GREEN":
            risk_level = "YELLOW"
    
    # Return summary
    summary = {
        'current_balance': current_balance,
        'overdue_count': overdue_count,
        'days_since_activity': days_since_last_activity,
        'reliability_score': reliability
    }
    
    return risk_level, risk_flags, summary


def get_credit_action(risk_level, worker):
    """
    Get recommended credit action based on risk level
    Returns: dict with credit policy
    """
    actions = {
        'GREEN': {
            'credit_allowed': True,
            'max_credit_limit': 100000,
            'payment_terms': 'NET_30',
            'message': 'Good standing - standard credit terms',
            'badge_class': 'success'
        },
        'YELLOW': {
            'credit_allowed': True,
            'max_credit_limit': 30000,
            'payment_terms': 'NET_15',
            'message': 'Moderate risk - reduced credit limit',
            'badge_class': 'warning'
        },
        'RED': {
            'credit_allowed': False,
            'max_credit_limit': 0,
            'payment_terms': 'ADVANCE_ONLY',
            'message': 'High risk - cash/advance payment required',
            'badge_class': 'danger'
        }
    }
    
    return actions.get(risk_level, actions['YELLOW'])


# =====================================================
# OUTSTANDING & ALERTS
# =====================================================

def get_outstanding_by_worker():
    """Get outstanding amounts grouped by worker"""
    # Get latest balance for each worker
    workers = Worker.objects.filter(active_status=True)
    
    outstanding_list = []
    for worker in workers:
        balance = get_worker_current_balance(worker)
        if balance < 0:  # Negative = Outstanding
            outstanding_list.append({
                'worker': worker,
                'outstanding': abs(balance),
                'balance': balance
            })
    
    # Sort by outstanding amount descending
    outstanding_list.sort(key=lambda x: x['outstanding'], reverse=True)
    
    return outstanding_list


def generate_credit_alerts():
    """
    Generate credit-related alerts for action
    Returns: List of alert dicts
    """
    alerts = []
    
    # Alert Type 1: High-Value Outstanding
    workers_with_high_outstanding = []
    for worker in Worker.objects.filter(active_status=True):
        balance = get_worker_current_balance(worker)
        if balance < -50000:
            alerts.append({
                'type': 'HIGH_OUTSTANDING',
                'worker': worker,
                'amount': abs(balance),
                'priority': 'HIGH',
                'action': 'Call for payment plan',
                'badge_class': 'danger'
            })
    
    # Alert Type 2: Due Date Approaching (7 days)
    upcoming_due = WorkerCreditLedger.objects.filter(
        is_settled=False,
        due_date__range=[
            timezone.now().date(),
            timezone.now().date() + timedelta(days=7)
        ]
    ).select_related('worker')
    
    for ledger in upcoming_due:
        alerts.append({
            'type': 'DUE_SOON',
            'worker': ledger.worker,
            'amount': ledger.debit - ledger.credit,
            'due_date': ledger.due_date,
            'priority': 'MEDIUM',
            'action': 'Send payment reminder',
            'badge_class': 'warning'
        })
    
    # Alert Type 3: Overdue (Past Due Date)
    overdue = WorkerCreditLedger.objects.filter(
        is_settled=False,
        due_date__lt=timezone.now().date()
    ).exclude(due_date__isnull=True).select_related('worker')
    
    for ledger in overdue:
        days_overdue = (timezone.now().date() - ledger.due_date).days
        alerts.append({
            'type': 'OVERDUE',
            'worker': ledger.worker,
            'amount': ledger.debit - ledger.credit,
            'days_overdue': days_overdue,
            'priority': 'CRITICAL',
            'action': 'Immediate collection action',
            'badge_class': 'danger'
        })
    
    return alerts


def get_workers_at_risk():
    """Get all workers with risk assessment"""
    workers = Worker.objects.filter(active_status=True)
    
    risk_workers = []
    for worker in workers:
        risk_level, risk_flags, summary = assess_worker_risk(worker)
        if risk_level in ['YELLOW', 'RED']:
            risk_workers.append({
                'worker': worker,
                'risk_level': risk_level,
                'risk_flags': risk_flags,
                'summary': summary,
                'action': get_credit_action(risk_level, worker)
            })
    
    # Sort by risk level (RED first, then YELLOW)
    risk_workers.sort(key=lambda x: 0 if x['risk_level'] == 'RED' else 1)
    
    return risk_workers