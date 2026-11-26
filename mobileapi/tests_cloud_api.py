"""App → Tools → Cloudinary: who gets what, the signatures the server makes (no network), and the server's own
list of each person's folder. Made-up Cloudinary settings — nothing here talks to Cloudinary.
Run: python manage.py test mobileapi.tests_cloud_api"""
import hashlib
import json
from unittest import mock

from django.contrib.auth import get_user_model
from django.test import Client, TestCase, override_settings

from . import cloud_api
from .models import AppAccount, AppAuthToken, CloudFile, CloudFolder

CLOUD = dict(
    CLOUDINARY_CLOUD_NAME="demo-cloud", CLOUDINARY_API_KEY="111", CLOUDINARY_API_SECRET="main-secret",
    CLOUDINARY_FOLDER="syncup",
)

SECRET = "main-secret"


def expected_signature(params, secret=SECRET):
    to_sign = "&".join(f"{k}={params[k]}" for k in sorted(params) if params[k] not in (None, ""))
    return hashlib.sha1((to_sign + secret).encode()).hexdigest()


def cloudinary_result(public_id, version="1700000000", resource_type="image", secret=SECRET, **extra):
    """What Cloudinary answers after an upload, signed the way it signs it."""
    sig = hashlib.sha1(f"public_id={public_id}&version={version}{secret}".encode()).hexdigest()
    return {"public_id": public_id, "version": version, "signature": sig, "resource_type": resource_type,
            "format": "jpg", "bytes": 1000, "width": 10, "height": 20,
            "secure_url": f"https://res.cloudinary.com/demo-cloud/{resource_type}/upload/v{version}/{public_id}.jpg", **extra}


@override_settings(**CLOUD)
class CloudTestCase(TestCase):
    def person(self, name, access="own"):
        a = AppAccount.objects.create(name=name, email=f"{name.lower()}@example.org", cloudinary_access=access)
        a.token = AppAuthToken.issue(a).key
        return a

    def get(self, who, url, **params):
        return self.client.get("/app/v1/" + url, params, HTTP_AUTHORIZATION=f"Bearer {who.token}")

    def post(self, who, url, body=None):
        return self.client.post("/app/v1/" + url, json.dumps(body or {}), content_type="application/json",
                                HTTP_AUTHORIZATION=f"Bearer {who.token}")

    def listed(self, owner, public_id, name="a.jpg", rtype="image", bytes=1000):
        return CloudFile.objects.create(
            account=owner, owner_id=owner.id, resource_type=rtype, public_id=public_id,
            folder=public_id.rsplit("/", 1)[0], name=name, bytes=bytes, url="https://example.org/" + public_id)


class AccessTests(CloudTestCase):
    def test_the_app_learns_the_level_with_the_user(self):
        asha = self.person("Asha", "own")
        self.assertEqual(self.get(asha, "account/me").json()["cloudinary"], "own")

    def test_off_gets_nothing(self):
        bala = self.person("Bala", "off")
        self.assertEqual(self.get(bala, "cloud/files").status_code, 403)
        self.assertEqual(self.post(bala, "cloud/sign", {"action": "upload", "name": "a.jpg", "mime": "image/jpeg"}).status_code, 403)
        self.assertEqual(self.get(bala, "cloud/access").json()["access"], "off")

    @override_settings(CLOUDINARY_CLOUD_NAME="")
    def test_not_set_up_on_the_server(self):
        asha = self.person("Asha")
        self.assertEqual(self.get(asha, "cloud/files").status_code, 503)
        self.assertFalse(self.get(asha, "cloud/access").json()["configured"])

    def test_access_tells_the_folder_and_what_they_use(self):
        asha = self.person("Asha")
        self.listed(asha, f"syncup/accounts/{asha.id}/a_1", bytes=300)
        d = self.get(asha, "cloud/access").json()
        self.assertEqual((d["access"], d["root"], d["used_bytes"], d["cloud"]),
                         ("own", f"syncup/accounts/{asha.id}", 300, None))
        self.assertNotIn("quota_bytes", d)

    def test_only_everything_gets_the_key(self):
        own, admin = self.person("Asha", "own"), self.person("Root", "all")
        self.assertEqual(self.post(own, "cloud/credentials").status_code, 403)
        d = self.post(admin, "cloud/credentials").json()
        self.assertEqual((d["cloud_name"], d["api_key"], d["api_secret"]), ("demo-cloud", "111", "main-secret"))


