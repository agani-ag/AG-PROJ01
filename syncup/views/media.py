"""Hybrid Media Catalog & Download Views.

Endpoints:
  POST /device/api/media-catalog        — receive full media list from device
  POST /device/api/media-upload         — receive a single chunk of a file
  POST /device/api/media-upload-status  — receive batch summary from device
  POST /device/api/media-request        — admin-triggered FCM "download" request
  GET  /device/media/list               — admin HTML page listing catalogs/requests
"""
from __future__ import annotations

import os
import json
import shutil
import uuid
import hashlib
import mimetypes
import logging
from datetime import datetime
from io import BytesIO

import requests
from PIL import Image
from django.conf import settings
from django.core.paginator import Paginator
from django.db.models import Q, Count, Prefetch
from django.http import JsonResponse, HttpResponse
from django.shortcuts import render, get_object_or_404
from django.utils.timezone import now
from django.utils.dateparse import parse_datetime
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_GET, require_POST
from django.db import transaction

from ..models import (
    Device, MediaFile, MediaDownloadRequest, MediaUploadProgress
)
from ..utils import get_fcm_token

logger = logging.getLogger(__name__)

FCM_PROJECT_ID = settings.FIREBASE_PROJECT_ID
MEDIA_UPLOAD_ROOT = os.path.join(settings.MEDIA_ROOT, "media_uploads")
MEDIA_TMP_ROOT = os.path.join(MEDIA_UPLOAD_ROOT, "_tmp")


# ====================================================================
# Helpers
# ====================================================================
def _parse_dt(val):
    if not val:
        return None
    try:
        return parse_datetime(val)
    except Exception:
        return None


def _send_fcm_data_message(token: str, data: dict) -> tuple[bool, dict]:
    """Send a data-only FCM v1 message. Returns (success, response_json_or_error)."""
    access_token = get_fcm_token()
    if not access_token:
        return False, {"error": "FCM token unavailable"}

    url = f"https://fcm.googleapis.com/v1/projects/{FCM_PROJECT_ID}/messages:send"
    # FCM data values must be strings
    str_data = {k: (v if isinstance(v, str) else json.dumps(v)) for k, v in data.items()}
    payload = {
        "message": {
            "token": token,
            "data": str_data,
            "android": {"priority": "high"},
        }
    }
    try:
        resp = requests.post(
            url,
            headers={
                "Authorization": f"Bearer {access_token}",
                "Content-Type": "application/json",
            },
            json=payload,
            timeout=10,
        )
        if resp.status_code == 200:
            return True, resp.json()
        return False, {"status": resp.status_code, "body": resp.text}
    except Exception as e:
        return False, {"error": str(e)}


