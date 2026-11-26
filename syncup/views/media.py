"""Media Views - Cloudinary + ClickSend. Superuser-only, like the rest of AG-PROJ01.

Endpoints:
  GET  /services/cloudinary      - Client-side Cloudinary gallery page
  GET  /services/clicksend       - Client-side ClickSend SMS page
  POST /services/cloud-sign      - Server-side signature for the gallery's delete / ZIP calls

The Cloudinary gallery and ClickSend pages are fully client-side: the server only injects config/data;
the browser does the rest.
"""
from __future__ import annotations

import json
import base64
import hashlib
import time

from django.conf import settings
from django.http import JsonResponse
from django.shortcuts import render
from django.urls import reverse
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_GET, require_POST
from django.core.serializers.json import DjangoJSONEncoder

from .auth import superuser_required

# ====================================================================
# Client-side standalone pages (server only injects config)
# ====================================================================
@superuser_required
@require_GET
def cloudinary(request):
    # NOTE: the API secret is NEVER sent to the browser. Signed operations (delete / ZIP)
    # call the server-side `cloud_sign` endpoint instead.
    config = {
        "upload_preset": getattr(settings, "CLOUDINARY_UPLOAD_PRESET", "syncup_unsigned"),
        "tags": getattr(settings, "CLOUDINARY_FOLDER_PREFIX", "devices"),
        "cloud_name": getattr(settings, "CLOUDINARY_CLOUD_NAME", ""),
        "sign_url": reverse("cloud_sign"),
        # Accounts scope: people's Cloud folders (app → Tools → Cloud), tagged syncup_cloud + acct_<id>.
        **_cloud_accounts_config(),
    }

    encoded = base64.b64encode(json.dumps(config, cls=DjangoJSONEncoder).encode()).decode()
    return render(request, "services/cloudinary.html", {"app_config": encoded})


def _cloud_accounts_config():
    """The gallery's Accounts scope: people with Cloud switched on or with files (names for the account tags), and
    the console's sign / uploaded / deleted addresses (account 0 = replaced by the chosen account's id)."""
    from mobileapi import cloud_api
    from mobileapi.models import AppAccount, CloudFile, CloudFolder
    from django.db.models import Q

    ids = set(CloudFile.objects.values_list("owner_id", flat=True).distinct())
    ids |= set(CloudFolder.objects.values_list("account_id", flat=True).distinct())
    people = AppAccount.objects.filter(Q(pk__in=ids) | ~Q(cloudinary_access="off")).order_by("name")
    return {
        "cloud_tag": "syncup_cloud",
        "accounts": [{"id": a.id, "name": a.name or a.login_label, "tag": f"acct_{a.id}", "root": cloud_api.account_root(a.id),
                      "access": a.cloudinary_access} for a in people],
        "account_sign_url": reverse("mobile_account_cloud_sign", args=[0]),
        "account_uploaded_url": reverse("mobile_account_cloud_uploaded", args=[0]),
        "account_deleted_url": reverse("mobile_account_cloud_deleted", args=[0]),
    }


@csrf_exempt
@require_POST
def cloud_sign(request):
    """Sign Cloudinary params server-side so the API secret never reaches the browser.

    Superuser-only. Body: {"params": {...}} → {"signature", "api_key", "timestamp"}.
    The server injects the timestamp; the client echoes the returned timestamp/api_key/signature
    back to Cloudinary's destroy / generate_archive endpoints (existing gallery flow unchanged).
    """
    if not (request.user.is_authenticated and request.user.is_superuser):
        return JsonResponse({"error": "forbidden"}, status=403)
    try:
        body = json.loads(request.body or b"{}")
    except (ValueError, TypeError):
        return JsonResponse({"error": "invalid json"}, status=400)

    params = dict(body.get("params") or {})
    params["timestamp"] = str(int(time.time()))
    to_sign = "&".join(f"{k}={params[k]}" for k in sorted(params) if params[k] not in (None, ""))
    signature = hashlib.sha1((to_sign + settings.CLOUDINARY_API_SECRET).encode()).hexdigest()
    return JsonResponse({
        "signature": signature,
        "api_key": settings.CLOUDINARY_API_KEY,
        "timestamp": params["timestamp"],
    })


@superuser_required
@require_GET
def clicksend(request):
    config = {
        "username": getattr(settings, "CLICKSEND_USERNAME", ""),
        "api_key": getattr(settings, "CLICKSEND_API_KEY", ""),
    }

    encoded = base64.b64encode(json.dumps(config, cls=DjangoJSONEncoder).encode()).decode()
    return render(request, "services/clicksend.html", {"app_config": encoded})
