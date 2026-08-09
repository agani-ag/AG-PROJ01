"""Token auth helpers for the mobile API (hand-rolled Bearer, no DRF)."""
import json
from functools import wraps

from django.http import JsonResponse
from django.utils import timezone

from .models import AppAuthToken


def json_body(request):
    """Parse a JSON request body; returns None on invalid JSON."""
    try:
        return json.loads(request.body or b"{}")
    except (ValueError, TypeError):
        return None


def _extract_bearer(request):
    header = request.META.get("HTTP_AUTHORIZATION", "")
    if header.startswith("Bearer "):
        return header[7:].strip()
    return None


def app_token_required(view):
    """Require a valid `Authorization: Bearer <key>`; sets request.account/auth_token."""

    @wraps(view)
    def wrapper(request, *args, **kwargs):
        key = _extract_bearer(request)
        if not key:
            return JsonResponse({"success": False, "message": "Authentication required"}, status=401)
        token = AppAuthToken.objects.select_related("account").filter(key=key).first()
        if not token or not token.is_valid or not token.account.is_active:
            return JsonResponse({"success": False, "message": "Invalid or expired token"}, status=401)
        token.last_used_at = timezone.now()
        token.save(update_fields=["last_used_at"])
        request.account = token.account
        request.auth_token = token
        return view(request, *args, **kwargs)

    return wrapper
