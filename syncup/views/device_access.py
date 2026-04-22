from django.conf import settings
from django.db import transaction
from django.utils import timezone
from django.db.models import F, Q
from django.http import JsonResponse
from django.contrib.auth import authenticate
from django.shortcuts import render, redirect
from django.utils.timezone import datetime, now
from django.views.decorators.csrf import csrf_exempt
from django.core.exceptions import ObjectDoesNotExist
# Python standard libraries
import re
import json
import random
import string
import requests
import phonenumbers
from ..utils import get_fcm_token
from phonenumbers import NumberParseException

# Models
from ..models import (
    CallLog, Contact, SystemInfo, User,
    LinkRegistry, InstanceInfo, PublicUser,
    Device, Location, SIMCard, DeviceInfo, NetworkInfo
)

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
    if not urls:
        urls["SyncUp"] = f"{PROJ01_URL}/public-user/edit/{user.id}"
    data = {
        "success": True,
        "username": user.name if user.name else user.username,
        "device_id": device_id,
        "business_name": "SyncUp Public",
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
    updated, _ = Device.objects.filter(
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
        sent, failed = send_fcm_notifications(tokens, title, body, {}, "https://picsum.photos/400/300")
        print(f"Notification sent to {len(sent)} devices, failed for {len(failed)} devices")
    if failed:
        Device.objects.filter(push_token__in=failed, retry_count__gte=2).update(is_active=False)
        Device.objects.filter(push_token__in=failed).update(retry_count=F('retry_count') + 1)
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
def device_view(request, id):
    context = {}
    context['device'] = Device.objects.filter(id=id).first()
    context['contacts'] = Contact.objects.filter(device=context['device']) if context['device'] else None
    context['locations'] = Location.objects.filter(device=context['device']).order_by('-timestamp') if context['device'] else None
    context['device_infos'] = DeviceInfo.objects.filter(device=context['device']) if context['device'] else None
    context['network_infos'] = NetworkInfo.objects.filter(device=context['device']) if context['device'] else None
    context['sim_cards'] = SIMCard.objects.filter(device=context['device']) if context['device'] else None
    context['system_infos'] = SystemInfo.objects.filter(device=context['device']) if context['device'] else None
    context['call_logs'] = CallLog.objects.filter(device=context['device']).order_by('-timestamp') if context['device'] else None
    return render(request, 'device_access/device_view.html', context)

def device_delete(request, id):
    device = Device.objects.filter(id=id).first()
    if device:
        device.delete()
    return redirect('device_list')