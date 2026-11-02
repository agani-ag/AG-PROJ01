"""Admin → DNS services: what phones can choose from for browsing in SyncUp.

Each one is listed on every phone, turned off. Add, test, show or hide, reorder, remove. The app reads the
result from dns_services.catalog. Superuser-only, like the rest of /mobile/.
"""
import json

from django.contrib import messages
from django.db.models import Max
from django.http import JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.views.decorators.http import require_POST

from .admin_views import superuser_required
from .dns_services import PLAIN, kind_of, test_lookup
from .models import DnsService


def _body(request):
    try:
        data = json.loads(request.body or b"{}")
    except ValueError:
        return {}
    return data if isinstance(data, dict) else {}


@superuser_required
def page(request):
    services = list(DnsService.objects.all())
    for s in services:
        s.unencrypted = kind_of(s.address) == PLAIN
    return render(request, "mobileapi/dns_services.html", {"services": services, "shown": sum(s.is_active for s in services)})


@superuser_required
@require_POST
def save(request):
    """Add, or with `id` edit. A service that doesn't answer needs `anyway`: this server's own network may be
    what blocks it, so the admin can still decide."""
    body = _body(request)
    name = str(body.get("name") or "").strip()[:60]
    address = str(body.get("address") or "").strip()[:200]
    description = str(body.get("description") or "").strip()[:120]
    service = get_object_or_404(DnsService, id=body["id"]) if body.get("id") else None
    if not name:
        return JsonResponse({"error": "Give it a name."}, status=400)
    if not kind_of(address):
        return JsonResponse({"error": "Enter an https address, a DNS hostname or an IP address."}, status=400)
    same = DnsService.objects.filter(address__iexact=address)
    if service:
        same = same.exclude(id=service.id)
    if same.exists():
        return JsonResponse({"error": "That address is already on the list."}, status=400)
    if not body.get("anyway") and (service is None or service.address != address):
        _, problem = test_lookup(address)
        if problem:
            return JsonResponse({"unanswered": problem})
    if service:
        service.name, service.address, service.description = name, address, description
        service.save()
        messages.success(request, f"“{name}” saved.")
    else:
        position = (DnsService.objects.aggregate(m=Max("position"))["m"] or 0) + 1
        DnsService.objects.create(name=name, address=address, description=description, position=position)
        messages.success(request, f"“{name}” added. It's on every phone, turned off.")
    return JsonResponse({"ok": True})


@superuser_required
@require_POST
def test(request):
    ms, problem = test_lookup(str(_body(request).get("address") or ""))
    return JsonResponse({"ms": ms, "problem": problem})


@superuser_required
@require_POST
def toggle(request, service_id):
    s = get_object_or_404(DnsService, id=service_id)
    s.is_active = not s.is_active
    s.save(update_fields=["is_active", "updated_at"])
    messages.success(request, f"“{s.name}” is {'shown on phones' if s.is_active else 'hidden from phones'}.")
    return redirect("mobile_dns")


@superuser_required
@require_POST
def move(request, service_id):
    services = list(DnsService.objects.all())
    ids = [s.id for s in services]
    s = get_object_or_404(DnsService, id=service_id)
    i = ids.index(s.id)
    j = i - 1 if request.POST.get("to") == "up" else i + 1
    if 0 <= j < len(services):
        services[i], services[j] = services[j], services[i]
        for position, item in enumerate(services, start=1):
            if item.position != position:
                item.position = position
                item.save(update_fields=["position", "updated_at"])
    return redirect("mobile_dns")


@superuser_required
@require_POST
def delete(request, service_id):
    s = get_object_or_404(DnsService, id=service_id)
    name = s.name
    s.delete()
    messages.success(request, f"“{name}” removed from every phone.")
    return redirect("mobile_dns")