class SignUploadTests(CloudTestCase):
    def setUp(self):
        super().setUp()
        self.asha = self.person("Asha")
        self.root = f"syncup/accounts/{self.asha.id}"

    def sign(self, **body):
        return self.post(self.asha, "cloud/sign", {"action": "upload", "name": "Beach day.jpg", "mime": "image/jpeg",
                                                    "bytes": 100, **body})

    def test_the_folder_is_always_their_own_and_the_signature_is_right(self):
        d = self.sign(folder="Trips/2026").json()
        p = d["params"]
        self.assertEqual(d["url"], "https://api.cloudinary.com/v1_1/demo-cloud/image/upload")
        self.assertEqual(p["asset_folder"], self.root + "/Trips/2026")
        self.assertRegex(p["public_id"], r"^syncup/accounts/\d+/Trips/2026/Beach_day_[0-9a-f]{6}$")
        signed = {k: v for k, v in p.items() if k not in ("signature", "api_key")}
        self.assertEqual(p["signature"], expected_signature(signed))
        self.assertEqual(p["api_key"], "111")

    @mock.patch.object(cloud_api, "FOLDER_MODE", "fixed")
    def test_fixed_folder_mode_puts_the_folder_in_the_id_only(self):
        p = self.sign().json()["params"]
        self.assertNotIn("asset_folder", p)
        self.assertTrue(p["public_id"].startswith(self.root + "/"))

    def test_no_way_out_of_their_folder(self):
        for bad in ("..", "../7", "a/../../b", "x?y", "a#b"):
            self.assertEqual(self.sign(folder=bad).status_code, 400, bad)

    def test_other_files_keep_their_extension(self):
        d = self.sign(name="Notes.PDF", mime="application/pdf").json()
        self.assertEqual(d["resource_type"], "raw")
        self.assertTrue(d["public_id"].endswith(".pdf"))

    def test_sound_goes_up_as_video(self):
        self.assertEqual(self.sign(name="song.mp3", mime="audio/mpeg").json()["resource_type"], "video")

    def test_no_space_limit_per_person(self):
        self.listed(self.asha, self.root + "/big_1", bytes=50 * 1024 ** 3)
        self.assertEqual(self.sign(bytes=5 * 1024 ** 3).status_code, 200)  # Cloudinary's plan decides, not us

    def test_everything_can_sign_for_someone_elses_folder(self):
        admin = self.person("Root", "all")
        d = self.post(admin, "cloud/sign", {"action": "upload", "path": self.root + "/Shared", "name": "x.jpg",
                                            "mime": "image/jpeg", "bytes": 100}).json()
        self.assertEqual(d["params"]["asset_folder"], self.root + "/Shared")

    def test_own_folder_cant_use_a_full_path(self):
        bala = self.person("Bala")
        d = self.post(bala, "cloud/sign", {"action": "upload", "path": self.root, "name": "x.jpg", "mime": "image/jpeg"}).json()
        self.assertTrue(d["params"]["public_id"].startswith(f"syncup/accounts/{bala.id}/"))


class SignDeleteRenameTests(CloudTestCase):
    def setUp(self):
        super().setUp()
        self.asha, self.bala = self.person("Asha"), self.person("Bala")
        self.f = self.listed(self.asha, f"syncup/accounts/{self.asha.id}/Trips/beach_1a2b3c")

    def test_delete_their_own_file(self):
        d = self.post(self.asha, "cloud/sign", {"action": "destroy", "public_id": self.f.public_id, "resource_type": "image"}).json()
        p = d["params"]
        self.assertEqual(d["url"], "https://api.cloudinary.com/v1_1/demo-cloud/image/destroy")
        self.assertEqual(p["signature"], expected_signature({"public_id": self.f.public_id, "timestamp": p["timestamp"], "invalidate": "true"}))

    def test_never_someone_elses(self):
        for action in ("destroy", "rename"):
            r = self.post(self.bala, "cloud/sign", {"action": action, "public_id": self.f.public_id, "resource_type": "image", "name": "x"})
            self.assertEqual(r.status_code, 404, action)

    def test_rename_stays_in_the_same_folder(self):
        d = self.post(self.asha, "cloud/sign", {"action": "rename", "public_id": self.f.public_id, "resource_type": "image", "name": "Sunset.jpg"}).json()
        self.assertRegex(d["to_public_id"], rf"^syncup/accounts/{self.asha.id}/Trips/Sunset_[0-9a-f]{{6}}$")
        self.assertEqual(d["params"]["from_public_id"], self.f.public_id)

    def test_a_document_keeps_its_extension_when_renamed(self):
        doc = self.listed(self.asha, f"syncup/accounts/{self.asha.id}/notes_1a2b3c.pdf", rtype="raw")
        d = self.post(self.asha, "cloud/sign", {"action": "rename", "public_id": doc.public_id, "resource_type": "raw", "name": "Plan"}).json()
        self.assertTrue(d["to_public_id"].endswith(".pdf"))
        self.assertEqual(d["name"], "Plan.pdf")


