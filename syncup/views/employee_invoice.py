# Django imports
from django.db.models import Sum
from django.shortcuts import render
from django.http import JsonResponse
from django.contrib.auth.models import User
from django.views.decorators.csrf import csrf_exempt
from django.contrib.auth.decorators import login_required
from ..models import (
    UserProfile, InvoiceEmployeeMapping
)
import json

# =============== Invoice VIEWS ===============
@login_required
def invoice_employee_list(request):
    from django.db.models import Count
    employees = User.objects.filter(
        is_staff=True
    ).exclude(
        is_superuser=True
    ).select_related('userprofile').annotate(
        invoice_count=Count('userprofile__invoiceemployeemapping'),
        invoice_amount=Sum('userprofile__invoiceemployeemapping__invoice_amount'),
    )
    context = {"employees": employees}
    return render(request, 'employee_invoice/invoice_employee_list.html', context)

@login_required
def employee_invoices(request, user_id):
    from django.db.models.functions import TruncMonth, ExtractYear, ExtractMonth
    employee = UserProfile.objects.get(user__id=user_id)
    qs = InvoiceEmployeeMapping.objects.filter(user=employee).order_by('-invoice_date')

    # Available months for filter dropdown
    available_months = (
        qs.annotate(m=TruncMonth('invoice_date'))
        .values('m')
        .distinct()
        .order_by('-m')
    )
    months_list = [{'value': m['m'].strftime('%Y-%m'), 'label': m['m'].strftime('%B %Y')} for m in available_months if m['m']]

    # Apply filters
    selected_month = request.GET.get('month', '')
    date_from = request.GET.get('date_from', '')
    date_to = request.GET.get('date_to', '')

    if selected_month:
        try:
            year, month = selected_month.split('-')
            qs = qs.filter(invoice_date__year=int(year), invoice_date__month=int(month))
        except ValueError:
            pass
    if date_from:
        qs = qs.filter(invoice_date__gte=date_from)
    if date_to:
        qs = qs.filter(invoice_date__lte=date_to)

    grand_total = qs.aggregate(total=Sum('invoice_amount'))['total'] or 0

    context = {
        "employee": employee,
        "invoice_mappings": qs,
        "months_list": months_list,
        "selected_month": selected_month,
        "date_from": date_from,
        "date_to": date_to,
        "grand_total": grand_total,
        "record_count": qs.count(),
    }
    return render(request, 'employee_invoice/employee_invoices.html', context)

# =============== API VIEWS ===============
@csrf_exempt
def invoice_employee_mapping_api(request):
    if request.method == 'POST':
        try:
            data = json.loads(request.body)
        except json.JSONDecodeError:
            return JsonResponse({'error': 'Invalid JSON.'}, status=400)
        invoice_id = data.get('invoice_id')
        employee_id = data.get('employee_id')
        if not invoice_id or not employee_id:
            return JsonResponse({'error': 'Invoice ID and Employee ID are required.'}, status=400)
        user = UserProfile.objects.filter(user__id=employee_id, user__is_staff=True).first()
        if not user:
            return JsonResponse({'error': 'Employee not found or is not a staff member.'}, status=404)
        
        # Check if invoice already mapped
        existing = InvoiceEmployeeMapping.objects.filter(invoice_id=invoice_id).first()
        if existing:
            return JsonResponse({
                'error': 'Invoice already mapped.',
                'existing_mapping': {
                    'mapping_id': existing.id,
                    'employee': {
                        'id': existing.user.user.id,
                        'name': existing.user.name or existing.user.user.username,
                    },
                    'invoice_number': existing.invoice_number,
                    'mapped_at': str(existing.created_at),
                },
            }, status=409)
        
        defaults = {'user': user}
        if data.get('invoice_number'):
            defaults['invoice_number'] = data['invoice_number']
        if data.get('invoice_amount') is not None:
            defaults['invoice_amount'] = data['invoice_amount']
        if data.get('invoice_date'):
            defaults['invoice_date'] = data['invoice_date']
        if data.get('invoice_brand'):
            defaults['invoice_brand'] = data['invoice_brand']
        if data.get('invoice_brand_id') is not None:
            defaults['invoice_brand_id'] = data['invoice_brand_id']
        mapping = InvoiceEmployeeMapping.objects.create(
            invoice_id=invoice_id,
            **defaults,
        )
        return JsonResponse({
            'message': 'Mapping saved successfully.',
            'mapping_id': mapping.id,
            'status': 'new',
            'employee': {
                'id': user.user.id,
                'name': user.name or user.user.username,
            },
            'invoice': {
                'invoice_id': mapping.invoice_id,
                'invoice_number': mapping.invoice_number,
                'invoice_amount': str(mapping.invoice_amount),
                'invoice_date': str(mapping.invoice_date),
                'invoice_brand': mapping.invoice_brand,
                'invoice_brand_id': mapping.invoice_brand_id,
            },
        })
    else:
        employees = User.objects.filter(is_staff=True).exclude(is_superuser=True).select_related('userprofile')
        data = [{'id': e.id, 'username': e.userprofile.name or e.username} for e in employees]
        return JsonResponse({'employees': data})


@csrf_exempt
@login_required
def invoice_mapping_delete(request, mapping_id):
    if request.method != 'DELETE':
        return JsonResponse({'error': 'DELETE method required.'}, status=405)
    mapping = InvoiceEmployeeMapping.objects.filter(id=mapping_id).first()
    if not mapping:
        return JsonResponse({'error': 'Mapping not found.'}, status=404)
    mapping.delete()
    return JsonResponse({'message': 'Mapping deleted successfully.'})