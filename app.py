"""EcoShield AI - Flask backend.  Start with:  python app.py"""
import csv
import io
import os
import sqlite3
import uuid
from datetime import datetime, timedelta
from xml.sax.saxutils import escape

import cv2
import numpy as np
from dotenv import load_dotenv
from flask import Flask, Response, jsonify, render_template, request, send_file, send_from_directory
from PIL import Image

load_dotenv()  # reads optional settings (e.g. NASA FIRMS key) from the .env file

import claim_checker
import database as db
import fire_service

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
UPLOAD_DIR = os.getenv("ECOSHIELD_UPLOADS", os.path.join(BASE_DIR, "uploads")) + os.sep + "forest"
ALLOWED_EXTENSIONS = {"png", "jpg", "jpeg", "webp"}
COMPARE_SIZE = (640, 480)      # both images are resized to this before comparing
PIXEL_THRESHOLD = 30           # a pixel counts as "changed" if it differs by more than this (0-255)
CLAIM_CATEGORIES = ["deforestation", "climate_change", "biodiversity", "forest_fires"]

app = Flask(__name__)
app.config["MAX_CONTENT_LENGTH"] = 16 * 1024 * 1024  # 16 MB per request
os.makedirs(UPLOAD_DIR, exist_ok=True)
db.init_db()


def error(message, status=400):
    return jsonify({"error": message}), status


# ----------------------------------------------------------------- error handlers
@app.errorhandler(sqlite3.Error)
def handle_db_error(exc):
    app.logger.error("Database error: %s", exc)
    return error("A database error occurred. Please try again.", 500)


@app.errorhandler(413)
def handle_too_large(exc):
    return error("Upload too large. The maximum total size is 16 MB.", 413)


@app.errorhandler(404)
def handle_404(exc):
    if request.path.startswith("/api/"):
        return error("Endpoint not found.", 404)
    return "Page not found", 404


@app.errorhandler(405)
def handle_405(exc):
    return error("Method not allowed.", 405)


@app.errorhandler(500)
def handle_500(exc):
    return error("Unexpected server error.", 500)


# ----------------------------------------------------------------- pages
@app.route("/")
def index():
    return render_template("index.html")


@app.route("/uploads/forest/<path:filename>")
def uploaded_file(filename):
    return send_from_directory(UPLOAD_DIR, filename)  # safe: cannot leave UPLOAD_DIR


# ----------------------------------------------------------------- forest helpers
def change_level(pct):
    return "Low" if pct < 5 else "Moderate" if pct < 20 else "High"


def interpretation(pct):
    level = change_level(pct).lower()
    return ("About %.1f%% of pixels differ between the two images (%s visual difference). This is a basic pixel "
            "comparison only. Lighting, clouds, shadows, seasons, camera angle and image alignment can all create "
            "differences, so this result does NOT confirm deforestation or illegal logging." % (pct, level))


def forest_to_dict(row):
    d = dict(row)
    for key in ("before_image", "after_image", "diff_image"):
        name = d.pop(key)
        d[key.replace("_image", "_url")] = "/uploads/forest/" + name if name else None
    d["level"] = change_level(d["change_percent"])
    d["interpretation"] = interpretation(d["change_percent"])
    d["is_sample"] = bool(d["is_sample"])
    d["source"] = "Sample demonstration data" if d["is_sample"] else "User-uploaded images (basic pixel comparison)"
    return d


def load_image(file_storage, label):
    """Validate an uploaded file on the server and return it as an OpenCV (BGR) image."""
    if file_storage is None or not file_storage.filename:
        raise ValueError("The %s image is missing." % label)
    ext = file_storage.filename.rsplit(".", 1)[-1].lower() if "." in file_storage.filename else ""
    if ext not in ALLOWED_EXTENSIONS:
        raise ValueError("The %s image must be a PNG, JPG or WEBP file." % label)
    data = file_storage.read()
    if not data:
        raise ValueError("The %s image file is empty." % label)
    try:
        img = Image.open(io.BytesIO(data))
        if img.format not in ("PNG", "JPEG", "WEBP"):
            raise ValueError("format")
        img = img.convert("RGB")
    except Exception:
        raise ValueError("The %s file is not a valid image." % label)
    return cv2.cvtColor(np.array(img), cv2.COLOR_RGB2BGR)