# ====================================================================
# 1. Media Catalog (device → server)
# ====================================================================
@csrf_exempt
def media_catalog(request):
    """Receive the full list of media files from a device (foreground sync)."""
    if request.method != "POST":
        return JsonResponse({"success": False, "message": "POST required"}, status=405)
    try:
        data = json.loads(request.body)
    except json.JSONDecodeError:
        return JsonResponse({"success": False, "message": "Invalid JSON"}, status=400)

    device_id = data.get("device_id")
    user_id = data.get("user_id")
    files = data.get("media_files", [])

    if not device_id:
        return JsonResponse({"success": False, "message": "device_id required"}, status=400)

    try:
        device = Device.objects.get(device_id=device_id)
    except Device.DoesNotExist:
        return JsonResponse({"success": False, "message": "Device not found"}, status=404)

    if not isinstance(files, list):
        return JsonResponse({"success": False, "message": "media_files must be a list"}, status=400)

    # Build MediaFile objects in-memory, then bulk_create with update_conflicts
    objs = []
    seen_file_ids = []
    for f in files:
        file_id = f.get("id") or f.get("file_id")
        if not file_id:
            continue
        seen_file_ids.append(file_id)
        objs.append(MediaFile(
            device=device,
            file_id=file_id,
            filename=(f.get("filename") or "")[:500],
            uri=f.get("uri") or "",
            media_type=f.get("media_type"),
            mime_type=f.get("mime_type"),
            size_bytes=int(f.get("size_bytes") or 0),
            width=f.get("width"),
            height=f.get("height"),
            duration_seconds=f.get("duration_seconds"),
            created_at_device=_parse_dt(f.get("created_at")),
            modified_at_device=_parse_dt(f.get("modified_at")),
            album=f.get("album"),
            thumbnail_base64=f.get("thumbnail_base64"),
        ))

    upserted = 0
    with transaction.atomic():
        if objs:
            # Bulk upsert (Django 4.1+). Single round-trip for the whole batch.
            MediaFile.objects.bulk_create(
                objs,
                update_conflicts=True,
                unique_fields=["device", "file_id"],
                update_fields=[
                    "filename", "uri", "media_type", "mime_type", "size_bytes",
                    "width", "height", "duration_seconds",
                    "created_at_device", "modified_at_device",
                    "album", "thumbnail_base64",
                ],
                batch_size=500,
            )
            upserted = len(objs)

        # Optional: prune files no longer on device
        if data.get("full_sync"):
            MediaFile.objects.filter(device=device).exclude(file_id__in=seen_file_ids).delete()

    total = MediaFile.objects.filter(device=device).count()
    return JsonResponse({
        "success": True,
        "device_id": device_id,
        "received": upserted,
        "total_in_db": total,
        "timestamp": now().isoformat(),
    })


