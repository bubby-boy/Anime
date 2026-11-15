import importlib.util
import os
import re
import sqlite3
import sys
import tempfile
import unittest
from concurrent.futures import ThreadPoolExecutor
from functools import partial
from html.parser import HTMLParser
from pathlib import Path
from unittest.mock import patch


class PageElements(HTMLParser):
    def __init__(self, html):
        super().__init__()
        self.ids = set()
        self.assets = set()
        self.feed(html)

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        if "id" in attrs:
            self.ids.add(attrs["id"])
        for key in ("src", "href"):
            if attrs.get(key, "").startswith("/static/"):
                self.assets.add(attrs[key])


class ClosingConnection(sqlite3.Connection):
    def __exit__(self, *args):
        try:
            return super().__exit__(*args)
        finally:
            self.close()


class PageTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temp = tempfile.TemporaryDirectory()
        cls.addClassCleanup(cls.temp.cleanup)
        env = patch.dict(os.environ, {
            "DATABASE_PATH": str(Path(cls.temp.name) / "pages.sqlite3"),
            "FLASK_SECRET": "page-test-only",
            "COOKIE_SECURE": "0",
            "SECRET_PAGE_PASSWORD": "test-unlock",
            "ADMIN_PASSWORD": "",
            "ADMIN_ALLOWED_IPS": "127.0.0.1",
        })
        env.start()
        cls.addClassCleanup(env.stop)
        # Close test connections deterministically so Windows can remove the DB.
        connections = patch("sqlite3.connect", partial(sqlite3.connect, factory=ClosingConnection))
        connections.start()
        cls.addClassCleanup(connections.stop)
        cls.root = Path(__file__).resolve().parent
        spec = importlib.util.spec_from_file_location("mathlab_page_test", cls.root / "app.py")
        cls.module = importlib.util.module_from_spec(spec)
        sys.modules[spec.name] = cls.module
        cls.addClassCleanup(sys.modules.pop, spec.name)
        spec.loader.exec_module(cls.module)
        cls.module.app.config["TESTING"] = True

    def setUp(self):
        self.client = self.module.app.test_client()
        catalog = patch.object(self.module, "CATALOG", [])
        catalog.start()
        self.addCleanup(catalog.stop)
        catalog_by_id = patch.object(self.module, "CATALOG_BY_ID", {})
        catalog_by_id.start()
        self.addCleanup(catalog_by_id.stop)
        with self.module.get_db() as db:
            db.execute("DELETE FROM users")
            db.execute("DELETE FROM activity")

    def register(self):
        response = self.client.post("/api/register", json={
            "username": "page_test", "password": "test-password-only",
        })
        self.assertEqual(response.status_code, 200)

    def unlock(self):
        response = self.client.post("/api/unlock", json={"password": "test-unlock"})
        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.json["logged_in"])

    def check_page(self, path, script=None):
        response = self.client.get(path)
        self.assertEqual(response.status_code, 200, path)
        page = PageElements(response.get_data(as_text=True))
        for asset in page.assets:
            with self.client.get(asset) as result:
                self.assertEqual(result.status_code, 200, asset)
        if script:
            text = (self.root / ("static_" + script)).read_text(encoding="utf-8")
            required = set(re.findall(r"getElementById\(['\"]([^'\"]+)['\"]\)", text))
            self.assertFalse(required - page.ids, f"Missing elements: {required - page.ids}")
        return response

    def test_homepage_and_account_render(self):
        response = self.check_page("/")
        self.assertIn(b"Quick calculator", response.data)
        self.assertNotIn(b'id="unlock-form"', response.data)
        self.check_page("/account")
        with self.client.get("/static/style-additions.css") as response:
            self.assertEqual(response.status_code, 200)

    def test_signed_in_homepage_has_all_script_elements(self):
        self.register()
        self.check_page("/", "home.js")
        self.check_page("/", "site.js")

    def test_library_requires_login_and_unlock(self):
        self.assertEqual(self.client.get("/hidden-library").status_code, 302)
        self.assertEqual(self.client.get("/api/catalog").status_code, 401)
        self.register()
        self.assertEqual(self.client.get("/hidden-library").status_code, 404)
        self.assertEqual(self.client.post("/api/unlock", json={"password": "wrong"}).status_code, 401)
        self.assertEqual(self.client.get("/hidden-library").status_code, 404)
        self.unlock()
        self.check_page("/hidden-library", "library.js")
        self.check_page("/hidden-library", "site.js")

    def test_empty_catalog_is_supported(self):
        self.register()
        self.unlock()
        self.assertEqual(self.client.get("/api/catalog").json, [])
        self.assertEqual(self.client.get("/api/favorites").json, [])
        self.assertEqual(self.client.get("/api/progress").json, [])
        self.assertIn(b"No videos are configured yet", self.client.get("/hidden-library").data)

    def test_supplied_catalog_is_available_after_unlock(self):
        catalog = self.module.load_catalog()
        self.module.CATALOG = catalog
        self.module.CATALOG_BY_ID = {item["id"]: item for item in catalog}
        self.register()
        self.unlock()
        response = self.client.get("/api/catalog")
        self.assertEqual(response.status_code, 200)
        source_urls = self.module.ANIME_FILE.read_text(encoding="utf-8").splitlines()
        self.assertEqual([item["url"] for item in response.json], source_urls)
        self.assertEqual(len({item["id"] for item in response.json}), len(source_urls))
        self.assertNotIn(b"No videos are configured yet", self.client.get("/hidden-library").data)
        self.assertEqual(self.client.get("/health").json["catalog_entries"], len(source_urls))

    def test_logout_and_login_clear_unlock(self):
        self.register()
        self.unlock()
        self.assertEqual(self.client.post("/api/logout").status_code, 200)
        self.assertEqual(self.client.get("/hidden-library").status_code, 302)
        response = self.client.post("/api/login", json={
            "username": "page_test", "password": "test-password-only",
        })
        self.assertEqual(response.status_code, 200)
        self.assertEqual(self.client.get("/hidden-library").status_code, 404)
        self.unlock()
        self.check_page("/hidden-library")

    def test_admin_template_and_access_gate(self):
        self.register()
        self.assertEqual(self.client.get("/overlord").status_code, 404)
        with self.module.get_db() as db:
            db.execute("UPDATE users SET role='admin' WHERE username='page_test'")
        self.check_page("/overlord", "admin.js")
        self.assertEqual(self.client.get("/api/admin/overview").status_code, 200)

    def test_health(self):
        response = self.client.get("/health")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json, {"status": "ok", "catalog_entries": 0})

    def test_flat_assets_have_expected_contents_and_types(self):
        for name, filename in self.module.STATIC_FILES.items():
            with self.subTest(asset=name):
                with self.client.get("/static/" + name) as response:
                    self.assertEqual(response.status_code, 200)
                    self.assertEqual(response.data, (self.root / filename).read_bytes())
                    if name.endswith(".css"):
                        self.assertEqual(response.mimetype, "text/css")
                    else:
                        self.assertIn(response.mimetype, ("text/javascript", "application/javascript"))
        css = (self.root / "static_style.css").read_text(encoding="utf-8")
        self.assertIn('@import url("./style-additions.css");', css)

    def test_private_files_are_not_public_assets(self):
        for path in (
            "/static/app.py", "/static/../app.py", "/static/%2e%2e/app.py",
            "/static/.env", "/static/templates_admin.html",
            "/static/data/mathlab.sqlite3", "/static/missing.js",
            "/app.py", "/templates_admin.html", "/static_admin.js",
        ):
            with self.subTest(path=path):
                self.assertEqual(self.client.get(path).status_code, 404)

    def popup_clients(self):
        clients = []
        ids = []
        for username in ("popup_admin", "popup_recipient", "popup_other"):
            client = self.module.app.test_client()
            self.assertEqual(client.post("/api/register", json={
                "username": username, "password": "test-password-only",
            }).status_code, 200)
            with client.session_transaction() as session:
                ids.append(session["uid"])
            clients.append(client)
        with self.module.get_db() as db:
            db.execute("UPDATE users SET role='admin' WHERE id=?", (ids[0],))
        return clients, ids

    def send_popup(self, admin, user_id, mode="once"):
        response = admin.post(f"/api/admin/users/{user_id}/popups", json={
            "title": "A message <script>", "message": "Please respond.\nSecond line.", "pause_mode": mode,
        })
        self.assertEqual(response.status_code, 201, response.json)
        return response.json["id"]

    def test_popup_targeting_reply_and_dismissal_modes(self):
        (admin, recipient, other), (_, user_id, _) = self.popup_clients()
        for mode in ("once", "dismiss", "admin"):
            with self.subTest(mode=mode):
                popup_id = self.send_popup(admin, user_id, mode)
                self.assertIsNone(other.get("/api/screen-popup").json["popup"])
                response = recipient.get("/api/screen-popup")
                self.assertEqual(response.headers["Cache-Control"], "no-store")
                self.assertEqual(response.json["popup"]["pause_mode"], mode)
                self.assertEqual(response.json["popup"]["title"], "A message <script>")
                self.assertEqual(other.post(f"/api/popups/{popup_id}/reply", json={"reply": "spy"}).status_code, 404)
                self.assertEqual(other.post(f"/api/popups/{popup_id}/dismiss", json={}).status_code, 404)
                self.assertEqual(recipient.post(f"/api/popups/{popup_id}/reply", json={"reply": "<b>OK</b>"}).status_code, 200)
                self.assertEqual(recipient.post(f"/api/popups/{popup_id}/reply", json={"reply": "<b>OK</b>"}).status_code, 200)
                self.assertEqual(recipient.post(f"/api/popups/{popup_id}/reply", json={"reply": "overwrite"}).status_code, 409)
                history = admin.get(f"/api/admin/users/{user_id}/popups").json
                self.assertEqual(history[0]["reply"], "<b>OK</b>")
                self.assertIsNotNone(history[0]["received_at"])
                dismissed = recipient.post(f"/api/popups/{popup_id}/dismiss", json={})
                if mode == "admin":
                    self.assertEqual(dismissed.status_code, 409)
                    self.assertEqual(recipient.get("/api/screen-popup").json["popup"]["id"], popup_id)
                    self.assertEqual(admin.post(f"/api/admin/popups/{popup_id}/release", json={}).status_code, 200)
                else:
                    self.assertEqual(dismissed.status_code, 200)
                self.assertIsNone(recipient.get("/api/screen-popup").json["popup"])
        with self.module.get_db() as db:
            actions = [row[0] for row in db.execute(
                "SELECT action FROM activity WHERE action LIKE '%popup%'"
            ).fetchall()]
        self.assertIn("admin_popup_sent", actions)
        self.assertIn("popup_replied", actions)
        self.assertIn("admin_popup_released", actions)

    def test_popup_admin_auth_and_ip_gate(self):
        (admin, recipient, _), (_, user_id, _) = self.popup_clients()
        popup_id = self.send_popup(admin, user_id)
        for client, expected in ((self.client, 401), (recipient, 404)):
            self.assertEqual(client.post(f"/api/admin/users/{user_id}/popups", json={}).status_code, expected)
            self.assertEqual(client.get(f"/api/admin/users/{user_id}/popups").status_code, expected)
            self.assertEqual(client.post(f"/api/admin/popups/{popup_id}/release", json={}).status_code, expected)
        blocked = {"REMOTE_ADDR": "192.0.2.1"}
        self.assertEqual(admin.post(f"/api/admin/users/{user_id}/popups", json={}, environ_overrides=blocked).status_code, 404)
        self.assertEqual(admin.post(f"/api/admin/popups/{popup_id}/release", json={}, environ_overrides=blocked).status_code, 404)
        self.assertEqual(self.client.get("/api/screen-popup").status_code, 401)
        with self.module.get_db() as db:
            db.execute("UPDATE user_sessions SET revoked=1 WHERE user_id=?", (user_id,))
        self.assertEqual(recipient.get("/api/screen-popup").status_code, 401)
        self.assertEqual(recipient.post(f"/api/popups/{popup_id}/reply", json={"reply": "no"}).status_code, 401)

    def test_popup_validation_and_one_active_per_user(self):
        (admin, recipient, _), (admin_id, user_id, _) = self.popup_clients()
        url = f"/api/admin/users/{user_id}/popups"
        valid = {"title": "Hello", "message": "Message", "pause_mode": "once"}
        for bad in ([], None, {}, {**valid, "title": 1}, {**valid, "title": " "},
                    {**valid, "title": "x" * 121}, {**valid, "message": "x" * 2001},
                    {**valid, "message": []}, {**valid, "pause_mode": "invalid"}):
            self.assertEqual(admin.post(url, json=bad).status_code, 400)
        self.assertEqual(admin.post(url, data=valid).status_code, 400)
        self.assertEqual(admin.post(f"/api/admin/users/{admin_id}/popups", json=valid).status_code, 400)
        self.assertEqual(admin.post("/api/admin/users/999999/popups", json=valid).status_code, 404)
        popup_id = self.send_popup(admin, user_id)
        self.assertEqual(admin.post(url, json=valid).status_code, 409)
        for reply in ("", " ", 123, "x" * 2001):
            self.assertEqual(recipient.post(f"/api/popups/{popup_id}/reply", json={"reply": reply}).status_code, 400)
        self.assertEqual(recipient.post(f"/api/popups/{popup_id}/dismiss", data={}).status_code, 400)
        self.assertEqual(admin.post(f"/api/admin/popups/{popup_id}/release", data={}).status_code, 400)
        self.assertEqual(admin.post(f"/api/admin/popups/{popup_id}/release", json={}).status_code, 200)
        self.assertEqual(admin.post(f"/api/admin/popups/{popup_id}/release", json={}).status_code, 200)
        self.assertIsNone(recipient.get("/api/screen-popup").json["popup"])
        # A reply already being typed is still accepted after release.
        self.assertEqual(recipient.post(f"/api/popups/{popup_id}/reply", json={"reply": "Thanks"}).status_code, 200)
        self.send_popup(admin, user_id)

    def test_concurrent_popup_sends_do_not_replace_each_other(self):
        (admin, _, _), (_, user_id, _) = self.popup_clients()
        cookie = admin.get_cookie("session").value

        def send():
            client = self.module.app.test_client()
            client.set_cookie("session", cookie)
            return client.post(f"/api/admin/users/{user_id}/popups", json={
                "title": "Hello", "message": "Message", "pause_mode": "admin",
            }).status_code

        with ThreadPoolExecutor(max_workers=2) as pool:
            results = list(pool.map(lambda _: send(), range(2)))
        self.assertEqual(sorted(results), [201, 409])
        self.assertEqual(len(admin.get(f"/api/admin/users/{user_id}/popups").json), 1)

    def test_popup_persists_between_sessions_and_is_removed_with_user(self):
        (admin, recipient, _), (_, user_id, _) = self.popup_clients()
        recipient.post("/api/logout")
        popup_id = self.send_popup(admin, user_id, "admin")
        self.module.init_db()
        self.assertEqual(recipient.post("/api/login", json={
            "username": "popup_recipient", "password": "test-password-only",
        }).status_code, 200)
        self.assertEqual(recipient.get("/api/screen-popup").json["popup"]["id"], popup_id)
        with self.module.get_db() as db:
            db.execute("DELETE FROM users WHERE id=?", (user_id,))
            self.assertIsNone(db.execute("SELECT id FROM screen_popups WHERE id=?", (popup_id,)).fetchone())

    def test_popup_controls_render_on_signed_in_pages_only(self):
        self.assertNotIn(b'id="screen-popup"', self.client.get("/").data)
        self.register()
        self.check_page("/", "popup.js")
        self.unlock()
        self.check_page("/hidden-library", "popup.js")
        with self.module.get_db() as db:
            db.execute("UPDATE users SET role='admin' WHERE username='page_test'")
        self.check_page("/overlord", "popup.js")


if __name__ == "__main__":
    unittest.main()