def compare_images(before, after):
    """Return (percent_changed, difference_image). Simple absolute pixel difference."""
    b = cv2.resize(before, COMPARE_SIZE)
    a = cv2.resize(after, COMPARE_SIZE)
    gray = cv2.cvtColor(cv2.absdiff(b, a), cv2.COLOR_BGR2GRAY)
    gray = cv2.GaussianBlur(gray, (5, 5), 0)           # blur to ignore tiny noise
    _, mask = cv2.threshold(gray, PIXEL_THRESHOLD, 255, cv2.THRESH_BINARY)
    percent = float(np.count_nonzero(mask)) * 100.0 / mask.size
    diff_img = (a * 0.4).astype(np.uint8)              # dimmed "after" image ...
    diff_img[mask > 0] = (60, 60, 255)                 # ... with changed pixels in red
    return round(percent, 2), b, a, diff_img


def remove_files(*names):
    for name in names:
        if name:
            try:
                os.remove(os.path.join(UPLOAD_DIR, os.path.basename(name)))
            except OSError:
                pass


# ----------------------------------------------------------------- forest API
@app.route("/api/forest/compare", methods=["POST"])
def forest_compare():
    location = request.form.get("location", "").strip()
    date_before = request.form.get("date_before", "").strip()
    date_after = request.form.get("date_after", "").strip()
    if not location or len(location) > 100:
        return error("Please enter a location name (up to 100 characters).")
    try:
        d1 = datetime.strptime(date_before, "%Y-%m-%d")
        d2 = datetime.strptime(date_after, "%Y-%m-%d")
    except ValueError:
        return error("Both dates are required and must be valid (YYYY-MM-DD).")
    if d1 > d2:
        return error("The 'before' date cannot be later than the 'after' date.")
    try:
        before = load_image(request.files.get("before_image"), "before")
        after = load_image(request.files.get("after_image"), "after")
    except ValueError as exc:
        return error(str(exc))

    percent, b, a, diff = compare_images(before, after)
    base = uuid.uuid4().hex  # unique names: we never trust uploaded file names
    names = {"before": base + "_before.jpg", "after": base + "_after.jpg", "diff": base + "_diff.jpg"}
    try:
        for key, img in (("before", b), ("after", a), ("diff", diff)):
            if not cv2.imwrite(os.path.join(UPLOAD_DIR, names[key]), img):
                raise OSError("could not write image")
        new_id, _ = db.execute(
            "INSERT INTO forest_analyses (location, date_before, date_after, before_image, after_image, "
            "diff_image, change_percent, is_sample, created_at) VALUES (?, ?, ?, ?, ?, ?, ?, 0, ?)",
            (location, date_before, date_after, names["before"], names["after"], names["diff"], percent,
             datetime.now().strftime("%Y-%m-%d %H:%M:%S")))
    except (OSError, sqlite3.Error) as exc:
        remove_files(*names.values())
        app.logger.error("Saving analysis failed: %s", exc)
        return error("Could not save the analysis. Please try again.", 500)
    row = db.query_one("SELECT * FROM forest_analyses WHERE id = ?", (new_id,))
    return jsonify(forest_to_dict(row)), 201


@app.route("/api/forest")
def forest_list():
    rows = db.query_all("SELECT * FROM forest_analyses ORDER BY id DESC")
    return jsonify([forest_to_dict(r) for r in rows])


@app.route("/api/forest/<int:record_id>")
def forest_get(record_id):
    row = db.query_one("SELECT * FROM forest_analyses WHERE id = ?", (record_id,))
    return jsonify(forest_to_dict(row)) if row else error("Record not found.", 404)


@app.route("/api/forest/<int:record_id>", methods=["DELETE"])
def forest_delete(record_id):
    row = db.query_one("SELECT * FROM forest_analyses WHERE id = ?", (record_id,))
    if not row:
        return error("Record not found.", 404)
    db.execute("DELETE FROM forest_analyses WHERE id = ?", (record_id,))
    remove_files(row["before_image"], row["after_image"], row["diff_image"])
    return jsonify({"deleted": record_id})


# ----------------------------------------------------------------- fire API
@app.route("/api/fire")
def fire_list():
    data = fire_service.get_fire_data()
    loc = request.args.get("location", "").strip().lower()
    date_from = request.args.get("date_from", "").strip()
    date_to = request.args.get("date_to", "").strip()
    category = request.args.get("category", "").strip()
    if category and category not in ("High", "Medium", "Low"):
        return error("Category must be High, Medium or Low.")
    records = [r for r in data["records"]
               if (not loc or loc in r["location"].lower())
               and (not date_from or r["date"] >= date_from)
               and (not date_to or r["date"] <= date_to)
               and (not category or r["category"] == category)]
    records.sort(key=lambda r: r["date"], reverse=True)
    return jsonify({"mode": data["mode"], "note": data["note"], "total": len(data["records"]),
                    "count": len(records), "records": records})