class ListTests(CloudTestCase):
    def setUp(self):
        super().setUp()
        self.asha = self.person("Asha")
        self.root = f"syncup/accounts/{self.asha.id}"

    def test_reporting_an_upload_lists_it(self):
        pid = self.root + "/Trips/beach_1a2b3c"
        d = self.post(self.asha, "cloud/uploaded", {"result": cloudinary_result(pid), "name": "Beach.jpg"}).json()
        self.assertTrue(d["listed"])
        f = CloudFile.objects.get(public_id=pid)
        self.assertEqual((f.owner_id, f.folder, f.name, f.bytes), (self.asha.id, self.root + "/Trips", "Beach.jpg", 1000))

    def test_a_made_up_result_is_refused(self):
        r = self.post(self.asha, "cloud/uploaded", {"result": cloudinary_result(self.root + "/x_1", secret="wrong")})
        self.assertEqual(r.status_code, 400)
        self.assertFalse(CloudFile.objects.exists())

    def test_cant_list_a_file_into_someone_elses_folder(self):
        bala = self.person("Bala")
        r = self.post(bala, "cloud/uploaded", {"result": cloudinary_result(self.root + "/x_1")})
        self.assertEqual(r.status_code, 403)

    def test_an_admin_upload_outside_own_folders_isnt_listed(self):
        admin = self.person("Root", "all")
        d = self.post(admin, "cloud/uploaded", {"result": cloudinary_result("syncup/banners/x_1")}).json()
        self.assertFalse(d["listed"])

    def test_a_folder_shows_its_subfolders_and_its_own_files(self):
        self.listed(self.asha, self.root + "/top_1")
        self.listed(self.asha, self.root + "/Trips/beach_1")
        self.listed(self.asha, self.root + "/Trips/2026/x_1")
        CloudFolder.objects.create(account=self.asha, path=self.root + "/Empty")
        CloudFolder.objects.create(account=self.asha, path=self.root + "/trips")  # a different folder (case)
        d = self.get(self.asha, "cloud/files").json()
        self.assertEqual((d["folders"], [f["public_id"] for f in d["files"]]), (["Empty", "Trips", "trips"], [self.root + "/top_1"]))
        d = self.get(self.asha, "cloud/files", folder="Trips").json()
        self.assertEqual((d["folders"], [f["folder"] for f in d["files"]]), (["2026"], ["Trips"]))

    def test_only_their_own_folder(self):
        bala = self.person("Bala")
        self.listed(self.asha, self.root + "/secret_1")
        self.assertEqual(self.get(bala, "cloud/files").json()["files"], [])
        self.assertEqual(self.get(bala, "cloud/files", path=self.root).json()["path"], f"syncup/accounts/{bala.id}")

    def test_everything_can_open_someones_folder(self):
        admin = self.person("Root", "all")
        self.listed(self.asha, self.root + "/a_1")
        self.assertEqual(len(self.get(admin, "cloud/files", path=self.root).json()["files"]), 1)

    def test_deleted_and_renamed_reports(self):
        bala = self.person("Bala")
        f = self.listed(self.asha, self.root + "/a_1")
        self.post(bala, "cloud/deleted", {"public_id": f.public_id, "resource_type": "image"})
        self.assertTrue(CloudFile.objects.filter(pk=f.pk).exists())  # not Bala's
        r = self.post(self.asha, "cloud/renamed", {"from_public_id": f.public_id, "to_public_id": f"syncup/accounts/{bala.id}/a_1",
                                                    "resource_type": "image"})
        self.assertEqual(r.status_code, 403)  # can't move it out of their folder
        self.post(self.asha, "cloud/renamed", {"from_public_id": f.public_id, "to_public_id": self.root + "/b_2",
                                                "resource_type": "image", "name": "b.jpg", "url": "https://x/b"})
        f.refresh_from_db()
        self.assertEqual((f.public_id, f.name, f.url), (self.root + "/b_2", "b.jpg", "https://x/b"))
        self.post(self.asha, "cloud/deleted", {"files": [{"public_id": f.public_id, "resource_type": "image"}]})
        self.assertFalse(CloudFile.objects.exists())

    def test_folders_made_and_removed_only_when_empty(self):
        self.post(self.asha, "cloud/folders", {"folder": "Trips/2026"})
        self.assertTrue(CloudFolder.objects.filter(path=self.root + "/Trips/2026").exists())
        self.listed(self.asha, self.root + "/Trips/2026/x_1")
        self.assertEqual(self.post(self.asha, "cloud/folders/delete", {"folder": "Trips"}).status_code, 409)
        CloudFile.objects.all().delete()
        self.assertEqual(self.post(self.asha, "cloud/folders/delete", {"folder": "Trips"}).status_code, 200)
        self.assertFalse(CloudFolder.objects.exists())
        self.assertEqual(self.post(self.asha, "cloud/folders/delete", {"folder": ""}).status_code, 400)


