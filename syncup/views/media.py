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
from django.http import JsonResponse, HttpResponse, HttpResponseRedirect, FileResponse
from django.shortcuts import render, get_object_or_404
from django.urls import reverse
from django.utils.timezone import now
from django.utils.dateparse import parse_datetime
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_GET, require_POST
from django.db import transaction

from ..models import (
    Device, MediaFile, MediaDownloadRequest, MediaUploadProgress
)
from ..utils import get_fcm_token
from .. import storage_filebase as storage_filebase_mod

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

    # Batch metadata (all optional; defaults make a single-batch sync work)
    batch_index = int(data.get("batch_index") or 0)
    batch_total = int(data.get("batch_total") or 1)
    full_sync = bool(data.get("full_sync"))

    if not device_id:
        return JsonResponse({"success": False, "message": "device_id required"}, status=400)

    try:
        device = Device.objects.get(device_id=device_id)
    except Device.DoesNotExist:
        return JsonResponse({"success": False, "message": "Device not found"}, status=404)

    if not isinstance(files, list):
        return JsonResponse({"success": False, "message": "media_files must be a list"}, status=400)

    # Mark the start of a new sync session on the first batch
    if batch_index == 0:
        device.media_sync_started_at = now()
        device.save(update_fields=["media_sync_started_at"])

    # Build MediaFile objects in-memory, then bulk_create with update_conflicts
    sync_now = now()
    objs = []
    for f in files:
        file_id = f.get("id") or f.get("file_id")
        if not file_id:
            continue
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
            last_seen=sync_now,
        ))

    upserted = 0
    pruned = 0
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
                    "album", "thumbnail_base64", "last_seen",
                ],
                batch_size=500,
            )
            upserted = len(objs)

        # Prune only when the client signals the final batch with full_sync=true.
        # We delete files whose last_seen is older than the session start time —
        # i.e. files not present in any batch of this sync.
        if full_sync and device.media_sync_started_at:
            pruned = MediaFile.objects.filter(
                device=device,
                last_seen__lt=device.media_sync_started_at,
            ).delete()[0]

    total = MediaFile.objects.filter(device=device).count()
    return JsonResponse({
        "success": True,
        "device_id": device_id,
        "batch_index": batch_index,
        "batch_total": batch_total,
        "received": upserted,
        "pruned": pruned,
        "full_sync": full_sync,
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
    # Accept common field name variants from device clients (multipart)
    chunk_data = (
        request.FILES.get("chunk_data")
        or request.FILES.get("chunk")
        or request.FILES.get("file")
        or request.FILES.get("data")
        or (next(iter(request.FILES.values()), None))  # fallback: first uploaded file
    )

    # Fallback: device may send chunk as base64 / plain string in POST instead of multipart file
    chunk_b64 = None
    if not chunk_data:
        chunk_b64 = (
            request.POST.get("chunk_data")
            or request.POST.get("chunk")
            or request.POST.get("data")
            or request.POST.get("chunk_base64")
        )

    if not all([request_id, file_id, filename, chunk_index_raw, total_chunks_raw]):
        return JsonResponse({"success": False, "message": "Missing required fields"}, status=400)
    if not chunk_data and not chunk_b64:
        return JsonResponse({
            "success": False,
            "message": "chunk_data file required",
            "received_files": list(request.FILES.keys()),
            "received_post": list(request.POST.keys()),
        }, status=400)

    try:
        chunk_index = int(chunk_index_raw)
        total_chunks = int(total_chunks_raw)
    except ValueError:
        return JsonResponse({"success": False, "message": "Invalid chunk_index/total_chunks"}, status=400)
    if chunk_index < 0 or total_chunks <= 0 or chunk_index >= total_chunks:
        return JsonResponse({"success": False, "message": "Out-of-range chunk indices"}, status=400)

    # Locate the request (must be pre-registered when admin triggered FCM)
    # For share_intent uploads, auto-create a request on first chunk.
    download_req = MediaDownloadRequest.objects.filter(request_id=request_id).first()
    source = (request.POST.get("source") or "").lower()
    if not download_req:
        device_id_val = request.POST.get("device_id") or ""
        if source == "share_intent" and device_id_val:
            device = Device.objects.filter(device_id=device_id_val, is_active=True).first()
            if not device:
                return JsonResponse({"success": False, "message": "Unknown device_id"}, status=404)
            mime_type = request.POST.get("mime_type") or ""
            file_size_raw = request.POST.get("file_size") or "0"
            download_req = MediaDownloadRequest.objects.create(
                request_id=request_id,
                device=device,
                requested_files=[{
                    "file_id": file_id,
                    "filename": filename,
                    "mime_type": mime_type,
                    "file_size": file_size_raw,
                    "source": "share_intent",
                }],
                total_files=1,
                status="pending",
                storage_backend="filebase" if storage_filebase_mod.is_enabled() else "local",
            )
        else:
            return JsonResponse({"success": False, "message": "Unknown request_id"}, status=404)
    elif source == "share_intent":
        # Same share request with additional files — track them
        existing_ids = {f.get("file_id") for f in (download_req.requested_files or [])}
        if file_id not in existing_ids:
            mime_type = request.POST.get("mime_type") or ""
            file_size_raw = request.POST.get("file_size") or "0"
            download_req.requested_files.append({
                "file_id": file_id,
                "filename": filename,
                "mime_type": mime_type,
                "file_size": file_size_raw,
                "source": "share_intent",
            })
            download_req.total_files = len(download_req.requested_files)
            download_req.save(update_fields=["requested_files", "total_files"])

    # Sanitize filename
    safe_name = os.path.basename(filename).replace("\x00", "")
    if not safe_name:
        return JsonResponse({"success": False, "message": "Invalid filename"}, status=400)

    # Per-file tmp dir
    tmp_dir = os.path.join(MEDIA_TMP_ROOT, request_id, file_id)
    os.makedirs(tmp_dir, exist_ok=True)
    chunk_path = os.path.join(tmp_dir, f"{chunk_index:08d}.part")

    if chunk_data is not None:
        with open(chunk_path, "wb") as f:
            for blk in chunk_data.chunks():
                f.write(blk)
    else:
        # Decode base64 / data-URL string and write
        import base64
        s = chunk_b64 or ""
        if s.startswith("data:"):
            # strip "data:<mime>;base64,"
            s = s.split(",", 1)[-1]
        try:
            raw = base64.b64decode(s, validate=False)
        except Exception as e:
            return JsonResponse({
                "success": False,
                "message": f"Invalid base64 chunk_data: {e}",
            }, status=400)
        with open(chunk_path, "wb") as f:
            f.write(raw)

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
            # discard the new copy and reuse the existing storage location.
            existing_dup = (
                MediaUploadProgress.objects
                .filter(
                    request__device_id=download_req.device_id,
                    content_hash=content_hash,
                    is_complete=True,
                )
                .exclude(pk=progress.pk)
                .only("final_path", "final_url", "file_size", "mime_type",
                      "storage_backend", "s3_key", "ipfs_cid")
                .first()
            )

            dup_reused = False
            if existing_dup:
                if existing_dup.storage_backend == "filebase" and existing_dup.s3_key:
                    # Reuse the same S3 object — discard the local copy.
                    try:
                        os.remove(final_path)
                    except OSError:
                        pass
                    progress.storage_backend = "filebase"
                    progress.s3_key = existing_dup.s3_key
                    progress.ipfs_cid = existing_dup.ipfs_cid
                    progress.final_path = ""
                    progress.final_url = existing_dup.final_url
                    progress.file_size = existing_dup.file_size
                    progress.mime_type = existing_dup.mime_type
                    dup_reused = True
                elif existing_dup.final_path and os.path.isfile(existing_dup.final_path):
                    # Local-storage dedup
                    try:
                        os.remove(final_path)
                    except OSError:
                        pass
                    progress.storage_backend = "local"
                    progress.final_path = existing_dup.final_path
                    progress.final_url = existing_dup.final_url
                    progress.file_size = existing_dup.file_size
                    progress.mime_type = existing_dup.mime_type
                    dup_reused = True

            if not dup_reused:
                progress.file_size = file_size
                guessed_mime, _ = mimetypes.guess_type(final_path)
                progress.mime_type = guessed_mime or "application/octet-stream"

                # Upload to Filebase if this request asked for it AND it's configured.
                storage_filebase = storage_filebase_mod
                use_filebase = (
                    download_req.storage_backend == "filebase"
                    and storage_filebase.is_enabled()
                )
                if use_filebase:
                    s3_key = f"{download_req.device.device_id}/{unique_name}"
                    try:
                        s3_url, cid = storage_filebase.upload_file(
                            final_path, s3_key, progress.mime_type
                        )
                        progress.storage_backend = "filebase"
                        progress.s3_key = s3_key
                        progress.ipfs_cid = cid
                        # If we got the CID right away use IPFS; otherwise route
                        # through our redirect view which will resolve it lazily.
                        if cid:
                            progress.final_url = storage_filebase.ipfs_url(cid)
                        else:
                            progress.final_url = f"/device/media/serve/{progress.id}"
                        progress.final_path = ""  # no longer kept locally
                        # Remove local copy now that it's in Filebase
                        try:
                            os.remove(final_path)
                        except OSError:
                            pass
                    except Exception as e:
                        # On upload failure, fall back to local serving so we don't lose the file
                        logger.exception("Filebase upload failed for %s; falling back to local", s3_key)
                        progress.storage_backend = "local"
                        progress.final_path = final_path
                        progress.final_url = (
                            f"{settings.PROJ01_URL}{settings.MEDIA_URL}"
                            f"media_uploads/{download_req.device.device_id}/{unique_name}"
                        )
                        progress.error = f"Filebase upload failed: {e}"
                else:
                    progress.storage_backend = "local"
                    progress.final_path = final_path
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
    storage_backend = (data.get("storage_backend") or "local").lower()
    if storage_backend not in ("local", "filebase"):
        storage_backend = "local"
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
        storage_backend=storage_backend,
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
# 4b. Retry a pending/failed download request
# ====================================================================
@csrf_exempt
def media_retry_request(request):
    """Re-send FCM for incomplete files in an existing download request."""
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
        return JsonResponse({"success": False, "message": "Request not found"}, status=404)

    device = download_req.device
    if not device.is_active:
        return JsonResponse({"success": False, "message": "Device inactive"}, status=400)
    if not device.push_token:
        return JsonResponse({"success": False, "message": "Device has no push token"}, status=400)

    # Determine which files are incomplete (not yet uploaded)
    completed_file_ids = set(
        download_req.uploads
        .filter(is_complete=True)
        .values_list("file_id", flat=True)
    )
    all_files = download_req.requested_files  # [{file_id, file_uri, filename}]
    pending_files = [f for f in all_files if f.get("file_id") not in completed_file_ids]

    if not pending_files:
        return JsonResponse({"success": False, "message": "All files already completed"}, status=400)

    # Create a new request so the device handles it as fresh work
    new_request_id = f"req_{uuid.uuid4().hex[:16]}"
    new_req = MediaDownloadRequest.objects.create(
        request_id=new_request_id,
        device=device,
        requested_files=pending_files,
        total_files=len(pending_files),
        status="pending",
        storage_backend=download_req.storage_backend,
    )

    # Build FCM data
    if len(pending_files) == 1:
        fcm_data = {
            "type": "media_download_request",
            "request_id": new_request_id,
            "file_id": pending_files[0]["file_id"],
            "file_uri": pending_files[0]["file_uri"],
        }
    else:
        fcm_data = {
            "type": "media_download_request",
            "request_id": new_request_id,
            "files": json.dumps(pending_files),
        }

    ok, fcm_resp = _send_fcm_data_message(device.push_token, fcm_data)
    new_req.fcm_response = fcm_resp
    if not ok:
        new_req.status = "failed"
    new_req.save()

    # Mark old request as failed if it was still pending
    if download_req.status == "pending":
        download_req.status = "failed"
        download_req.save(update_fields=["status"])

    return JsonResponse({
        "success": ok,
        "new_request_id": new_request_id,
        "files_count": len(pending_files),
        "fcm_response": fcm_resp,
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
            "storage_backend", "s3_key", "ipfs_cid",
            "request__id", "request__device_id",
            "request__device__user_id", "request__device__platform",
            "request__device__device_id",
        )
        .order_by("-updated_at")
    )

    media_filter = request.GET.get("type", "")
    device_pk = request.GET.get("device")
    storage_filter = request.GET.get("storage", "")
    if device_pk and device_pk.isdigit():
        qs = qs.filter(request__device_id=int(device_pk))

    if media_filter == "image":
        qs = qs.filter(_image_filter_q())
    elif media_filter == "other":
        qs = qs.exclude(_image_filter_q())

    if storage_filter in ("local", "filebase"):
        qs = qs.filter(storage_backend=storage_filter)

    paginator = Paginator(qs, GALLERY_PAGE_SIZE)
    page = paginator.get_page(request.GET.get("page") or 1)

    items = []
    for p in page.object_list:
        items.append({
            "progress": p,
            "is_image": _is_image(p),
            "device": p.request.device,
            "serve_url": reverse("media_serve_file", args=[p.id]),
        })

    devices = Device.objects.all().only("id", "user_id", "platform", "device_id")

    # Storage summary stats
    from django.db.models import Sum
    all_complete = MediaUploadProgress.objects.filter(is_complete=True)
    local_agg = all_complete.filter(storage_backend="local").aggregate(
        count=Count("id"), size=Sum("file_size")
    )
    fb_agg = all_complete.filter(storage_backend="filebase").aggregate(
        count=Count("id"), size=Sum("file_size")
    )

    # Local disk usage — actual folder size + free space
    local_disk_used = 0
    try:
        for dirpath, _, filenames in os.walk(MEDIA_UPLOAD_ROOT):
            for fname in filenames:
                fpath = os.path.join(dirpath, fname)
                if os.path.isfile(fpath):
                    local_disk_used += os.path.getsize(fpath)
    except OSError:
        pass
    local_disk_free = 0
    try:
        import shutil as _shutil
        disk = _shutil.disk_usage(MEDIA_UPLOAD_ROOT)
        local_disk_free = disk.free
    except Exception:
        pass

    storage_stats = {
        "local_count": local_agg["count"] or 0,
        "local_size": local_agg["size"] or 0,
        "local_disk_used": local_disk_used,
        "local_disk_free": local_disk_free,
        "filebase_count": fb_agg["count"] or 0,
        "filebase_size": fb_agg["size"] or 0,
    }

    return render(request, "device_access/downloaded_files.html", {
        "items": items,
        "page": page,
        "devices": devices,
        "selected_device": int(device_pk) if (device_pk and device_pk.isdigit()) else None,
        "media_filter": media_filter,
        "storage_filter": storage_filter,
        "total_count": paginator.count,
        "storage_stats": storage_stats,
    })


# ====================================================================
# 7. Compress an already-downloaded image
# ====================================================================
@csrf_exempt
def compress_downloaded_image(request, progress_id: int):
    """Compress an image (replaces original). Works for local and Filebase storage."""
    if request.method != "POST":
        return JsonResponse({"success": False, "message": "POST required"}, status=405)

    progress = MediaUploadProgress.objects.filter(id=progress_id, is_complete=True).first()
    if not progress:
        return JsonResponse({"success": False, "message": "File not found"}, status=404)
    if progress.is_compressed:
        return JsonResponse({"success": False, "message": "Already compressed"}, status=400)
    if not _is_image(progress):
        return JsonResponse({"success": False, "message": "Not an image"}, status=400)

    storage_filebase = storage_filebase_mod
    is_filebase = progress.storage_backend == "filebase" and progress.s3_key

    # Get the source bytes into a temp local path (works for both backends)
    if is_filebase:
        tmp_src = os.path.join(MEDIA_TMP_ROOT, f"compress_{progress.id}_{uuid.uuid4().hex[:8]}")
        os.makedirs(MEDIA_TMP_ROOT, exist_ok=True)
        try:
            storage_filebase.download_to_path(progress.s3_key, tmp_src)
        except Exception as e:
            logger.exception("Failed to download S3 object for compress: %s", progress.s3_key)
            return JsonResponse({"success": False, "message": f"Failed to fetch source: {e}"}, status=500)
        src_path = tmp_src
    else:
        if not progress.final_path or not os.path.isfile(progress.final_path):
            return JsonResponse({"success": False, "message": "File missing on disk"}, status=404)
        src_path = progress.final_path

    original_size = os.path.getsize(src_path)

    try:
        with Image.open(src_path) as img:
            img.load()
            fmt = (img.format or "").upper()
            buffer = BytesIO()
            if fmt == "PNG":
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
        if is_filebase:
            try: os.remove(src_path)
            except OSError: pass
        return JsonResponse({"success": False, "message": f"Compression failed: {e}"}, status=500)

    new_data = buffer.getvalue()
    new_size = len(new_data)

    if new_size >= original_size:
        if is_filebase:
            try: os.remove(src_path)
            except OSError: pass
        return JsonResponse({
            "success": False,
            "message": "Already optimal — recompression would increase size",
            "original_size": original_size,
            "attempted_size": new_size,
        }, status=400)

    # ============= FILEBASE BACKEND =============
    if is_filebase:
        old_key = progress.s3_key
        old_base = os.path.splitext(os.path.basename(old_key))[0]

        # Copy-on-write if shared with another row
        is_shared = MediaUploadProgress.objects.filter(
            s3_key=old_key
        ).exclude(pk=progress.pk).exists()

        device_id = progress.request.device.device_id
        if is_shared:
            new_key = f"{device_id}/{uuid.uuid4().hex[:8]}_{old_base}{new_ext}"
        else:
            new_key = f"{device_id}/{old_base}{new_ext}"

        try:
            new_url, new_cid = storage_filebase.upload_bytes(new_data, new_key, new_mime)
        except Exception as e:
            logger.exception("Filebase upload failed during compress")
            try: os.remove(src_path)
            except OSError: pass
            return JsonResponse({"success": False, "message": f"Upload failed: {e}"}, status=500)

        # Delete the old S3 object only if no other rows reference it
        if not is_shared and new_key != old_key:
            storage_filebase.delete_key(old_key)

        # Cleanup local temp file
        try: os.remove(src_path)
        except OSError: pass

        progress.original_size = original_size
        progress.file_size = new_size
        progress.is_compressed = True
        progress.mime_type = new_mime
        progress.s3_key = new_key
        progress.ipfs_cid = new_cid
        if new_cid:
            progress.final_url = storage_filebase.ipfs_url(new_cid)
        else:
            progress.final_url = f"/device/media/serve/{progress.id}"
        progress.content_hash = None
        progress.filename = f"{old_base}{new_ext}"
        progress.save()

        return JsonResponse({
            "success": True,
            "id": progress.id,
            "original_size": original_size,
            "new_size": new_size,
            "savings_pct": round((1 - new_size / original_size) * 100, 1),
            "final_url": progress.final_url,
            "filename": progress.filename,
            "storage": "filebase",
        })

    # ============= LOCAL BACKEND (existing logic) =============
    src_dir = os.path.dirname(src_path)
    src_base, src_ext = os.path.splitext(os.path.basename(src_path))

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

    progress.original_size = original_size
    progress.file_size = new_size
    progress.is_compressed = True
    progress.mime_type = new_mime
    progress.final_path = new_path
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
        "storage": "local",
    })


