"""The server checks each person's Cloud folder with Cloudinary (cloud_sync.py): files put there outside SyncUp
join their list, so they can rename and delete them; files moved or deleted on Cloudinary follow. Cloudinary's
answers are made up here — nothing is sent anywhere.
Run: python manage.py test mobileapi.tests_cloud_sync"""
from datetime import timedelta
from unittest import mock

import requests
from django.core.cache import cache
from django.test import override_settings
from django.utils import timezone

from .models import CloudFile
from .tests_cloud_api import CLOUD, CloudTestCase


def res(pid, folder, name="Beach", fmt="jpg", rtype="image", **extra):
    """One file as Cloudinary's Search API describes it."""
    return {"public_id": pid, "asset_folder": folder, "display_name": name, "format": fmt, "resource_type": rtype,
            "type": "upload", "bytes": 2048, "width": 800, "height": 600, "version": 1764150000,
            "secure_url": f"https://res.cloudinary.com/demo-cloud/{rtype}/upload/v1764150000/{pid}", **extra}


@override_settings(**CLOUD)
class SyncTests(CloudTestCase):
    def setUp(self):
        cache.clear()
        self.asha = self.person("Asha")
        self.root = f"syncup/accounts/{self.asha.id}"
        self.pages = []   # Cloudinary's answers, in order
        self.asked = []

        def post(path, body):
            self.asked.append((path, body))
            return self.pages.pop(0)

        p = mock.patch("mobileapi.cloud_sync.cloudinary_post", side_effect=post)
        p.start()
        self.addCleanup(p.stop)
        q = mock.patch("mobileapi.cloud_sync.ping_cloud")
        self.ping = q.start()
        self.addCleanup(q.stop)

    def cloudinary_has(self, *resources, cursor=None):
        self.pages.append({"resources": list(resources), **({"next_cursor": cursor} if cursor else {})})

    def aged(self, *files):
        CloudFile.objects.filter(pk__in=[f.pk for f in files]).update(created_at=timezone.now() - timedelta(hours=1))

    def test_put_in_on_cloudinarys_website_then_theirs_to_rename_and_delete(self):
        self.cloudinary_has(res("kq3x9beach", self.root + "/Trips"))
        d = self.get(self.asha, "cloud/look").json()
        self.assertEqual(d["checked"], "checked")
        self.assertEqual(d["listed"], {"kq3x9beach": "Beach.jpg"})
        self.assertEqual(self.asked, [("resources/search", {
            "expression": f"asset_folder={self.root} OR asset_folder:{self.root}/*", "max_results": 500})])
        # The server knows it's in their folder now, so it signs a delete and a rename for it.
        r = self.post(self.asha, "cloud/sign", {"action": "destroy", "public_id": "kq3x9beach", "resource_type": "image"})
        self.assertEqual(r.status_code, 200)
        self.assertTrue(r.json()["url"].endswith("/image/destroy"))
        r = self.post(self.asha, "cloud/sign",
                      {"action": "rename", "public_id": "kq3x9beach", "resource_type": "image", "name": "Goa.jpg"})
        self.assertEqual(r.status_code, 200)
        self.ping.assert_called_once_with({self.asha.id})

    def test_older_apps_get_them_too(self):
        # App 10 reads only cloud/files: the check runs there as well, before the list is made.
        self.cloudinary_has(res("kq3x9beach", self.root))
        files = self.get(self.asha, "cloud/files", folder="").json()["files"]
        self.assertEqual([(f["public_id"], f["name"]) for f in files], [("kq3x9beach", "Beach.jpg")])

    def test_files_elsewhere_are_left_alone(self):
        bala = self.person("Bala")
        self.cloudinary_has(res("x1", f"syncup/accounts/{bala.id}"), res("x2", "devices/phone"),
                            res("x3", self.root + "0/a"))  # another account whose number starts the same
        self.get(self.asha, "cloud/look")
        self.assertFalse(CloudFile.objects.exists())

    def test_moved_or_deleted_on_cloudinary(self):
        kept = self.listed(self.asha, self.root + "/a_1", name="Mine.jpg")
        deleted = self.listed(self.asha, self.root + "/b_2")
        just_uploaded = self.listed(self.asha, self.root + "/c_3")  # Cloudinary's search may not have it yet
        self.aged(kept, deleted)
        self.cloudinary_has(res(self.root + "/a_1", self.root + "/Moved"))
        self.get(self.asha, "cloud/look")
        kept.refresh_from_db()
        self.assertEqual((kept.folder, kept.name), (self.root + "/Moved", "Mine.jpg"))  # follows, keeps its name
        self.assertFalse(CloudFile.objects.filter(pk=deleted.pk).exists())
        self.assertTrue(CloudFile.objects.filter(pk=just_uploaded.pk).exists())

    def test_moved_in_from_someone_else_becomes_theirs(self):
        bala = self.person("Bala")
        f = self.listed(bala, f"syncup/accounts/{bala.id}/p_1")
        self.cloudinary_has(res(f.public_id, self.root))
        self.get(self.asha, "cloud/look")
        f.refresh_from_db()
        self.assertEqual((f.owner_id, f.account_id, f.folder), (self.asha.id, self.asha.id, self.root))
        self.ping.assert_called_once_with({self.asha.id, bala.id})

    def test_nothing_changed_nothing_saved(self):
        f = self.listed(self.asha, self.root + "/a_1", name="Mine.jpg", bytes=2048)
        same = res(f.public_id, self.root)
        f.format, f.width, f.height, f.version, f.url = "jpg", 800, 600, "1764150000", same["secure_url"]
        f.save()
        self.cloudinary_has(same)
        self.assertEqual(self.get(self.asha, "cloud/look").json()["checked"], "checked")
        self.ping.assert_not_called()

    def test_every_page_is_read(self):
        self.cloudinary_has(res("a", self.root), cursor="next-1")
        self.cloudinary_has(res("b", self.root + "/Sub"))
        self.get(self.asha, "cloud/look")
        self.assertEqual(self.asked[1][1]["next_cursor"], "next-1")
        self.assertEqual(CloudFile.objects.count(), 2)

    def test_too_many_to_check_takes_nothing_off(self):
        old = self.listed(self.asha, self.root + "/old_1")
        self.aged(old)
        for i in range(10):
            self.cloudinary_has(res(f"p{i}", self.root), cursor=f"c{i}")
        self.get(self.asha, "cloud/look")
        self.assertTrue(CloudFile.objects.filter(pk=old.pk).exists())
        self.assertEqual(CloudFile.objects.count(), 11)

    def test_at_most_once_a_minute(self):
        self.cloudinary_has(res("a", self.root))
        self.get(self.asha, "cloud/look")
        self.get(self.asha, "cloud/files")
        self.assertEqual(self.get(self.asha, "cloud/look").json()["checked"], "recent")
        self.assertEqual(len(self.asked), 1)

    def test_cloudinary_unreachable_changes_nothing(self):
        f = self.listed(self.asha, self.root + "/a_1")
        self.aged(f)
        with mock.patch("mobileapi.cloud_sync.cloudinary_post", side_effect=requests.ConnectionError("proxy")):
            d = self.get(self.asha, "cloud/look").json()
        self.assertEqual(d["checked"], "unreachable")
        self.assertEqual(list(d["listed"]), [self.root + "/a_1"])

    def test_a_document_keeps_its_extension(self):
        self.cloudinary_has(res("notes_x1.pdf", self.root, name="notes", fmt="", rtype="raw"))
        self.get(self.asha, "cloud/look")
        self.assertEqual(CloudFile.objects.get().name, "notes.pdf")

    def test_an_admin_looking_at_someones_folder_doesnt_check_it(self):
        admin = self.person("Root", "all")
        self.get(admin, "cloud/files", path=self.root)
        self.assertEqual(self.asked, [])