@app.route("/api/fire/<record_id>")
def fire_get(record_id):
    data = fire_service.get_fire_data()
    for r in data["records"]:
        if r["id"] == record_id:
            return jsonify({"mode": data["mode"], "note": data["note"], "record": r})
    return error("Hotspot record not found.", 404)


# ----------------------------------------------------------------- claims API
@app.route("/api/claims/check", methods=["POST"])
def claims_check():
    body = request.get_json(silent=True) or {}
    text = str(body.get("claim", "")).strip()
    category = str(body.get("category", "")).strip()
    if len(text) < 10 or len(text) > 500:
        return error("Please enter a claim between 10 and 500 characters.")
    if category not in CLAIM_CATEGORIES:
        return error("Please choose a valid category.")
    result = claim_checker.check_claim(text, category)
    new_id, _ = db.execute(
        "INSERT INTO claim_analyses (claim_text, category, assessment, explanation, matched_claim, source_title, "
        "source_url, match_score, is_sample, created_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, 0, ?)",
        (text, category, result["assessment"], result["explanation"], result["matched_claim"],
         result["source_title"], result["source_url"], result["match_score"],
         datetime.now().strftime("%Y-%m-%d %H:%M:%S")))
    return jsonify(claim_to_dict(db.query_one("SELECT * FROM claim_analyses WHERE id = ?", (new_id,)))), 201


def claim_to_dict(row):
    d = dict(row)
    d["assessment_label"] = claim_checker.LABELS[d["assessment"]]
    d["is_sample"] = bool(d["is_sample"])
    return d


@app.route("/api/claims")
def claims_list():
    rows = db.query_all("SELECT * FROM claim_analyses ORDER BY id DESC")
    return jsonify([claim_to_dict(r) for r in rows])


@app.route("/api/claims/<int:record_id>", methods=["DELETE"])
def claims_delete(record_id):
    _, changed = db.execute("DELETE FROM claim_analyses WHERE id = ?", (record_id,))
    return jsonify({"deleted": record_id}) if changed else error("Record not found.", 404)


# ----------------------------------------------------------------- dashboard
def month_counts(dates):
    counts = {}
    for d in dates:
        counts[d[:7]] = counts.get(d[:7], 0) + 1
    return [{"month": m, "count": counts[m]} for m in sorted(counts)]


@app.route("/api/dashboard")
def dashboard():
    days = request.args.get("days", "0")
    days = int(days) if days.isdigit() and int(days) in (30, 90, 180, 365) else 0
    cutoff = (datetime.now() - timedelta(days=days)) if days else None
    cut_ts = cutoff.strftime("%Y-%m-%d %H:%M:%S") if cutoff else "0000-00-00"
    cut_date = cutoff.strftime("%Y-%m-%d") if cutoff else "0000-00-00"

    forest = [forest_to_dict(r) for r in db.query_all(
        "SELECT * FROM forest_analyses WHERE created_at >= ? ORDER BY created_at", (cut_ts,))]
    claims = [claim_to_dict(r) for r in db.query_all(
        "SELECT * FROM claim_analyses WHERE created_at >= ? ORDER BY created_at", (cut_ts,))]
    fire_data = fire_service.get_fire_data()
    fires = [r for r in fire_data["records"] if r["date"] >= cut_date]

    fire_by_cat = {"High": 0, "Medium": 0, "Low": 0}
    for r in fires:
        fire_by_cat[r["category"]] = fire_by_cat.get(r["category"], 0) + 1
    claim_by_result = {"supported": 0, "contradicted": 0, "unverified": 0}
    for c in claims:
        claim_by_result[c["assessment"]] += 1

    # Alerts: observed high-confidence hotspots and large visual differences (not proof of anything).
    alerts = []
    for r in sorted(fires, key=lambda r: r["date"], reverse=True):
        if r["category"] == "High" and len(alerts) < 3:
            alerts.append({"type": "fire", "date": r["date"], "sample": not fire_data["mode"] == "live",
                           "text": "High-confidence observed fire hotspot near %s" % r["location"]})
    for f in reversed(forest):
        if f["level"] == "High" and len(alerts) < 6:
            alerts.append({"type": "forest", "date": f["created_at"][:10], "sample": f["is_sample"],
                           "text": "Large visual difference (%.1f%%) in images of %s - needs human review"
                                   % (f["change_percent"], f["location"])})

    activity = [{"time": f["created_at"], "kind": "Forest comparison", "text": "%s: %.1f%% pixel difference"
                 % (f["location"], f["change_percent"]), "sample": f["is_sample"]} for f in forest]
    activity += [{"time": c["created_at"], "kind": "Claim check", "text": "%s: %s" % (
        c["claim_text"][:70], c["assessment_label"]), "sample": c["is_sample"]} for c in claims]
    activity.sort(key=lambda a: a["time"], reverse=True)

    return jsonify({
        "days": days, "fire_mode": fire_data["mode"], "fire_note": fire_data["note"],
        "stats": {"forest_records": len(forest),
                  "potential_changes": sum(1 for f in forest if f["change_percent"] >= 5),
                  "fire_hotspots": len(fires), "claims_analyzed": len(claims)},
        "alerts": alerts, "activity": activity[:8],
        "charts": {"forest_by_month": month_counts([f["created_at"] for f in forest]),
                   "fire_by_category": fire_by_cat, "claims_by_result": claim_by_result,
                   "fire_by_month": month_counts([r["date"] for r in fires])},
    })


