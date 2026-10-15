"""Automated tests. They use a temporary database and upload folder, never your real data."""
import io
import os
import sys
import tempfile
import unittest

TMP = tempfile.mkdtemp()
os.environ["ECOSHIELD_DB"] = os.path.join(TMP, "test.db")
os.environ["ECOSHIELD_UPLOADS"] = os.path.join(TMP, "uploads")
os.environ["FIRMS_MAP_KEY"] = ""  # force demo mode
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from PIL import Image  # noqa: E402

import app as app_module  # noqa: E402
import database as db  # noqa: E402


def make_image(color, box=None, fmt="PNG"):
    img = Image.new("RGB", (200, 150), color)
    if box:
        img.paste((120, 80, 40), box)
    buf = io.BytesIO()
    img.save(buf, fmt)
    buf.seek(0)
    return buf


class EcoShieldTests(unittest.TestCase):
    def setUp(self):
        self.client = app_module.app.test_client()

    def compare(self, before, after, **over):
        data = {"location": "Test forest", "date_before": "2025-01-01", "date_after": "2026-01-01",
                "before_image": (before, "a.png"), "after_image": (after, "b.png")}
        data.update(over)
        return self.client.post("/api/forest/compare", data=data, content_type="multipart/form-data")

    def test_01_init_no_duplicates(self):
        db.init_db(); db.init_db()
        self.assertEqual(db.query_one("SELECT COUNT(*) c FROM forest_analyses")["c"], 4)
        self.assertEqual(db.query_one("SELECT COUNT(*) c FROM claim_analyses")["c"], 3)

    def test_02_dashboard(self):
        d = self.client.get("/api/dashboard").get_json()
        self.assertGreaterEqual(d["stats"]["forest_records"], 4)
        self.assertEqual(d["stats"]["fire_hotspots"], 60)
        self.assertEqual(d["fire_mode"], "demo")
        self.assertEqual(self.client.get("/api/dashboard?days=30").status_code, 200)

    def test_03_identical_and_changed_images(self):
        same = self.compare(make_image((20, 120, 20)), make_image((20, 120, 20)))
        self.assertEqual(same.status_code, 201)
        self.assertEqual(same.get_json()["change_percent"], 0.0)
        diff = self.compare(make_image((20, 120, 20)), make_image((20, 120, 20), (0, 0, 100, 150)))
        body = diff.get_json()
        self.assertGreater(body["change_percent"], 40)
        self.assertTrue(os.path.exists(os.path.join(app_module.UPLOAD_DIR, os.path.basename(body["diff_url"]))))
        self.assertEqual(self.client.get(body["diff_url"]).status_code, 200)

    def test_04_invalid_uploads(self):
        bad_type = self.compare(io.BytesIO(b"not an image"), make_image((1, 2, 3)))
        self.assertEqual(bad_type.status_code, 400)
        wrong_ext = self.compare((io.BytesIO(b"x"), "x.txt"), make_image((1, 2, 3)))
        self.assertEqual(wrong_ext.status_code, 400)
        self.assertEqual(self.client.post("/api/forest/compare", data={"location": "x", "date_before": "2025-01-01",
                         "date_after": "2026-01-01"}).status_code, 400)
        self.assertEqual(self.compare(make_image((1, 1, 1)), make_image((1, 1, 1)), location="").status_code, 400)
        self.assertEqual(self.compare(make_image((1, 1, 1)), make_image((1, 1, 1)), date_before="2027-01-01").status_code, 400)
        self.assertEqual(self.compare(make_image((1, 1, 1)), make_image((1, 1, 1)), date_after="bad").status_code, 400)

    def test_05_list_get_delete_forest(self):
        new = self.compare(make_image((9, 9, 9)), make_image((200, 200, 200))).get_json()
        self.assertEqual(self.client.get("/api/forest/%d" % new["id"]).status_code, 200)
        files = [os.path.basename(new[k]) for k in ("before_url", "after_url", "diff_url")]
        self.assertEqual(self.client.delete("/api/forest/%d" % new["id"]).status_code, 200)
        self.assertEqual(self.client.get("/api/forest/%d" % new["id"]).status_code, 404)
        self.assertEqual(self.client.delete("/api/forest/%d" % new["id"]).status_code, 404)
        for f in files:
            self.assertFalse(os.path.exists(os.path.join(app_module.UPLOAD_DIR, f)))

    def test_06_fire(self):
        d = self.client.get("/api/fire").get_json()
        self.assertEqual(d["total"], 60)
        high = self.client.get("/api/fire?category=High").get_json()
        self.assertTrue(all(r["category"] == "High" for r in high["records"]))
        self.assertEqual(self.client.get("/api/fire?location=zzzz").get_json()["count"], 0)
        self.assertEqual(self.client.get("/api/fire?category=Nope").status_code, 400)
        self.assertEqual(self.client.get("/api/fire/S-001").status_code, 200)
        self.assertEqual(self.client.get("/api/fire/NOPE").status_code, 404)

    def check(self, text, cat="climate_change"):
        return self.client.post("/api/claims/check", json={"claim": text, "category": cat})

    def test_07_claims(self):
        r = self.check("Planting trees alone can fully solve climate change").get_json()
        self.assertEqual(r["assessment"], "contradicted")
        self.assertTrue(r["source_url"].startswith("https://"))
        r = self.check("Forests absorb carbon dioxide from the atmosphere", "deforestation").get_json()
        self.assertEqual(r["assessment"], "supported")
        r = self.check("Volcano ice cream tastes better on Tuesdays").get_json()
        self.assertEqual(r["assessment"], "unverified")
        self.assertIn("Unverified", r["assessment_label"])
        # same words with opposite meaning must not be marked supported
        r = self.check("Forests do not absorb carbon dioxide from the atmosphere", "deforestation").get_json()
        self.assertEqual(r["assessment"], "unverified")
        self.assertEqual(self.check("short").status_code, 400)
        self.assertEqual(self.check("A long enough claim here", "bogus").status_code, 400)
        self.assertEqual(self.client.post("/api/claims/check", data="nonsense").status_code, 400)

    def test_08_claim_history_delete(self):
        new = self.check("Global deforestation has completely stopped", "deforestation").get_json()
        ids = [c["id"] for c in self.client.get("/api/claims").get_json()]
        self.assertIn(new["id"], ids)
        self.assertEqual(self.client.delete("/api/claims/%d" % new["id"]).status_code, 200)
        self.assertEqual(self.client.delete("/api/claims/%d" % new["id"]).status_code, 404)

    def test_09_reports_and_exports(self):
        rep = self.client.get("/api/reports").get_json()
        self.assertGreater(len(rep["claims"]), 0)
        self.assertEqual(self.client.get("/api/reports?date_from=bad").status_code, 400)
        self.assertEqual(len(self.client.get("/api/reports?fire_level=High").get_json()["claims"]), len(rep["claims"]))
        for kind in ("forest", "fire", "claims"):
            res = self.client.get("/api/reports/export/csv?type=" + kind)
            self.assertEqual(res.status_code, 200)
            self.assertIn("text/csv", res.mimetype)
            self.assertGreater(len(res.data.decode("utf-8-sig").splitlines()), 1)
        self.assertEqual(self.client.get("/api/reports/export/csv?type=x").status_code, 400)
        pdf = self.client.get("/api/reports/export/pdf")
        self.assertEqual(pdf.status_code, 200)
        self.assertTrue(pdf.data.startswith(b"%PDF"))

    def test_10_pages_and_errors(self):
        page = self.client.get("/")
        self.assertEqual(page.status_code, 200)
        self.assertIn(b"EcoShield AI", page.data)
        self.assertEqual(self.client.get("/static/js/script.js").status_code, 200)
        self.assertEqual(self.client.get("/api/nothing").status_code, 404)
        self.assertEqual(self.client.get("/uploads/forest/../../app.py").status_code, 404)


if __name__ == "__main__":
    unittest.main()