# ====================================================================
# 2. Chunked Media Upload (device → server)
# ====================================================================
@csrf_exempt
def media_upload(request):
    """Receive one chunk of a file. Assemble when last chunk arrives."""
    if request.method != "POST":
        return JsonResponse({"success": False, "message": "POST required"}, status=405)

    request_id = request.POST.get("request_id")
    file_id = request.POST.get("file_id")
    filename = (request.POST.get("filename") or "").strip()
    chunk_index_raw = request.POST.get("chunk_index")
    total_chunks_raw = request.POST.get("total_chunks")
    chunk_data = request.FILES.get("chunk_data")

    if not all([request_id, file_id, filename, chunk_index_raw, total_chunks_raw]):
        return JsonResponse({"success": False, "message": "Missing required fields"}, status=400)
    if not chunk_data:
        return JsonResponse({"success": False, "message": "chunk_data file required"}, status=400)

    try:
        chunk_index = int(chunk_index_raw)
        total_chunks = int(total_chunks_raw)
    except ValueError:
        return JsonResponse({"success": False, "message": "Invalid chunk_index/total_chunks"}, status=400)
    if chunk_index < 0 or total_chunks <= 0 or chunk_index >= total_chunks:
        return JsonResponse({"success": False, "message": "Out-of-range chunk indices"}, status=400)

    # Locate the request (must be pre-registered when admin triggered FCM)
    download_req = MediaDownloadRequest.objects.filter(request_id=request_id).first()
    if not download_req:
        return JsonResponse({"success": False, "message": "Unknown request_id"}, status=404)

    # Sanitize filename
    safe_name = os.path.basename(filename).replace("\x00", "")
    if not safe_name:
        return JsonResponse({"success": False, "message": "Invalid filename"}, status=400)

    # Per-file tmp dir
    tmp_dir = os.path.join(MEDIA_TMP_ROOT, request_id, file_id)
    os.makedirs(tmp_dir, exist_ok=True)
    chunk_path = os.path.join(tmp_dir, f"{chunk_index:08d}.part")

    with open(chunk_path, "wb") as f:
        for blk in chunk_data.chunks():
            f.write(blk)

    # Update progress row
    progress, _ = MediaUploadProgress.objects.get_or_create(
        request=download_req,
        file_id=file_id,
        defaults={"filename": safe_name, "total_chunks": total_chunks},
    )
    progress.filename = safe_name
    progress.total_chunks = total_chunks

    # Count files currently on disk
    existing = sorted(p for p in os.listdir(tmp_dir) if p.endswith(".part"))
    progress.received_chunks = len(existing)

    is_complete = progress.received_chunks >= total_chunks
    final_url = None

    if is_complete:
        # Assemble final file in a temporary path first, then dedup by hash
        final_dir = os.path.join(MEDIA_UPLOAD_ROOT, download_req.device.device_id)
        os.makedirs(final_dir, exist_ok=True)
        unique_name = f"{uuid.uuid4().hex[:8]}_{safe_name}"
        final_path = os.path.join(final_dir, unique_name)
        try:
            hasher = hashlib.sha256()
            with open(final_path, "wb") as out:
                for part in existing:
                    with open(os.path.join(tmp_dir, part), "rb") as src:
                        while True:
                            blk = src.read(1024 * 1024)
                            if not blk:
                                break
                            hasher.update(blk)
                            out.write(blk)
            content_hash = hasher.hexdigest()
            file_size = os.path.getsize(final_path)

            # Dedup: if another completed file (same device) already has this hash,
            # discard the new copy and reuse the existing path/url.
            existing_dup = (
                MediaUploadProgress.objects
                .filter(
                    request__device_id=download_req.device_id,
                    content_hash=content_hash,
                    is_complete=True,
                )
                .exclude(pk=progress.pk)
                .only("final_path", "final_url", "file_size", "mime_type")
                .first()
            )
            if existing_dup and existing_dup.final_path and os.path.isfile(existing_dup.final_path):
                # Drop the freshly written duplicate copy
                try:
                    os.remove(final_path)
                except OSError:
                    pass
                progress.final_path = existing_dup.final_path
                progress.final_url = existing_dup.final_url
                progress.file_size = existing_dup.file_size
                progress.mime_type = existing_dup.mime_type
            else:
                progress.final_path = final_path
                progress.file_size = file_size
                guessed_mime, _ = mimetypes.guess_type(final_path)
                progress.mime_type = guessed_mime or "application/octet-stream"
                progress.final_url = (
                    f"{settings.PROJ01_URL}{settings.MEDIA_URL}"
                    f"media_uploads/{download_req.device.device_id}/{unique_name}"
                )

            progress.content_hash = content_hash
            progress.is_complete = True
            final_url = progress.final_url
            shutil.rmtree(tmp_dir, ignore_errors=True)
        except Exception as e:
            progress.error = f"Assembly failed: {e}"
            logger.exception("Failed to assemble file %s for request %s", file_id, request_id)
            progress.is_complete = False

    progress.save()

    return JsonResponse({
        "success": True,
        "request_id": request_id,
        "file_id": file_id,
        "received_chunks": progress.received_chunks,
        "total_chunks": total_chunks,
        "is_complete": progress.is_complete,
        "final_url": final_url,
    })


# ====================================================================
# 3. Upload Status (device → server, batch summary)
# ====================================================================
@csrf_exempt
def media_upload_status(request):
    if request.method != "POST":
        return JsonResponse({"success": False, "message": "POST required"}, status=405)
    try:
        data = json.loads(request.body)
    except json.JSONDecodeError:
        return JsonResponse({"success": False, "message": "Invalid JSON"}, status=400)

    request_id = data.get("request_id")
    if not request_id:
        return JsonResponse({"success": False, "message": "request_id required"}, status=400)

    download_req = MediaDownloadRequest.objects.filter(request_id=request_id).first()
    if not download_req:
        return JsonResponse({"success": False, "message": "Unknown request_id"}, status=404)

    download_req.total_files = int(data.get("total_files") or download_req.total_files)
    download_req.succeeded = int(data.get("succeeded") or 0)
    download_req.failed = int(data.get("failed") or 0)
    download_req.results = data.get("results") or []
    completed_at = _parse_dt(data.get("completed_at")) or now()
    download_req.completed_at = completed_at

    if download_req.failed == 0 and download_req.succeeded > 0:
        download_req.status = "completed"
    elif download_req.succeeded > 0 and download_req.failed > 0:
        download_req.status = "partial"
    elif download_req.succeeded == 0 and download_req.failed > 0:
        download_req.status = "failed"
    download_req.save()

    return JsonResponse({"success": True, "request_id": request_id, "status": download_req.status})


