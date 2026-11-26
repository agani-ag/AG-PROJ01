"""Cloudinary's webhook notifications keep each person's Cloud folder list complete: whatever lands in
<base>/accounts/<id>/… shows in that account's app — uploaded, moved, renamed or deleted in Cloudinary itself too.
Made-up Cloudinary settings; nothing here talks to Cloudinary.
Run: python manage.py test mobileapi.tests_cloud_notify"""
import hashlib
import json
import time
from unittest import mock

from django.test import override_settings

from .models import CloudFile, CloudFolder
from .tests_cloud_api import CLOUD, SECRET, CloudTestCase


@override_settings(**CLOUD)
class NotifyTestCase(CloudTestCase):
    def setUp(self):
        self.asha = self.person("Asha")
        self.root = f"syncup/accounts/{self.asha.id}"
        p = mock.patch("mobileapi.cloud_notify.ping_cloud")
        self.ping = p.start()
        self.addCleanup(p.stop)

    def notify(self, payload, secret=SECRET, ts=None, algo=hashlib.sha1):
        body = json.dumps(payload).encode()
        ts = str(int(time.time()) if ts is None else ts)
        sig = algo(body + ts.encode() + secret.encode()).hexdigest()
        return self.client.post("/app/v1/cloud/notify", body, content_type="application/json",
                                HTTP_X_CLD_TIMESTAMP=ts, HTTP_X_CLD_SIGNATURE=sig)

    def upload(self, public_id, folder, **extra):
        return {"notification_type": "upload", "public_id": public_id, "resource_type": "image", "type": "upload",
                "asset_folder": folder, "display_name": "Beach", "format": "jpg", "bytes": 2048, "width": 800,
                "height": 600, "version": 1764150000,
                "secure_url": f"https://res.cloudinary.com/demo-cloud/image/upload/v1764150000/{public_id}.jpg", **extra}

    def asha_sees(self, folder=""):
        return self.get(self.asha, "cloud/files", folder=folder).json()


class SignatureTests(NotifyTestCase):
    def test_wrong_secret_old_or_missing_is_refused(self):
        self.assertEqual(self.notify(self.upload("x1", self.root), secret="nope").status_code, 401)
        self.assertEqual(self.notify(self.upload("x1", self.root), ts=int(time.time()) - 3 * 3600).status_code, 401)
        r = self.client.post("/app/v1/cloud/notify", b"{}", content_type="application/json")
        self.assertEqual(r.status_code, 401)
        self.assertFalse(CloudFile.objects.exists())

    def test_sha256_is_fine_too(self):
        self.assertEqual(self.notify(self.upload("x1", self.root), algo=hashlib.sha256).status_code, 200)
        self.assertTrue(CloudFile.objects.filter(public_id="x1").exists())

    def test_other_kinds_are_ignored(self):
        r = self.notify({"notification_type": "eager", "public_id": "x"})
        self.assertEqual(r.json(), {"ok": True, "ignored": "eager"})


class UploadTests(NotifyTestCase):
    def test_uploaded_in_cloudinary_into_their_folder_shows_in_their_app(self):
        # Cloudinary's own website: a random public id, the folder only in asset_folder.
        self.assertEqual(self.notify(self.upload("kq3x9beach", self.root + "/Trips")).status_code, 200)
        self.assertEqual(self.asha_sees()["folders"], ["Trips"])
        files = self.asha_sees("Trips")["files"]
        self.assertEqual([(f["public_id"], f["name"], f["bytes"]) for f in files], [("kq3x9beach", "Beach.jpg", 2048)])
        self.assertIn("/v1764150000/kq3x9beach.jpg", files[0]["url"])
        self.ping.assert_called_once_with({self.asha.id})

    def test_elsewhere_or_unknown_account_is_not_listed(self):
        self.notify(self.upload("a", "devices/phone-1"))
        self.notify(self.upload("b", "syncup/accounts/99999"))
        self.notify(self.upload("c", "syncup/accounts/abc"))
        self.assertFalse(CloudFile.objects.exists())
        self.ping.assert_not_called()

    def test_app_reported_first_keeps_its_name_and_no_second_ping(self):
        pid = self.root + "/Photo_2026_ab12cd"
        f = self.listed(self.asha, pid, name="Photo 2026-11-26 10.00.00.jpg", bytes=2048)
        f.format, f.width, f.height, f.version = "jpg", 800, 600, "1764150000"
        f.url = f"https://res.cloudinary.com/demo-cloud/image/upload/v1764150000/{pid}.jpg"
        f.save()
        self.notify(self.upload(pid, self.root, display_name="Photo_2026_ab12cd"))
        self.assertEqual(CloudFile.objects.get(public_id=pid).name, "Photo 2026-11-26 10.00.00.jpg")
        self.ping.assert_not_called()

    def test_notification_first_then_the_app_names_it(self):
        from .tests_cloud_api import cloudinary_result
        pid = self.root + "/Photo_2026_ab12cd"
        self.notify(self.upload(pid, self.root, display_name="Photo_2026_ab12cd"))
        with mock.patch("mobileapi.cloud_api.ping_cloud"):
            self.post(self.asha, "cloud/uploaded", {"result": cloudinary_result(pid), "name": "Photo 2026.jpg"})
        self.assertEqual(CloudFile.objects.get(public_id=pid).name, "Photo 2026.jpg")
        self.assertEqual(CloudFile.objects.count(), 1)

    def test_raw_file_keeps_its_extension(self):
        self.notify(self.upload("notes_x1.pdf", self.root, resource_type="raw", format="", display_name="notes"))
        self.assertEqual(CloudFile.objects.get(public_id="notes_x1.pdf").name, "notes.pdf")