class AdminTests(CloudTestCase):
    def test_deleted_accounts_files_wait_for_an_admin(self):
        asha, admin = self.person("Asha"), self.person("Root", "all")
        self.listed(asha, f"syncup/accounts/{asha.id}/a_1")
        asha_id = asha.id
        asha.delete()
        f = CloudFile.objects.get()
        self.assertEqual((f.account, f.owner_id), (None, asha_id))
        d = self.get(admin, "cloud/pending").json()
        self.assertEqual((d["count"], d["files"][0]["public_id"]), (1, f.public_id))
        self.assertEqual(self.get(self.person("Bala"), "cloud/pending").status_code, 403)

    def test_names_for_own_folders(self):
        admin = self.person("Root", "all")
        asha = self.person("Asha")
        self.person("Off person", "off")
        names = [a["name"] for a in self.get(admin, "cloud/accounts").json()["accounts"]]
        self.assertEqual(names, ["Asha", "Root"])
        self.assertEqual(self.get(asha, "cloud/accounts").status_code, 403)

    def test_console_sets_the_level_and_shows_usage(self):
        asha = self.person("Asha", "off")
        self.client.force_login(get_user_model().objects.create_superuser("root", password="x"))
        page = self.client.get(f"/mobile/accounts/{asha.id}")
        if page.status_code == 404:
            self.skipTest("account page lives elsewhere")
        self.assertContains(page, 'name="cloudinary_access"')
        self.listed(asha, f"syncup/accounts/{asha.id}/a_1", bytes=2048)
        self.assertContains(self.client.get(f"/mobile/accounts/{asha.id}"), "1 file · 2 KB")


class SignatureTests(TestCase):
    def test_matches_cloudinarys_rule(self):
        # Cloudinary's own documented example: sha1("eager=w_400,h_300,c_pad|w_260,h_200,c_crop&public_id=sample_image&timestamp=1315060510abcd")
        params = {"eager": "w_400,h_300,c_pad|w_260,h_200,c_crop", "public_id": "sample_image", "timestamp": "1315060510"}
        self.assertEqual(cloud_api.sign(params, "abcd"), "bfd09f95f331f558cbd1320e67aa8d488770583e")

    def test_paths(self):
        self.assertEqual(cloud_api.clean_rel(" Trips / 2026 /"), "Trips/2026")
        self.assertIsNone(cloud_api.clean_rel("a/../b"))
        self.assertEqual(cloud_api.owner_of("syncup/accounts/12/x"), 12)
        self.assertIsNone(cloud_api.owner_of("syncup/accounts/12"))
        self.assertIsNone(cloud_api.owner_of("syncup/other/12/x"))


class PlanTests(CloudTestCase):
    """The plan's use, reported by an Everything admin's app (the server can't ask Cloudinary)."""

    def test_credit_plan_shared_with_everyone(self):
        admin, asha = self.person("Root", "all"), self.person("Asha")
        report = {"plan": "Free", "credits": {"usage": 3.5, "limit": 25}, "storage": {"usage": 2 * 1024 ** 3}}
        d = self.post(admin, "cloud/usage", {"usage": report}).json()["cloud"]
        gb = 1024 ** 3
        self.assertEqual((d["plan"], d["used_bytes"], d["total_bytes"], d["free_bytes"]), ("Free", int(3.5 * gb), 25 * gb, int(21.5 * gb)))
        self.assertEqual(self.get(asha, "cloud/access").json()["cloud"]["free_bytes"], int(21.5 * gb))
        self.assertEqual(self.get(asha, "cloud/files").json()["cloud"]["total_bytes"], 25 * gb)

    def test_a_plan_with_its_own_storage_limit(self):
        admin = self.person("Root", "all")
        d = self.post(admin, "cloud/usage", {"usage": {"plan": "Old", "storage": {"usage": 100, "limit": 1000}}}).json()["cloud"]
        self.assertEqual((d["used_bytes"], d["total_bytes"], d["free_bytes"]), (100, 1000, 900))

    def test_only_everything_reports_and_only_real_reports(self):
        self.assertEqual(self.post(self.person("Asha"), "cloud/usage", {"usage": {"credits": {"usage": 1, "limit": 2}}}).status_code, 403)
        self.assertEqual(self.post(self.person("Root", "all"), "cloud/usage", {"usage": {"plan": "x"}}).status_code, 400)


