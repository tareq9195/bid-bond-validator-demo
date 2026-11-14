"""Bid Bond Validator: an AI-assisted checker for bid bond (tender guarantee) PDFs.

The app renders the first pages of an uploaded PDF as images, sends them to a
vision-language model together with the tender requirements, and shows the
model's checklist as a table. It is an assistive tool: a human must always
confirm the result before accepting or rejecting a bond.

All company-specific policy lives in rules.json, not in the code.
"""
import base64
import json
import os
import re
import secrets
from datetime import datetime, timedelta
from html import escape
from pathlib import Path

import fitz  # PyMuPDF
import requests
from flask import Flask, Response, jsonify, render_template_string, request

BASE_DIR = Path(__file__).parent
RULES_FILE = os.environ.get("RULES_FILE", "rules.json")
RULES = json.loads((BASE_DIR / RULES_FILE).read_text(encoding="utf-8"))

NVIDIA_API_KEY = os.environ.get("NVIDIA_API_KEY")
MODEL = os.environ.get("MODEL", "meta/llama-3.2-11b-vision-instruct")
API_URL = os.environ.get(
    "API_URL", "https://integrate.api.nvidia.com/v1/chat/completions"
)

APP_USER = os.environ.get("APP_USER", "admin")
APP_PASSWORD = os.environ.get("APP_PASSWORD")
ALLOW_NO_AUTH = os.environ.get("ALLOW_NO_AUTH") == "1"  # local development only

MAX_UPLOAD_MB = 10
MAX_PAGES = 6

app = Flask(__name__)
app.config["MAX_CONTENT_LENGTH"] = MAX_UPLOAD_MB * 1024 * 1024


# --------------------------------------------------------------------------
# Access control (HTTP Basic). Secure by default: refuses to serve without it.
# --------------------------------------------------------------------------
@app.before_request
def require_auth():
    if ALLOW_NO_AUTH:
        return None
    if not APP_PASSWORD:
        return Response(
            "Server is not configured: set APP_PASSWORD (or ALLOW_NO_AUTH=1 locally).",
            503,
        )
    auth = request.authorization
    if (
        auth
        and secrets.compare_digest(auth.username or "", APP_USER)
        and secrets.compare_digest(auth.password or "", APP_PASSWORD)
    ):
        return None
    return Response(
        "Authentication required",
        401,
        {"WWW-Authenticate": 'Basic realm="Bid Bond Validator"'},
    )


