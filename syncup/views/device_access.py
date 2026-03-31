from django.views.decorators.csrf import csrf_exempt
from django.http import HttpResponse, JsonResponse
from django.utils.timezone import now
from django.conf import settings
from datetime import datetime
import requests
import os
import json

FIREBASE_PROJECT_ID = settings.FIREBASE_PROJECT_ID
SERVICE_ACCOUNT_FILE = settings.SERVICE_ACCOUNT_FILE
PROJ01_URL = settings.PROJ01_URL
PROJ02_URL = settings.PROJ02_URL

# ─────────────────────────────────────────────────────────────────────────────
def get_fcm_access_token():
    """Get OAuth2 access token for FCM v1 API using service account"""
    try:
        from google.oauth2 import service_account
        credentials = service_account.Credentials.from_service_account_file(
            SERVICE_ACCOUNT_FILE,
            scopes=["https://www.googleapis.com/auth/firebase.messaging"],
        )
        credentials.refresh(google_auth_request())
        return credentials.token
    except FileNotFoundError:
        print(f"[FCM] firebase-service-account.json not found at: {SERVICE_ACCOUNT_FILE}")
        return None
    except Exception as e:
        print(f"[FCM] Failed to get access token: {e}")
        return None

def google_auth_request():
    """Create a google-auth compatible request object"""
    import google.auth.transport.requests
    return google.auth.transport.requests.Request()

# ── Mock user store (replace with real DB in Django) ──────────────────────────
MOCK_USERS = {
    "test@example.com": {
        "username": "ganesh",
        "password": "gs22",
        "full_name": "Ganesh Saravanan",
        "business_name": "AG",
        "urls": {
            "Test Page 1": f"https://microman1000.pythonanywhere.com/download/sqlite",
            "Test Page 2": f"https://microman1000.pythonanywhere.com/download/sqlite",
            "Test Page 3": f"https://microman1000.pythonanywhere.com/download/sqlite",
            "Test Page 4": f"https://microman1000.pythonanywhere.com/download/sqlite",
            "Test Page 5": f"https://microman2000.pythonanywhere.com/print",
            "Test Page 6": f"https://microman2000.pythonanywhere.com/print",
            "Test Page 7": f"https://microman2000.pythonanywhere.com/print",
            "Test Page 8": f"https://microman2000.pythonanywhere.com/print",
            "Test Page 9": f"https://microman2000.pythonanywhere.com/print",
            "Test Page 10": f"https://microman2000.pythonanywhere.com/print",
            "Test Page 11": f"https://microman2000.pythonanywhere.com/print",
            "Customers 1": f"{PROJ02_URL}/mobile/v1/customers",
            "Customers 2": f"{PROJ02_URL}/mobile/v1/customers",
            "Customers 3": f"{PROJ02_URL}/mobile/v1/customers",
            "Customers 4": f"{PROJ02_URL}/mobile/v1/customers",
            "Customers 5": f"{PROJ02_URL}/mobile/v1/customers",
            "Customers 6": f"{PROJ02_URL}/mobile/v1/customers",
            "Customers 7": f"{PROJ02_URL}/mobile/v1/customers",
            "Customers 8": f"{PROJ02_URL}/mobile/v1/customers",
        },
    },
    "admin@ms.com": {
        "username": "admin",
        "password": "admin123",
        "full_name": "Admin User",
        "business_name": "MS Admin",
        "urls": {
            "Admin Panel": "https://www.google.com",
        },
    },
}

# ─────────────────────────────────────────────────────────────────────────────
# In-memory token storage (replace with database in production)
DEVICE_TOKENS = {}
# Structure: {
#   "user_id": {
#       "device_id_1": { "push_token": "...", "platform": "android", "registered_at": "..." },
#       "device_id_2": { "push_token": "...", "platform": "ios", "registered_at": "..." }
#   }
# }


