from django.db import transaction
from ..models import WorkerCreditLedger

# Record Credit
@transaction.atomic
def record_credit(worker, amount, transaction_type):
    last_entry = WorkerCreditLedger.objects.filter(
        worker=worker
    ).order_by("-id").first()

    previous_balance = last_entry.running_balance if last_entry else 0

    new_balance = previous_balance + amount

    return WorkerCreditLedger.objects.create(
        worker=worker,
        transaction_type=transaction_type,
        debit=amount,
        running_balance=new_balance
    )