# --------------------------------------------------------------------------
# UI
# --------------------------------------------------------------------------
HTML_TEMPLATE = """
<!DOCTYPE html>
<html lang="ar" dir="rtl">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>منصة تدقيق خطابات الضمان</title>
<style>
body { font-family: sans-serif; background: #f4f7f9; display: flex; justify-content: center; padding: 20px; text-align: right; }
.card { background: white; padding: 30px; border-radius: 12px; box-shadow: 0 4px 20px rgba(0,0,0,0.1); width: 100%; max-width: 650px; }
h2 { color: #d93025; text-align: center; border-bottom: 2px solid #eee; padding-bottom: 15px; }
.grid { display: grid; grid-template-columns: 1fr 1fr; gap: 15px; margin-bottom: 15px; }
label { display: block; margin-bottom: 5px; font-weight: bold; font-size: 14px; }
input, select { width: 100%; padding: 10px; border: 1px solid #ccc; border-radius: 8px; box-sizing: border-box; font-size: 14px; }
button { width: 100%; background: #1a73e8; color: white; padding: 14px; border: none; border-radius: 8px; margin-top: 20px; cursor: pointer; font-weight: bold; font-size: 16px; }
button:disabled { background: #aaa; cursor: not-allowed; }
#status { display: none; text-align: center; margin: 15px 0; color: #1a73e8; font-weight: bold; }
#report { margin-top: 20px; padding: 15px; background: #fafafa; border: 1px solid #ddd; border-radius: 8px; display: none; }
#report table { width: 100%; border-collapse: collapse; font-size: 13px; }
#report th { background: #1a73e8; color: white; padding: 8px; text-align: right; }
#report td { padding: 8px; border: 1px solid #ddd; text-align: right; }
#report tr:nth-child(even) { background: #f5f5f5; }
#report td.st-GREEN { background: #d7f2dd; font-weight: bold; }
#report td.st-YELLOW { background: #fff3c4; font-weight: bold; }
#report td.st-RED { background: #fbd5d1; font-weight: bold; }
.error { color: #d93025; }
</style>
</head>
<body>
<div class="card">
<h2>منصة تدقيق خطابات الضمان</h2>
<div class="grid">
<div><label>Tender No:</label><input type="text" id="tNum" placeholder="{{ tender_example }}"></div>
<div><label>Tender Name:</label><input type="text" id="tName" placeholder="اسم المناقصة"></div>
</div>
<div class="grid">
<div>
<label>Bid Bond Amount ({{ currency }}):</label>
<select id="bAmount">
{% for tier in tiers %}<option value="{{ tier }}" {% if tier == default_tier %}selected{% endif %}>{{ "{:,}".format(tier) }}</option>
{% endfor %}</select>
</div>
<div><label>Bid Closing Date:</label><input type="date" id="cDate"></div>
</div>
<label>رفع ملف PDF (حد أقصى {{ max_mb }} MB):</label>
<input type="file" id="fileInput" accept=".pdf">
<button id="runBtn" onclick="runCheck()">تحليل المستند</button>
<div id="status">⏳ جاري التحليل...</div>
<div id="report"></div>
</div>
<script>
async function runCheck() {
  const file = document.getElementById('fileInput').files[0];
  const tNum = document.getElementById('tNum').value.trim();
  const tName = document.getElementById('tName').value.trim();
  const cDate = document.getElementById('cDate').value;
  if (!file) return alert("يرجى اختيار ملف PDF");
  if (!tNum || !tName || !cDate) return alert("يرجى تعبئة جميع الحقول");

  const fd = new FormData();
  fd.append('file', file);
  fd.append('tNum', tNum);
  fd.append('tName', tName);
  fd.append('bAmount', document.getElementById('bAmount').value);
  fd.append('cDate', cDate);

  const report = document.getElementById('report');
  document.getElementById('runBtn').disabled = true;
  document.getElementById('status').style.display = 'block';
  report.style.display = 'none';

  try {
    const r = await fetch('/', { method: 'POST', body: fd });
    const d = await r.json();
    if (d.report) {
      report.innerHTML = d.report;  // sanitized on the server
    } else {
      report.textContent = 'خطأ: ' + (d.error || 'unknown');
      report.className = 'error';
    }
    report.style.display = 'block';
  } catch (e) {
    alert("خطأ: " + e.message);
  }
  document.getElementById('status').style.display = 'none';
  document.getElementById('runBtn').disabled = false;
}
</script>
</body>
</html>
"""


# --------------------------------------------------------------------------
# Prompt
# --------------------------------------------------------------------------
def build_system_prompt() -> str:
    r = RULES
    blacklist = ", ".join(r.get("blacklisted_banks", [])) or "none"
    return f"""You are a compliance assistant for {r['company_name']}.
Analyze the Bid Bond images and verify them against the tender requirements.

Output ONLY an HTML table with columns: Criterion | Status | Finding.
Status must be exactly one of: GREEN, YELLOW, RED.

Check these 7 criteria:
1. Tender Name and Number - must match exactly (GREEN or RED only)
2. Beneficiary Name - must be exactly "{r['beneficiary_name']}" (GREEN or RED only)
3. Currency - must be {r['currency']} only (GREEN or RED only)
4. Bid Bond Amount - must be equal to or greater than the required amount (GREEN or RED only)
5. Bank Status - GREEN if issued by an approved local bank, YELLOW if issued by an international bank, RED if the bank is blacklisted.
   Blacklisted banks: {blacklist}
6. Validity - GREEN if the bond is valid until the required minimum expiry date or later; YELLOW if short by {r['validity_tolerance_days']} days or less; RED if short by more than {r['validity_tolerance_days']} days
7. Form Compliance - GREEN if it matches the standard template, YELLOW for minor deviations, RED if conditional language blocks payment On First Demand

After the table add: <b>Overall Result: PASS / CONDITIONAL / FAIL</b>
Then: <b>Legal Risk Note:</b> one sentence.
"""


# --------------------------------------------------------------------------
# Helpers
# --------------------------------------------------------------------------
ALLOWED_TAGS = ("table", "thead", "tbody", "tr", "th", "td", "b", "br")