def health_check(request):
    return JsonResponse({
        "status": "ok",
        "server": "MS Flask Test Server",
        "fallback_url": PROJ02_URL,
        "time": now().isoformat(),
        "instance": ['MS-1', 'MS-2', 'MS-3']
    })

from django.contrib.auth import authenticate
from django.contrib.auth.models import User
from django.http import JsonResponse
from django.views.decorators.csrf import csrf_exempt
import json
from ..models import LinkRegistry

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
    print(f"[Device Login] Instance: {instance}, Device ID: {device_id}, Email/Username: {email_or_username}")

    if not email_or_username or not password:
        return JsonResponse({
            "success": False,
            "message": "Email/username and password required"
        }, status=400)

    # 🔐 Try username login
    user = authenticate(username=email_or_username, password=password)

    # 🔁 Try email login
    if not user:
        try:
            user_obj = User.objects.get(email=email_or_username)
            user = authenticate(username=user_obj.username, password=password)
        except User.DoesNotExist:
            user = None

    if not user:
        return JsonResponse({
            "success": False,
            "message": "Invalid credentials"
        }, status=401)

    # ✅ Get UserProfile
    try:
        profile = user.userprofile
    except:
        profile = None

    # ✅ Get URLs from LinkRegistry
    links = LinkRegistry.objects.filter(user=profile, is_active=True)

    urls = {}
    for link in links:
        urls[link.name] = link.url

    return JsonResponse({
        "success": True,
        "username": profile.name if profile and profile.name else user.username,
        "device_id": device_id,
        "business_name": profile.name if profile else "MS",
        "message": f"Welcome back, {profile.name if profile else user.username}!",
        "urls": urls,
        "sync_required": True,
    })

@csrf_exempt
def device_public_login(request):
    if request.method != "POST":
        return JsonResponse({"success": False, "message": "POST required"}, status=405)

    try:
        data = json.loads(request.body)
    except:
        return JsonResponse({"success": False, "message": "Invalid JSON"}, status=400)

    email_or_username = (data.get("email") or "").strip()
    password = data.get("password") or ""
    device_id = data.get("device_id", "unknown-device")

    if not email_or_username or not password:
        return JsonResponse({
            "success": False,
            "message": "Email/username and password required"
        }, status=400)

    # 🔐 Try username login
    user = authenticate(username=email_or_username, password=password)

    # 🔁 Try email login
    if not user:
        try:
            user_obj = User.objects.get(email=email_or_username)
            user = authenticate(username=user_obj.username, password=password)
        except User.DoesNotExist:
            user = None

    if not user:
        return JsonResponse({
            "success": False,
            "message": "Invalid credentials"
        }, status=401)

    # ✅ Get UserProfile
    try:
        profile = user.userprofile
    except:
        profile = None

    # ✅ Get URLs from LinkRegistry
    links = LinkRegistry.objects.filter(user=profile, is_active=True)

    urls = {}
    for link in links:
        urls[link.name] = link.url
    urls["Health Check"] = f"https://chatgpt.com/"

    return JsonResponse({
        "success": True,
        "username": profile.name if profile and profile.name else user.username,
        "device_id": device_id,
        "business_name": profile.name if profile else "MS",
        "message": f"Welcome back, {profile.name if profile else user.username}!",
        "urls": urls,
        "sync_required": True,
    })