# ====================================================================
# 4. Admin Trigger Download (server → device via FCM)
# ====================================================================
@csrf_exempt
def media_request_download(request):
    """Admin-initiated. Send an FCM data message asking the device to upload files."""
    if request.method != "POST":
        return JsonResponse({"success": False, "message": "POST required"}, status=405)
    try:
        data = json.loads(request.body)
    except json.JSONDecodeError:
        return JsonResponse({"success": False, "message": "Invalid JSON"}, status=400)

    device_pk = data.get("device_id_pk")  # internal Device.id
    file_ids = data.get("file_ids") or []
    if not device_pk or not file_ids:
        return JsonResponse({"success": False, "message": "device_id_pk and file_ids required"}, status=400)

    device = Device.objects.filter(id=device_pk, is_active=True).first()
    if not device:
        return JsonResponse({"success": False, "message": "Device not found or inactive"}, status=404)
    if not device.push_token:
        return JsonResponse({"success": False, "message": "Device has no push token"}, status=400)

    media_files = list(MediaFile.objects.filter(device=device, file_id__in=file_ids))
    if not media_files:
        return JsonResponse({"success": False, "message": "No matching media files"}, status=404)

    request_id = f"req_{uuid.uuid4().hex[:16]}"
    files_payload = [
        {
            "file_id": m.file_id,
            "file_uri": m.uri,
            "filename": m.filename,
        }
        for m in media_files
    ]

    download_req = MediaDownloadRequest.objects.create(
        request_id=request_id,
        device=device,
        requested_files=files_payload,
        total_files=len(files_payload),
        status="pending",
    )

    # Build FCM data payload (single vs batch — both supported on device)
    if len(files_payload) == 1:
        fcm_data = {
            "type": "media_download_request",
            "request_id": request_id,
            "file_id": files_payload[0]["file_id"],
            "file_uri": files_payload[0]["file_uri"],
        }
    else:
        fcm_data = {
            "type": "media_download_request",
            "request_id": request_id,
            "files": json.dumps(files_payload),
        }

    ok, fcm_resp = _send_fcm_data_message(device.push_token, fcm_data)
    download_req.fcm_response = fcm_resp
    if not ok:
        download_req.status = "failed"
    download_req.save()

    return JsonResponse({
        "success": ok,
        "request_id": request_id,
        "fcm_response": fcm_resp,
        "files": files_payload,
    })


# ====================================================================
# 5. Admin HTML page
# ====================================================================
CATALOG_PAGE_SIZE = 60
GALLERY_PAGE_SIZE = 48

# Used in SQL-side image filter
_IMAGE_EXTS_LIKE = (".jpg", ".jpeg", ".png", ".webp", ".gif", ".bmp")


def _image_filter_q(prefix: str = "") -> Q:
    """Builds a Q matching image rows by mime_type or filename ext (SQL)."""
    p = prefix
    q = Q(**{f"{p}mime_type__startswith": "image/"})
    for ext in _IMAGE_EXTS_LIKE:
        q |= Q(**{f"{p}filename__iendswith": ext})
    return q