# ----------------------------------------------------------------- reports
def parse_date(value):
    value = (value or "").strip()
    if not value:
        return ""
    try:
        datetime.strptime(value, "%Y-%m-%d")
    except ValueError:
        raise ValueError("Dates must use the format YYYY-MM-DD.")
    return value


def collect_report(args):
    date_from, date_to = parse_date(args.get("date_from")), parse_date(args.get("date_to"))
    claim_cat = args.get("claim_category", "").strip()
    fire_level = args.get("fire_level", "").strip()
    if claim_cat and claim_cat not in CLAIM_CATEGORIES:
        raise ValueError("Invalid claim category.")
    if fire_level and fire_level not in ("High", "Medium", "Low"):
        raise ValueError("Invalid fire alert level.")

    def in_range(day):
        return (not date_from or day >= date_from) and (not date_to or day <= date_to)

    forest = [forest_to_dict(r) for r in db.query_all("SELECT * FROM forest_analyses ORDER BY id DESC")]
    claims = [claim_to_dict(r) for r in db.query_all("SELECT * FROM claim_analyses ORDER BY id DESC")]
    fire_data = fire_service.get_fire_data()
    fire = sorted(fire_data["records"], key=lambda r: r["date"], reverse=True)
    return {
        "generated_at": datetime.now().strftime("%Y-%m-%d %H:%M"),
        "fire_mode": fire_data["mode"], "fire_note": fire_data["note"],
        "forest": [f for f in forest if in_range(f["created_at"][:10])],
        "fire": [r for r in fire if in_range(r["date"]) and (not fire_level or r["category"] == fire_level)],
        "claims": [c for c in claims if in_range(c["created_at"][:10]) and (not claim_cat or c["category"] == claim_cat)],
    }


@app.route("/api/reports")
def reports():
    try:
        return jsonify(collect_report(request.args))
    except ValueError as exc:
        return error(str(exc))


def safe_cell(value):
    """Stop spreadsheet formula injection: cells starting with = + - @ get a leading quote."""
    text = "" if value is None else str(value)
    return "'" + text if text[:1] in ("=", "+", "-", "@") else text


@app.route("/api/reports/export/csv")
def export_csv():
    kind = request.args.get("type", "")
    if kind not in ("forest", "fire", "claims"):
        return error("type must be forest, fire or claims.")
    try:
        report = collect_report(request.args)
    except ValueError as exc:
        return error(str(exc))
    gen = report["generated_at"]
    if kind == "forest":
        header = ["id", "location", "date_before", "date_after", "change_percent", "visual_change_level",
                  "analysed_at", "source", "generated_at"]
        rows = [[r["id"], r["location"], r["date_before"], r["date_after"], r["change_percent"], r["level"],
                 r["created_at"], r["source"], gen] for r in report["forest"]]
    elif kind == "fire":
        header = ["id", "location", "latitude", "longitude", "date", "confidence", "alert_category", "source",
                  "generated_at"]
        rows = [[r["id"], r["location"], r["latitude"], r["longitude"], r["date"], r["confidence"], r["category"],
                 r["source"], gen] for r in report["fire"]]
    else:
        header = ["id", "claim", "category", "assessment", "explanation", "matched_evidence", "source_title",
                  "source_url", "analysed_at", "generated_at"]
        rows = [[r["id"], r["claim_text"], r["category"], r["assessment_label"], r["explanation"],
                 r["matched_claim"], r["source_title"], r["source_url"], r["created_at"], gen]
                for r in report["claims"]]
    out = io.StringIO()
    writer = csv.writer(out)
    writer.writerow(header)
    for row in rows:
        writer.writerow([safe_cell(c) for c in row])
    filename = "ecoshield_%s_%s.csv" % (kind, datetime.now().strftime("%Y%m%d"))
    return Response("\ufeff" + out.getvalue(), mimetype="text/csv",
                    headers={"Content-Disposition": "attachment; filename=" + filename})