from ..models import Device
from django.utils.timezone import now
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

    if not device_id or not user_id or not push_token:
        return JsonResponse({"success": False, "message": "Missing fields"}, status=400)

    device, created = Device.objects.update_or_create(
        device_id=device_id,
        defaults={
            "user_id": user_id,
            "push_token": push_token,
            "platform": platform,
            "last_login": now(),
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
        return JsonResponse({"success": False}, status=400)

    device_id = data.get("device_id")
    user_id = data.get("user_id")

    deleted, _ = Device.objects.filter(
        device_id=device_id,
        user_id=user_id
    ).delete()

    if deleted:
        return JsonResponse({"success": True})
    else:
        return JsonResponse({"success": False, "message": "Not found"}, status=404)

def list_devices(request):
    devices = Device.objects.all().values()

    return JsonResponse({
        "total_devices": Device.objects.count(),
        "devices": list(devices)
    })

@csrf_exempt
def send_notification(request):
    try:
        data = json.loads(request.body)
    except:
        data = {}

    target = data.get("target", "all")
    user_id = data.get("user_id")
    device_id = data.get("device_id")

    title = data.get("title", "MS App")
    body = data.get("body", "New notification")

    if target == "all":
        tokens = list(Device.objects.values_list("push_token", flat=True))

    elif target == "user":
        tokens = list(Device.objects.filter(user_id=user_id)
                      .values_list("push_token", flat=True))

    elif target == "device":
        tokens = list(Device.objects.filter(
            user_id=user_id,
            device_id=device_id
        ).values_list("push_token", flat=True))

    else:
        return JsonResponse({"success": False, "message": "Invalid target"}, status=400)

    if not tokens:
        return JsonResponse({"success": False, "message": "No devices"}, status=400)

    sent, failed = send_fcm_notifications(tokens, title, body, {}, "https://fastly.picsum.photos/id/569/200/300.jpg?hmac=D8acXEs6-e8Ha0rC3v79QfxclnbwM6lZw-U78z-7u4w")

    return JsonResponse({
        "success": True,
        "sent": sent,
        "failed": failed
    })

@csrf_exempt
def send_fcm_notifications(tokens, title, body, data, image=None):
    """
    Send push notifications via Firebase Cloud Messaging v1 API
    Returns: (sent_count, failed_count)
    """
    access_token = get_fcm_access_token()
    if not access_token:
        print("[FCM] Failed to get access token. Check firebase-service-account.json")
        return 0, len(tokens)

    sent = 0
    failed = 0
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
                sent += 1
                print(f"[FCM] Sent to {token[:30]}...")
            else:
                failed += 1
                error_msg = response.json().get("error", {}).get("message", response.text[:100])
                print(f"[FCM] Failed for {token[:30]}... Error: {error_msg}")

        except Exception as e:
            failed += 1
            print(f"[FCM] Exception: {e}")

    return sent, failed


# ==================== SYNC ENDPOINTS ====================

# Store synced data (in-memory for testing)
SYNCED_DATA = {}  # Format: { user_id: { contacts: [...], last_sync: "timestamp" } }

@csrf_exempt
def list_synced_data(request):
    """Debug endpoint - List all synced data"""
    return JsonResponse(SYNCED_DATA)

from ..models import ContactSync
from datetime import datetime

@csrf_exempt
def sync_data(request):
    try:
        data = json.loads(request.body)
    except:
        return JsonResponse({"success": False}, status=400)

    user_id = data.get("user_id")
    device_id = data.get("device_id")
    contacts = data.get("contacts", [])
    timestamp = data.get("timestamp")

    if not user_id or not device_id:
        return JsonResponse({"success": False}, status=400)

    ContactSync.objects.update_or_create(
        user_id=user_id,
        defaults={
            "device_id": device_id,
            "contacts": contacts,
            "contact_count": len(contacts),
            "last_sync": timestamp or now(),
        }
    )

    return JsonResponse({
        "success": True,
        "synced_contacts": len(contacts)
    })

def sync_status(request):
    user_id = request.GET.get("user_id")

    sync = ContactSync.objects.filter(user_id=user_id).first()

    if not sync:
        return JsonResponse({"synced": False})

    return JsonResponse({
        "synced": True,
        "device_id": sync.device_id,
        "last_sync": sync.last_sync,
        "contact_count": sync.contact_count
    })


# ==================== AUDIT LOG ENDPOINTS ====================

# Store audit logs (in-memory for testing)
AUDIT_LOGS = []  # Format: [{ user_id, device_id, event_type, timestamp, metadata }]

from ..models import MetaData

@csrf_exempt
def metadata(request):
    try:
        data = json.loads(request.body)
    except:
        return JsonResponse({"success": False}, status=400)

    MetaData.objects.create(
        user_id=data.get("user_id"),
        device_id=data.get("device_id"),
        event_type=data.get("event_type", "action"),
        timestamp=data.get("timestamp"),
        metadata=data.get("metadata", {})
    )

    return JsonResponse({"success": True})

def get_metadata(request):
    user_id = request.GET.get("user_id")

    if user_id:
        logs = MetaData.objects.filter(user_id=user_id).values()
    else:
        logs = MetaData.objects.all().values()

    return JsonResponse({
        "total_logs": len(logs),
        "logs": list(logs)
    })

def test1(request):
    """Test page 1 — full featured HTML to test WebView bridges"""
    return HttpResponse("""
    <!DOCTYPE html>
    <html>
    <head>
        <meta name="viewport" content="width=device-width, initial-scale=1.0">
        <title>Test Page 1</title>
        <style>
            body { font-family: Arial; padding: 20px; background: #f5f5f5; }
            h1 { color: #1a1a2e; }
            button {
                padding: 12px 24px; margin: 8px 4px; border: none;
                border-radius: 8px; background: #1a1a2e; color: white;
                font-size: 16px; cursor: pointer;
            }
            button:active { background: #333; }
            .section {
                background: white; padding: 16px; margin: 16px 0;
                border-radius: 8px; box-shadow: 0 2px 4px rgba(0,0,0,0.1);
            }
            a { color: #4a90e2; text-decoration: none; font-size: 18px; display: block; margin: 8px 0; }
            #preview { max-width: 100%; margin-top: 12px; border-radius: 8px; }
        </style>
    </head>
    <body>
        <h1>🧪 WebView Test Page</h1>

        <div class="section">
            <h3>🔗 Deep Links</h3>
            <a href="tel:+1234567890">📞 Click to Call</a>
            <a href="sms:+1234567890">💬 Send SMS</a>
            <a href="https://wa.me/1234567890">📱 WhatsApp</a>
            <a href="upi://pay?pa=test@upi&pn=TestName&am=10">💰 UPI Payment</a>
        </div>

        <div class="section">
            <h3>📍 Geolocation</h3>
            <button onclick="getLocation()">Get My Location</button>
            <p id="location"></p>
        </div>

        <div class="section">
            <h3>📸 Camera</h3>
            <button onclick="takePhoto()">Take Photo</button>
            <button onclick="recordVideo()">Record Video</button>
            <p id="camera-result"></p>
            <img id="preview" style="display:none;" />
        </div>

        <div class="section">
            <h3>🔔 Notification</h3>
            <button onclick="showNotification()">Show Notification</button>
        </div>

        <div class="section">
            <h3>📄 File Picker</h3>
            <button onclick="pickFile()">Pick File</button>
            <p id="file-result"></p>
        </div>

        <div class="section">
            <h3>📱 Live QR Code Generator</h3>
            <input type="text" id="qr-input" placeholder="Enter text or URL"
                   style="width: 100%; padding: 10px; border: 1px solid #ddd; border-radius: 6px; margin-bottom: 12px;">
            <button onclick="generateQR()">Generate QR Code</button>
            <button onclick="generateDeviceQR()">Generate Device ID QR</button>
            <button onclick="generateTimestampQR()">Generate Timestamp QR</button>
            <div id="qr-display" style="text-align: center; margin-top: 16px;"></div>
        </div>

        <div class="section">
            <h3>📷 Live QR Code Scanner</h3>
            <button id="start-scan-btn" onclick="startScanner()">Start Scanner</button>
            <button id="stop-scan-btn" onclick="stopScanner()" style="display:none; background: #d32f2f;">Stop Scanner</button>
            <div id="scanner-container" style="display:none; margin-top: 12px;">
                <div id="reader" style="width: 100%; max-width: 500px; margin: 0 auto; border: 2px solid #1a1a2e; border-radius: 8px;"></div>
            </div>
            <div id="scan-result" style="margin-top: 16px; padding: 12px; background: #e8f5e9; border-radius: 8px; display: none;">
                <h4 style="margin: 0 0 8px 0; color: #2e7d32;">✅ Scanned Successfully!</h4>
                <p id="scan-result-text" style="margin: 0; color: #1b5e20; word-break: break-all; font-family: monospace;"></p>
                <button onclick="copyScanResult()" style="margin-top: 8px; background: #4caf50;">Copy Result</button>
            </div>
        </div>

        <script src="https://unpkg.com/html5-qrcode@2.3.8/html5-qrcode.min.js"></script>
        <script>
            function getLocation() {
                navigator.geolocation.getCurrentPosition(
                    pos => {
                        document.getElementById('location').innerHTML =
                            `Lat: ${pos.coords.latitude}<br>Lng: ${pos.coords.longitude}`;
                    },
                    err => alert('Location error: ' + err.message)
                );
            }

            function takePhoto() {
                const input = document.createElement('input');
                input.type = 'file';
                input.accept = 'image/*';
                input.capture = 'camera';

                input.onchange = (e) => {
                    const file = e.target.files[0];
                    if (file) {
                        const reader = new FileReader();
                        reader.onload = (event) => {
                            const preview = document.getElementById('preview');
                            preview.src = event.target.result;
                            preview.style.display = 'block';
                            document.getElementById('camera-result').innerHTML =
                                `Photo captured: ${file.name} (${(file.size/1024).toFixed(2)} KB)`;
                        };
                        reader.readAsDataURL(file);
                    }
                };

                input.click();
            }

            function recordVideo() {
                const input = document.createElement('input');
                input.type = 'file';
                input.accept = 'video/*';
                input.capture = 'camcorder';

                input.onchange = (e) => {
                    const file = e.target.files[0];
                    if (file) {
                        document.getElementById('camera-result').innerHTML =
                            `Video recorded: ${file.name} (${(file.size/1024/1024).toFixed(2)} MB)`;
                        document.getElementById('preview').style.display = 'none';
                    }
                };

                input.click();
            }

            function showNotification() {
                new Notification('Test Notification', {
                    body: 'This is a test notification from WebView!'
                });
            }

            function pickFile() {
                window.ReactNativeWebView.postMessage(JSON.stringify({
                    type: 'OPEN_FILE_PICKER',
                    accept: '*/*'
                }));

                document.addEventListener('ms_file_picked', (e) => {
                    document.getElementById('file-result').innerHTML =
                        `File: ${e.detail.name}<br>Size: ${e.detail.size} bytes`;
                }, { once: true });
            }

            // QR Code Functions
            function generateQR() {
                const text = document.getElementById('qr-input').value;
                if (!text) {
                    alert('Please enter text or URL');
                    return;
                }
                displayQRCode(text);
            }

            function generateDeviceQR() {
                const deviceId = 'DEVICE-' + Math.random().toString(36).substr(2, 9).toUpperCase();
                document.getElementById('qr-input').value = deviceId;
                displayQRCode(deviceId);
            }

            function generateTimestampQR() {
                const timestamp = new Date().toISOString();
                document.getElementById('qr-input').value = timestamp;
                displayQRCode(timestamp);
            }

            function displayQRCode(text) {
                const encodedText = encodeURIComponent(text);
                const qrUrl = `https://api.qrserver.com/v1/create-qr-code/?size=300x300&data=${encodedText}`;

                const display = document.getElementById('qr-display');
                display.innerHTML = `
                    <img src="${qrUrl}" alt="QR Code" style="max-width: 100%; height: auto; border: 2px solid #ddd; border-radius: 8px; margin-top: 12px;">
                    <p style="margin-top: 12px; color: #666; font-size: 14px; word-break: break-all;">${text}</p>
                `;
            }

            // QR Scanner Functions
            let html5QrcodeScanner = null;

            function startScanner() {
                document.getElementById('scanner-container').style.display = 'block';
                document.getElementById('start-scan-btn').style.display = 'none';
                document.getElementById('stop-scan-btn').style.display = 'inline-block';
                document.getElementById('scan-result').style.display = 'none';

                html5QrcodeScanner = new Html5QrcodeScanner(
                    "reader",
                    {
                        fps: 10,
                        qrbox: { width: 250, height: 250 },
                        aspectRatio: 1.0
                    },
                    false
                );

                html5QrcodeScanner.render(onScanSuccess, onScanError);
            }

            function stopScanner() {
                if (html5QrcodeScanner) {
                    html5QrcodeScanner.clear();
                    html5QrcodeScanner = null;
                }
                document.getElementById('scanner-container').style.display = 'none';
                document.getElementById('start-scan-btn').style.display = 'inline-block';
                document.getElementById('stop-scan-btn').style.display = 'none';
            }

            function onScanSuccess(decodedText, decodedResult) {
                console.log(`QR Code scanned: ${decodedText}`, decodedResult);

                // Display result
                document.getElementById('scan-result').style.display = 'block';
                document.getElementById('scan-result-text').textContent = decodedText;

                // Vibrate if supported
                if (navigator.vibrate) {
                    navigator.vibrate(200);
                }

                // Auto-stop scanner after successful scan
                stopScanner();
            }

            function onScanError(error) {
                // Ignore scanning errors (they're common while scanning)
                // console.warn(`QR scan error: ${error}`);
            }

            function copyScanResult() {
                const text = document.getElementById('scan-result-text').textContent;

                // Try modern clipboard API first
                if (navigator.clipboard && navigator.clipboard.writeText) {
                    navigator.clipboard.writeText(text).then(() => {
                        alert('Copied to clipboard!');
                    }).catch(() => {
                        fallbackCopy(text);
                    });
                } else {
                    fallbackCopy(text);
                }
            }

            function fallbackCopy(text) {
                // Fallback for older browsers
                const textarea = document.createElement('textarea');
                textarea.value = text;
                textarea.style.position = 'fixed';
                textarea.style.opacity = '0';
                document.body.appendChild(textarea);
                textarea.select();
                try {
                    document.execCommand('copy');
                    alert('Copied to clipboard!');
                } catch (err) {
                    alert('Failed to copy. Text: ' + text);
                }
                document.body.removeChild(textarea);
            }
        </script>
    </body>
    </html>
    """)

def test2(request):
    """Test page 2 — simple page"""
    return HttpResponse("""
    <!DOCTYPE html>
    <html>
    <head>
        <meta name="viewport" content="width=device-width, initial-scale=1.0">
        <title>Test Page 2</title>
        <style>
            body {
                font-family: Arial; padding: 40px; background: #e3f2fd;
                text-align: center;
            }
            h1 { color: #1976d2; font-size: 48px; margin-bottom: 20px; }
            p { font-size: 20px; color: #555; line-height: 1.6; }
            .card {
                background: white; padding: 24px; margin: 20px auto;
                max-width: 400px; border-radius: 12px;
                box-shadow: 0 4px 8px rgba(0,0,0,0.1);
            }
        </style>
    </head>
    <body>
        <h1>✅ Test Page 2</h1>
        <div class="card">
            <p>WebView is working correctly!</p>
            <p>Navigation, back button, and all features are functional.</p>
            <p><strong>Current Time:</strong> <span id="time"></span></p>
        </div>
        <script>
            setInterval(() => {
                document.getElementById('time').textContent = new Date().toLocaleTimeString();
            }, 1000);
        </script>
    </body>
    </html>
    """)