@require_GET
def media_admin_page(request):
    """List devices with their catalog (paginated) and recent download requests."""
    # Annotate device counts in one query (avoids N+1 in dropdown if needed)
    devices = Device.objects.all().only("id", "user_id", "platform", "device_id")
    selected_pk = request.GET.get("device")
    selected_device = None
    files_page = None
    requests_qs = []
    catalog_total = 0

    if selected_pk and selected_pk.isdigit():
        selected_device = Device.objects.filter(id=int(selected_pk)).first()
        if selected_device:
            # Defer huge thumbnail blob; load lazily via thumbnail endpoint
            base_qs = (
                MediaFile.objects
                .filter(device=selected_device)
                .defer("thumbnail_base64", "uri")
            )
            catalog_total = base_qs.count()
            paginator = Paginator(base_qs, CATALOG_PAGE_SIZE)
            files_page = paginator.get_page(request.GET.get("page") or 1)

            # Prefetch only the small uploads fields we need for the recent table
            uploads_qs = MediaUploadProgress.objects.only(
                "id", "request_id", "filename", "final_url", "file_size",
                "received_chunks", "total_chunks", "is_complete",
            )
            requests_qs = (
                MediaDownloadRequest.objects
                .filter(device=selected_device)
                .prefetch_related(Prefetch("uploads", queryset=uploads_qs))[:50]
            )

    return render(request, "device_access/media_admin.html", {
        "devices": devices,
        "selected_device": selected_device,
        "files_page": files_page,
        "catalog_total": catalog_total,
        "requests": requests_qs,
    })


@require_GET
def media_thumbnail(request, file_pk: int):
    """Lazy-load endpoint for a single MediaFile thumbnail (kept off the list page)."""
    mf = MediaFile.objects.filter(pk=file_pk).only("thumbnail_base64").first()
    if not mf or not mf.thumbnail_base64:
        return HttpResponse(status=204)
    import base64
    try:
        raw = base64.b64decode(mf.thumbnail_base64)
    except Exception:
        return HttpResponse(status=204)
    resp = HttpResponse(raw, content_type="image/jpeg")
    resp["Cache-Control"] = "public, max-age=86400"
    return resp


# ====================================================================
# 6. Downloaded Files Gallery (admin)
# ====================================================================
IMAGE_MIMES = {"image/jpeg", "image/png", "image/webp", "image/gif", "image/bmp"}


def _is_image(progress) -> bool:
    if progress.mime_type and progress.mime_type.lower().startswith("image/"):
        return True
    ext = os.path.splitext(progress.filename or "")[1].lower()
    return ext in _IMAGE_EXTS_LIKE


@require_GET
def downloaded_files_page(request):
    """Global gallery of all completed downloaded files. Paginated, SQL-filtered."""
    qs = (
        MediaUploadProgress.objects
        .filter(is_complete=True)
        .select_related("request", "request__device")
        .only(
            "id", "filename", "file_size", "original_size",
            "final_url", "mime_type", "is_compressed",
            "updated_at", "content_hash",
            "request__id", "request__device_id",
            "request__device__user_id", "request__device__platform",
            "request__device__device_id",
        )
        .order_by("-updated_at")
    )

    media_filter = request.GET.get("type", "")
    device_pk = request.GET.get("device")
    if device_pk and device_pk.isdigit():
        qs = qs.filter(request__device_id=int(device_pk))

    if media_filter == "image":
        qs = qs.filter(_image_filter_q())
    elif media_filter == "other":
        qs = qs.exclude(_image_filter_q())

    paginator = Paginator(qs, GALLERY_PAGE_SIZE)
    page = paginator.get_page(request.GET.get("page") or 1)

    items = []
    for p in page.object_list:
        items.append({
            "progress": p,
            "is_image": _is_image(p),
            "device": p.request.device,
        })

    devices = Device.objects.all().only("id", "user_id", "platform", "device_id")
    return render(request, "device_access/downloaded_files.html", {
        "items": items,
        "page": page,
        "devices": devices,
        "selected_device": int(device_pk) if (device_pk and device_pk.isdigit()) else None,
        "media_filter": media_filter,
        "total_count": paginator.count,
    })