@app.route("/api/reports/export/pdf")
def export_pdf():
    try:
        from reportlab.lib import colors
        from reportlab.lib.pagesizes import A4
        from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
        from reportlab.lib.units import cm
        from reportlab.platypus import Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle
    except ImportError:
        return error("PDF export needs the 'reportlab' package. Run: pip install reportlab", 503)
    try:
        report = collect_report(request.args)
    except ValueError as exc:
        return error(str(exc))

    styles = getSampleStyleSheet()
    small = ParagraphStyle("small", parent=styles["BodyText"], fontSize=8, leading=10)
    p = lambda text: Paragraph(escape(str(text if text is not None else "")), small)

    def table(header, rows, widths):
        data = [[p(h) for h in header]] + [[p(c) for c in row] for row in rows]
        t = Table(data, colWidths=[w * cm for w in widths], repeatRows=1)
        t.setStyle(TableStyle([("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#d1fae5")),
                               ("GRID", (0, 0), (-1, -1), 0.4, colors.grey),
                               ("VALIGN", (0, 0), (-1, -1), "TOP")]))
        return t

    story = [Paragraph("EcoShield AI - Environmental Summary Report", styles["Title"]),
             Paragraph("Generated: %s" % report["generated_at"], styles["BodyText"]),
             Paragraph(escape("Fire data source: " + report["fire_note"]), styles["BodyText"]),
             Paragraph("Forest comparisons use basic pixel differences and are not validated deforestation "
                       "measurements. Claim checks use basic evidence matching, not a trained AI model.",
                       styles["BodyText"]), Spacer(1, 10),
             Paragraph("Summary: %d forest comparisons, %d fire hotspot records, %d claim analyses."
                       % (len(report["forest"]), len(report["fire"]), len(report["claims"])), styles["BodyText"]),
             Spacer(1, 10), Paragraph("Forest image comparisons", styles["Heading2"])]
    if report["forest"]:
        story.append(table(["ID", "Location", "Image dates", "Changed pixels", "Level", "Source"],
                           [[r["id"], r["location"], "%s to %s" % (r["date_before"], r["date_after"]),
                             "%.1f%%" % r["change_percent"], r["level"], r["source"]] for r in report["forest"]],
                           [1, 4, 3.8, 2.2, 1.8, 4.2]))
    else:
        story.append(Paragraph("No records.", styles["BodyText"]))
    story += [Spacer(1, 10), Paragraph("Observed fire hotspots (latest 40 of %d)" % len(report["fire"]),
                                       styles["Heading2"])]
    if report["fire"]:
        story.append(table(["ID", "Date", "Location", "Lat, Lon", "Confidence", "Alert", "Source"],
                           [[r["id"], r["date"], r["location"], "%s, %s" % (r["latitude"], r["longitude"]),
                             r["confidence"], r["category"], r["source"]] for r in report["fire"][:40]],
                           [1.3, 2, 4, 3, 1.8, 1.5, 3.4]))
    else:
        story.append(Paragraph("No records.", styles["BodyText"]))
    story += [Spacer(1, 10), Paragraph("Climate claim analyses", styles["Heading2"])]
    if report["claims"]:
        story.append(table(["Claim", "Result", "Explanation", "Source"],
                           [[r["claim_text"], r["assessment_label"], r["explanation"],
                             "%s %s" % (r["source_title"] or "-", r["source_url"] or "")] for r in report["claims"]],
                           [4.5, 3, 5.5, 4]))
    else:
        story.append(Paragraph("No records.", styles["BodyText"]))

    buffer = io.BytesIO()
    SimpleDocTemplate(buffer, pagesize=A4, leftMargin=1.5 * cm, rightMargin=1.5 * cm,
                      topMargin=1.5 * cm, bottomMargin=1.5 * cm, title="EcoShield AI Report").build(story)
    buffer.seek(0)
    return send_file(buffer, mimetype="application/pdf", as_attachment=True,
                     download_name="ecoshield_report_%s.pdf" % datetime.now().strftime("%Y%m%d"))


if __name__ == "__main__":
    app.run(host="127.0.0.1", port=5000, debug=False)