def sanitize_report(text: str) -> str:
    """Escape everything, then re-enable a small whitelist of attribute-free tags."""
    text = re.sub(r"```(?:html)?", "", text)
    safe = escape(text)
    pattern = r"&lt;(/?)(" + "|".join(ALLOWED_TAGS) + r")\s*/?&gt;"
    safe = re.sub(pattern, r"<\1\2>", safe, flags=re.IGNORECASE)
    safe = re.sub(
        r"<td>\s*(GREEN|YELLOW|RED)\s*</td>",
        lambda m: f'<td class="st-{m.group(1).upper()}">{m.group(1).upper()}</td>',
        safe,
        flags=re.IGNORECASE,
    )
    return safe


def pdf_to_base64_images(pdf_bytes: bytes) -> list[str]:
    doc = fitz.open(stream=pdf_bytes, filetype="pdf")
    images = []
    try:
        for index, page in enumerate(doc):
            if index >= MAX_PAGES:
                break
            pix = page.get_pixmap(matrix=fitz.Matrix(2, 2))
            images.append(base64.b64encode(pix.tobytes("jpeg")).decode("utf-8"))
    finally:
        doc.close()
    return images


DISCLAIMER = (
    "<p><small>AI-assisted result. A human reviewer must verify the original "
    "document before any decision.</small></p>"
)


# --------------------------------------------------------------------------
# Routes
# --------------------------------------------------------------------------
@app.route("/", methods=["GET", "POST"])
def handle():
    if request.method == "GET":
        return render_template_string(
            HTML_TEMPLATE,
            tiers=RULES["amount_tiers_usd"],
            default_tier=RULES.get("default_amount_usd"),
            currency=RULES["currency"],
            tender_example=RULES.get("tender_number_example", ""),
            max_mb=MAX_UPLOAD_MB,
        )

    if not NVIDIA_API_KEY:
        return jsonify({"error": "Server is missing NVIDIA_API_KEY"}), 500

    f = request.files.get("file")
    if not f:
        return jsonify({"error": "لم يتم رفع أي ملف"}), 400

    try:
        closing = datetime.strptime(request.form.get("cDate", ""), "%Y-%m-%d").date()
        amount = int(request.form.get("bAmount", ""))
    except ValueError:
        return jsonify({"error": "Invalid date or amount"}), 400
    if amount not in RULES["amount_tiers_usd"]:
        return jsonify({"error": "Amount is not one of the allowed tiers"}), 400

    required_expiry = closing + timedelta(days=RULES["min_validity_days"])

    try:
        images = pdf_to_base64_images(f.read())
    except Exception:
        app.logger.exception("PDF processing failed")
        return jsonify({"error": "تعذر قراءة ملف PDF"}), 400
    if not images:
        return jsonify({"error": "الملف فارغ"}), 400

    user_prompt = (
        f"Tender No: {request.form.get('tNum', '').strip()}\n"
        f"Tender Name: {request.form.get('tName', '').strip()}\n"
        f"Required Bid Bond Amount: {amount} {RULES['currency']}\n"
        f"Bid Closing Date: {closing.isoformat()}\n"
        f"Required minimum expiry date of the bond: {required_expiry.isoformat()}\n\n"
        "Analyze the attached Bid Bond images."
    )

    content = [{"type": "text", "text": build_system_prompt() + "\n\n" + user_prompt}]
    for img_b64 in images:
        content.append(
            {
                "type": "image_url",
                "image_url": {"url": f"data:image/jpeg;base64,{img_b64}"},
            }
        )

    try:
        response = requests.post(
            API_URL,
            headers={
                "Authorization": f"Bearer {NVIDIA_API_KEY}",
                "Accept": "application/json",
            },
            json={
                "model": MODEL,
                "messages": [{"role": "user", "content": content}],
                "max_tokens": 1500,
                "temperature": 0.1,
            },
            timeout=90,
        )
        response.raise_for_status()
        report = response.json()["choices"][0]["message"]["content"]
    except Exception:
        app.logger.exception("Model request failed")
        return jsonify({"error": "فشل الاتصال بخدمة التحليل، حاول مرة أخرى"}), 502

    return jsonify({"report": sanitize_report(report) + DISCLAIMER})


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", 8080)))