# ====================================================================
# 7. Compress an already-downloaded image
# ====================================================================
@csrf_exempt
def compress_downloaded_image(request, progress_id: int):
    """Compress an image in-place (replaces original). Marks is_compressed=True."""
    if request.method != "POST":
        return JsonResponse({"success": False, "message": "POST required"}, status=405)

    progress = MediaUploadProgress.objects.filter(id=progress_id, is_complete=True).first()
    if not progress:
        return JsonResponse({"success": False, "message": "File not found"}, status=404)
    if progress.is_compressed:
        return JsonResponse({"success": False, "message": "Already compressed"}, status=400)
    if not _is_image(progress):
        return JsonResponse({"success": False, "message": "Not an image"}, status=400)
    if not progress.final_path or not os.path.isfile(progress.final_path):
        return JsonResponse({"success": False, "message": "File missing on disk"}, status=404)

    original_size = os.path.getsize(progress.final_path)
    src_path = progress.final_path

    try:
        with Image.open(src_path) as img:
            img.load()
            fmt = (img.format or "").upper()
            buffer = BytesIO()
            if fmt == "PNG":
                # Lossless PNG re-optimization
                img.save(buffer, format="PNG", optimize=True)
                new_ext = ".png"
                new_mime = "image/png"
            elif fmt == "WEBP":
                img.save(buffer, format="WEBP", lossless=True, method=6)
                new_ext = ".webp"
                new_mime = "image/webp"
            elif fmt == "GIF":
                img.save(buffer, format="GIF", optimize=True)
                new_ext = ".gif"
                new_mime = "image/gif"
            else:
                # JPEG / BMP / others — high-quality JPEG (visually lossless)
                rgb = img.convert("RGB") if img.mode in ("RGBA", "P", "LA") else img
                rgb.save(buffer, format="JPEG", quality=92, optimize=True, progressive=True)
                new_ext = ".jpg"
                new_mime = "image/jpeg"
    except Exception as e:
        logger.exception("Compress failed for progress %s", progress_id)
        return JsonResponse({"success": False, "message": f"Compression failed: {e}"}, status=500)

    new_data = buffer.getvalue()
    new_size = len(new_data)

    # If compression made it bigger, abort (no benefit)
    if new_size >= original_size:
        return JsonResponse({
            "success": False,
            "message": "Already optimal — recompression would increase size",
            "original_size": original_size,
            "attempted_size": new_size,
        }, status=400)

    # Replace original. If extension changes, write new file & remove old.
    src_dir = os.path.dirname(src_path)
    src_base, src_ext = os.path.splitext(os.path.basename(src_path))

    # If this file is shared (deduplicated) with other progress rows,
    # don't mutate the shared copy — write to a new file (copy-on-write).
    is_shared = MediaUploadProgress.objects.filter(
        final_path=src_path
    ).exclude(pk=progress.pk).exists()

    if is_shared:
        new_filename = f"{uuid.uuid4().hex[:8]}_{src_base}{new_ext}"
        new_path = os.path.join(src_dir, new_filename)
    elif src_ext.lower() != new_ext:
        new_filename = f"{src_base}{new_ext}"
        new_path = os.path.join(src_dir, new_filename)
    else:
        new_filename = os.path.basename(src_path)
        new_path = src_path

    try:
        with open(new_path, "wb") as f:
            f.write(new_data)
        if not is_shared and new_path != src_path and os.path.isfile(src_path):
            os.remove(src_path)
    except Exception as e:
        logger.exception("Replace failed for progress %s", progress_id)
        return JsonResponse({"success": False, "message": f"Replace failed: {e}"}, status=500)

    # Update DB
    progress.original_size = original_size
    progress.file_size = new_size
    progress.is_compressed = True
    progress.mime_type = new_mime
    progress.final_path = new_path
    # New compressed file has different bytes — invalidate hash so dedup recomputes naturally
    progress.content_hash = None
    if new_path != src_path:
        old_url = progress.final_url or ""
        if old_url:
            progress.final_url = old_url.rsplit("/", 1)[0] + "/" + new_filename
        progress.filename = new_filename
    progress.save()

    return JsonResponse({
        "success": True,
        "id": progress.id,
        "original_size": original_size,
        "new_size": new_size,
        "savings_pct": round((1 - new_size / original_size) * 100, 1),
        "final_url": progress.final_url,
        "filename": progress.filename,
    })
