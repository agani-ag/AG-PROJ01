from django.http import HttpResponse

# ==================== TEST ENDPOINTS ====================
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