# ====================================================================
# 8. Serve / redirect a Filebase-stored file (lazily resolves CID)
# ====================================================================
@require_GET
def media_serve_file(request, progress_id: int):
    """Resolve a stored file and serve it (local) or proxy it (Filebase).

    For Filebase rows:
      - If CID is known → 302 redirect to the dedicated IPFS gateway.
      - If CID is missing → try head_object once to resolve it.
      - If still no CID → stream the file through our server using S3 creds
        (never redirect to the raw S3 URL, which is always AccessDenied
        on private buckets).
    """
    progress = (
        MediaUploadProgress.objects
        .filter(id=progress_id, is_complete=True)
        .only("id", "storage_backend", "s3_key", "ipfs_cid", "final_url",
              "final_path", "mime_type", "filename")
        .first()
    )
    if not progress:
        return HttpResponse(status=404)

    if progress.storage_backend == "filebase" and progress.s3_key:
        storage_filebase = storage_filebase_mod
        cid = progress.ipfs_cid
        if not cid and storage_filebase.is_enabled():
            try:
                s3 = storage_filebase.get_client()
                head = s3.head_object(
                    Bucket=settings.FILEBASE_BUCKET, Key=progress.s3_key
                )
                cid = head.get("Metadata", {}).get("cid")
                if cid:
                    progress.ipfs_cid = cid
                    progress.final_url = storage_filebase.ipfs_url(cid)
                    progress.save(update_fields=["ipfs_cid", "final_url"])
            except Exception:
                logger.exception("CID lookup failed for progress %s", progress_id)
        if cid:
            return HttpResponseRedirect(storage_filebase.ipfs_url(cid))

        # No CID available — proxy the file through our server from S3.
        # This always works even on private buckets.
        if storage_filebase.is_enabled():
            try:
                s3 = storage_filebase.get_client()
                obj = s3.get_object(
                    Bucket=settings.FILEBASE_BUCKET, Key=progress.s3_key
                )
                ctype = progress.mime_type or obj.get("ContentType", "application/octet-stream")
                resp = HttpResponse(obj["Body"].read(), content_type=ctype)
                resp["Cache-Control"] = "public, max-age=3600"
                resp["Content-Disposition"] = f'inline; filename="{progress.filename or "file"}"'
                return resp
            except Exception:
                logger.exception("S3 proxy failed for progress %s", progress_id)
        return HttpResponse("File unavailable", status=502)

    # Local backend — stream the file directly
    if progress.final_path and os.path.isfile(progress.final_path):
        ctype = progress.mime_type or "application/octet-stream"
        resp = FileResponse(open(progress.final_path, "rb"), content_type=ctype)
        resp["Cache-Control"] = "public, max-age=3600"
        return resp
    return HttpResponse(status=404)