class ChangeTests(NotifyTestCase):
    def setUp(self):
        super().setUp()
        self.notify(self.upload("p1", self.root + "/Trips"))
        self.ping.reset_mock()

    def move(self, pid, frm, to, name="Hill"):
        return self.notify({"notification_type": "move", "resources": {pid: {
            "resource_type": "image", "type": "upload", "from_asset_folder": frm, "to_asset_folder": to,
            "display_name": name}}})

    def test_deleted_in_cloudinary_leaves_the_list(self):
        self.notify({"notification_type": "delete", "resources": [
            {"resource_type": "image", "type": "upload", "public_id": "p1", "asset_folder": self.root + "/Trips"}]})
        self.assertFalse(CloudFile.objects.exists())
        self.ping.assert_called_once_with({self.asha.id})

    def test_moved_inside_out_and_in(self):
        self.move("p1", self.root + "/Trips", self.root)                 # within their folder
        self.assertEqual(CloudFile.objects.get(public_id="p1").folder, self.root)
        self.move("p1", self.root, "elsewhere")                          # out of it
        self.assertFalse(CloudFile.objects.exists())
        self.move("q9", "elsewhere", self.root + "/Hills")               # an unlisted file moved in
        f = CloudFile.objects.get(public_id="q9")
        self.assertEqual((f.folder, f.name, f.owner_id), (self.root + "/Hills", "Hill", self.asha.id))
        self.assertEqual(f.url, "https://res.cloudinary.com/demo-cloud/image/upload/q9")
        self.assertEqual(self.asha_sees("Hills")["files"][0]["public_id"], "q9")

    def test_moved_to_someone_else(self):
        bala = self.person("Bala")
        self.move("p1", self.root + "/Trips", f"syncup/accounts/{bala.id}")
        f = CloudFile.objects.get(public_id="p1")
        self.assertEqual((f.owner_id, f.account_id), (bala.id, bala.id))
        self.assertEqual(self.asha_sees("Trips")["files"], [])
        self.ping.assert_called_once_with({self.asha.id, bala.id})

    def test_renamed_public_id(self):
        self.notify({"notification_type": "rename", "resource_type": "image", "type": "upload",
                     "from_public_id": "p1", "to_public_id": "beach_day", "asset_folder": self.root + "/Trips"})
        f = CloudFile.objects.get()
        self.assertEqual(f.public_id, "beach_day")
        self.assertTrue(f.url.endswith("/v1764150000/beach_day.jpg"))

    def test_new_display_name_keeps_the_extension(self):
        self.notify({"notification_type": "resource_display_name_changed", "resources": {"p1": {
            "public_id": "p1", "resource_type": "image", "asset_folder": self.root + "/Trips",
            "previous_display_name": "Beach", "new_display_name": "Goa beach"}}})
        self.assertEqual(CloudFile.objects.get().name, "Goa beach.jpg")

    def test_folder_renamed_or_moved(self):
        CloudFolder.objects.create(account=self.asha, path=self.root + "/Trips/2026")
        self.notify({"notification_type": "move_or_rename_asset_folder",
                     "from_path": self.root + "/Trips", "to_path": self.root + "/Holidays"})
        self.assertEqual(CloudFile.objects.get().folder, self.root + "/Holidays")
        self.assertEqual(sorted(CloudFolder.objects.values_list("path", flat=True)),
                         [self.root + "/Holidays", self.root + "/Holidays/2026"])
        self.assertEqual(self.asha_sees()["folders"], ["Holidays"])

    def test_folders_made_and_deleted_in_cloudinary(self):
        self.notify({"notification_type": "create_folder", "folder_path": self.root + "/Empty", "folder_name": "Empty"})
        self.assertIn("Empty", self.asha_sees()["folders"])
        self.notify({"notification_type": "delete_folder", "folder_path": self.root + "/Empty"})
        self.assertNotIn("Empty", self.asha_sees()["folders"])
        self.notify({"notification_type": "create_folder", "folder_path": "devices/x"})
        self.assertFalse(CloudFolder.objects.filter(path="devices/x").exists())