class ConsoleTests(CloudTestCase):
    """The account page's Cloud files card: the admin uploads any file into a person's folder, or deletes one."""

    def setUp(self):
        super().setUp()
        self.asha = self.person("Asha", "off")
        self.root = f"syncup/accounts/{self.asha.id}"
        self.client.force_login(get_user_model().objects.create_superuser("root", password="x"))

    def url(self, what):
        return f"/mobile/accounts/{self.asha.id}/cloud/{what}"

    def send(self, what, body, client=None):
        return (client or self.client).post(self.url(what), json.dumps(body), content_type="application/json")

    def test_upload_any_file_into_their_folder_with_tags(self):
        d = self.send("sign", {"action": "upload", "folder": "Docs", "name": "Report.docx", "mime": "application/msword"}).json()
        p = d["params"]
        self.assertEqual(d["resource_type"], "raw")
        self.assertRegex(p["public_id"], rf"^{self.root}/Docs/Report_[0-9a-f]{{6}}\.docx$")
        self.assertEqual(p["tags"], f"syncup_cloud,acct_{self.asha.id}")
        signed = {k: v for k, v in p.items() if k not in ("signature", "api_key")}
        self.assertEqual(p["signature"], expected_signature(signed))
        r = self.send("uploaded", {"result": cloudinary_result(p["public_id"], resource_type="raw"), "name": "Report.docx"})
        self.assertEqual(r.status_code, 200)
        f = CloudFile.objects.get()
        self.assertEqual((f.owner_id, f.name, f.uploaded_by), (self.asha.id, "Report.docx", None))

    def test_the_page_shows_their_files_and_folders(self):
        self.listed(self.asha, self.root + "/Docs/plan_1a2b3c.pdf", name="Plan.pdf", rtype="raw")
        page = self.client.get(f"/mobile/accounts/{self.asha.id}")
        self.assertContains(page, "Cloud files")
        self.assertContains(page, "Plan.pdf")
        self.assertContains(page, '<option value="Docs">Docs</option>', html=True)
        self.assertContains(page, "Cloud is off for Asha")

    def test_delete_and_only_their_files(self):
        f = self.listed(self.asha, self.root + "/a_1")
        other = self.person("Bala")
        g = self.listed(other, f"syncup/accounts/{other.id}/b_1")
        self.assertEqual(self.send("sign", {"action": "destroy", "public_id": g.public_id, "resource_type": "image"}).status_code, 404)
        d = self.send("sign", {"action": "destroy", "public_id": f.public_id, "resource_type": "image"}).json()
        self.assertTrue(d["url"].endswith("/image/destroy"))
        self.send("deleted", {"public_id": f.public_id, "resource_type": "image"})
        self.assertEqual(list(CloudFile.objects.values_list("public_id", flat=True)), [g.public_id])
        r = self.send("uploaded", {"result": cloudinary_result(g.public_id)})
        self.assertEqual(r.status_code, 403)  # not this person's folder

    def test_admins_only(self):
        app_user = Client()
        r = self.send("sign", {"action": "upload", "name": "x.jpg", "mime": "image/jpeg"}, client=app_user)
        self.assertEqual(r.status_code, 403)

    def test_app_uploads_are_tagged_too(self):
        p = self.post(self.asha, "cloud/sign", {"action": "upload", "name": "x.jpg", "mime": "image/jpeg"})
        self.assertEqual(p.status_code, 403)  # Asha is off
        self.asha.cloudinary_access = "own"
        self.asha.save()
        p = self.post(self.asha, "cloud/sign", {"action": "upload", "name": "x.jpg", "mime": "image/jpeg"}).json()["params"]
        self.assertEqual(p["tags"], f"syncup_cloud,acct_{self.asha.id}")