# ====================================================================
# 9. Backfill: re-resolve IPFS CIDs for any Filebase rows missing them
# ====================================================================
@csrf_exempt
def media_refresh_urls(request):
    """Iterate Filebase rows whose ipfs_cid is missing or whose final_url
    points at the (private) S3 endpoint, and re-fetch the CID."""
    storage_filebase = storage_filebase_mod
    if not storage_filebase.is_enabled():
        return JsonResponse({"success": False, "message": "Filebase not configured"}, status=400)

    rows = MediaUploadProgress.objects.filter(
        storage_backend="filebase", is_complete=True
    ).only("id", "s3_key", "ipfs_cid", "final_url")

    s3 = storage_filebase.get_client()
    fixed = 0
    failed = 0
    skipped = 0
    for r in rows:
        # Skip rows that already have a working IPFS URL on the configured gateway
        gw = getattr(settings, "FILEBASE_IPFS_GATEWAY", "ipfs.filebase.io")
        if r.ipfs_cid and r.final_url and gw and gw in (r.final_url or ""):
            skipped += 1
            continue
        if not r.s3_key:
            failed += 1
            continue
        try:
            head = s3.head_object(Bucket=settings.FILEBASE_BUCKET, Key=r.s3_key)
            cid = head.get("Metadata", {}).get("cid")
            if cid:
                r.ipfs_cid = cid
                r.final_url = storage_filebase.ipfs_url(cid)
                r.save(update_fields=["ipfs_cid", "final_url"])
                fixed += 1
            else:
                failed += 1
        except Exception:
            logger.exception("Refresh failed for progress %s", r.id)
            failed += 1

    return JsonResponse({
        "success": True,
        "fixed": fixed,
        "failed": failed,
        "skipped": skipped,
        "total": rows.count(),
    })


# ====================================================================
# 10. Filebase status / smoke test (diagnostics)
# ====================================================================
@require_GET
def filebase_status(request):
    """Quick diagnostics: is Filebase enabled? Can we connect? Try a tiny upload."""
    storage_filebase = storage_filebase_mod
    info = {
        "USE_FILEBASE_STORAGE": getattr(settings, "USE_FILEBASE_STORAGE", False),
        "FILEBASE_BUCKET": getattr(settings, "FILEBASE_BUCKET", None),
        "FILEBASE_ENDPOINT": getattr(settings, "FILEBASE_ENDPOINT", None),
        "FILEBASE_REGION": getattr(settings, "FILEBASE_REGION", None),
        "has_access_key": bool(getattr(settings, "FILEBASE_ACCESS_KEY", None)),
        "has_secret_key": bool(getattr(settings, "FILEBASE_SECRET_KEY", None)),
        "is_enabled": storage_filebase.is_enabled(),
    }
    if not info["is_enabled"]:
        info["error"] = "Filebase not enabled. Did you restart the server after editing .env?"
        return JsonResponse(info, status=400)

    if request.GET.get("test") == "1":
        try:
            test_key = f"_diag/probe_{uuid.uuid4().hex[:8]}.txt"
            url, cid = storage_filebase.upload_bytes(
                b"filebase smoke test", test_key, "text/plain"
            )
            info["test_upload"] = {"key": test_key, "url": url, "cid": cid}
            storage_filebase.delete_key(test_key)
            info["test_deleted"] = True
        except Exception as e:
            logger.exception("Filebase smoke test failed")
            info["test_error"] = str(e)
            return JsonResponse(info, status=500)
    return JsonResponse(info)


# ====================================================================
# 11. Delete a downloaded file (local + Filebase)
# ====================================================================
@csrf_exempt
def media_delete_file(request, progress_id: int):
    """Permanently delete a downloaded file from storage + DB."""
    if request.method != "POST":
        return JsonResponse({"success": False, "message": "POST required"}, status=405)

    progress = MediaUploadProgress.objects.filter(id=progress_id, is_complete=True).first()
    if not progress:
        return JsonResponse({"success": False, "message": "File not found"}, status=404)

    # Delete from storage
    if progress.storage_backend == "filebase" and progress.s3_key:
        # Only delete S3 object if no other row shares the same key
        shared = MediaUploadProgress.objects.filter(
            s3_key=progress.s3_key
        ).exclude(pk=progress.pk).exists()
        if not shared:
            storage_filebase = storage_filebase_mod
            if storage_filebase.is_enabled():
                storage_filebase.delete_key(progress.s3_key)
    elif progress.final_path and os.path.isfile(progress.final_path):
        # Only delete local file if no other row shares the same path
        shared = MediaUploadProgress.objects.filter(
            final_path=progress.final_path
        ).exclude(pk=progress.pk).exists()
        if not shared:
            try:
                os.remove(progress.final_path)
            except OSError:
                pass

    filename = progress.filename
    progress.delete()

    return JsonResponse({
        "success": True,
        "message": f"Deleted: {filename}",
    })


# ====================================================================
# 12. Move a local file to Filebase
# ====================================================================
@csrf_exempt
def media_move_to_filebase(request, progress_id: int):
    """Upload a locally-stored file to Filebase, then remove the local copy."""
    if request.method != "POST":
        return JsonResponse({"success": False, "message": "POST required"}, status=405)

    storage_filebase = storage_filebase_mod
    if not storage_filebase.is_enabled():
        return JsonResponse({"success": False, "message": "Filebase not configured"}, status=400)

    progress = MediaUploadProgress.objects.filter(id=progress_id, is_complete=True).first()
    if not progress:
        return JsonResponse({"success": False, "message": "File not found"}, status=404)
    if progress.storage_backend == "filebase":
        return JsonResponse({"success": False, "message": "Already on Filebase"}, status=400)
    if not progress.final_path or not os.path.isfile(progress.final_path):
        return JsonResponse({"success": False, "message": "Local file missing"}, status=404)

    # Build key
    device_id = progress.request.device.device_id
    safe_name = os.path.basename(progress.final_path)
    s3_key = f"{device_id}/{safe_name}"

    try:
        url, cid = storage_filebase.upload_file(
            progress.final_path, s3_key, progress.mime_type
        )
    except Exception as e:
        logger.exception("Move to Filebase failed for progress %s", progress_id)
        return JsonResponse({"success": False, "message": f"Upload failed: {e}"}, status=500)

    # Remove local file only if no other row references it
    local_path = progress.final_path
    shared = MediaUploadProgress.objects.filter(
        final_path=local_path
    ).exclude(pk=progress.pk).exists()
    if not shared:
        try:
            os.remove(local_path)
        except OSError:
            pass

    # Update DB
    progress.storage_backend = "filebase"
    progress.s3_key = s3_key
    progress.ipfs_cid = cid
    progress.final_path = ""
    if cid:
        progress.final_url = storage_filebase.ipfs_url(cid)
    else:
        progress.final_url = f"/device/media/serve/{progress.id}"
    progress.save()

    return JsonResponse({
        "success": True,
        "message": f"Moved to Filebase: {progress.filename}",
        "storage": "filebase",
        "final_url": progress.final_url,
        "ipfs_cid": cid,
    })
