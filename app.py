import os
import requests
from datetime import datetime, date
from flask import Flask, request, render_template, session, redirect, url_for
import anthropic
from dotenv import load_dotenv
from study_plan_content import build_system_prompt

load_dotenv()

app = Flask(__name__)
app.secret_key = os.environ.get("FLASK_SECRET_KEY", "dev-secret-change-in-production")


# ── Gumroad license validation ─────────────────────────────────────────────

def validate_gumroad_key(license_key: str) -> tuple[bool, str]:
    """
    Returns (is_valid, error_message).
    Calls Gumroad's license verify API.
    """
    product_permalink = os.environ.get("GUMROAD_PRODUCT_PERMALINK", "")
    if not product_permalink:
        # If not configured, allow access (dev mode)
        return True, ""
    try:
        resp = requests.post(
            "https://api.gumroad.com/v2/licenses/verify",
            data={
                "product_permalink": product_permalink,
                "license_key": license_key.strip(),
                "increment_uses_count": "false",
            },
            timeout=10,
        )
        data = resp.json()
        if data.get("success"):
            return True, ""
        else:
            msg = data.get("message", "Invalid license key.")
            return False, msg
    except requests.Timeout:
        return False, "Verification timed out. Please try again."
    except Exception:
        return False, "Could not verify your key. Please try again."


def is_unlocked() -> bool:
    return session.get("unlocked") is True


# ── Routes ─────────────────────────────────────────────────────────────────

@app.route("/unlock", methods=["GET", "POST"])
def unlock():
    error = None
    if request.method == "POST":
        key = request.form.get("license_key", "").strip()
        if not key:
            error = "Please enter your license key."
        else:
            valid, msg = validate_gumroad_key(key)
            if valid:
                session["unlocked"] = True
                session["license_key"] = key
                return redirect(url_for("index"))
            else:
                error = msg or "That key didn't work. Check your Gumroad receipt and try again."
    return render_template("unlock.html", error=error)


@app.route("/")
def index():
    if not is_unlocked():
        return redirect(url_for("unlock"))
    return render_template("index.html")


@app.route("/generate", methods=["POST"])
def generate():
    if not is_unlocked():
        return redirect(url_for("unlock"))

    # ── Parse form inputs ──────────────────────────────────────────────────
    name = request.form.get("name", "").strip()
    current_verbal = request.form.get("current_verbal", "untested")
    current_quant = request.form.get("current_quant", "untested")
    target_verbal = request.form.get("target_verbal", "160")
    target_quant = request.form.get("target_quant", "160")
    test_date_str = request.form.get("test_date", "")
    weekday_hours = float(request.form.get("weekday_hours", "2"))
    weekend_hours = float(request.form.get("weekend_hours", "3"))
    resources = request.form.getlist("resources")
    primary_weakness = request.form.get("primary_weakness", "both")
    biggest_challenge = request.form.get("biggest_challenge", "").strip()

    # ── Calculate weeks available ──────────────────────────────────────────
    weeks_available = 12
    if test_date_str:
        try:
            test_date = datetime.strptime(test_date_str, "%Y-%m-%d").date()
            days_remaining = (test_date - date.today()).days
            weeks_available = max(1, days_remaining // 7)
        except ValueError:
            pass

    # ── Weekly hours ───────────────────────────────────────────────────────
    total_weekly_hours = (weekday_hours * 5) + (weekend_hours * 2)

    # ── Human-readable labels ──────────────────────────────────────────────
    verbal_display = (
        "Haven't tested yet (estimate: ~150)"
        if current_verbal == "untested"
        else current_verbal
    )
    quant_display = (
        "Haven't tested yet (estimate: ~150)"
        if current_quant == "untested"
        else current_quant
    )
    weakness_labels = {
        "verbal": "Verbal (Reading Comprehension, Text Completion, Sentence Equivalence)",
        "quant": "Quant (Math — Arithmetic, Algebra, Geometry, Data Analysis)",
        "both": "Both Verbal and Quant equally",
    }
    weakness_display = weakness_labels.get(primary_weakness, primary_weakness)
    resources_display = ", ".join(resources) if resources else "Not specified"

    # ── Build Claude prompt ────────────────────────────────────────────────
    user_message = f"""Please generate a personalized GRE study plan for this student:

Student name: {name if name else "Not provided"}
Current Verbal score: {verbal_display}
Current Quant score: {quant_display}
Target Verbal score: {target_verbal}
Target Quant score: {target_quant}
Weeks until test date: {weeks_available}
Weekday study hours per day: {weekday_hours}
Weekend study hours per day: {weekend_hours}
Total weekly study hours: {total_weekly_hours:.1f}
Resources available: {resources_display}
Primary weakness: {weakness_display}
Biggest challenge: {biggest_challenge if biggest_challenge else "Not specified"}

Please create a detailed week-by-week study plan tailored exactly to this student's situation."""

    # ── Call Claude API ────────────────────────────────────────────────────
    client = anthropic.Anthropic(api_key=os.environ.get("ANTHROPIC_API_KEY"))
    plan_markdown = ""
    error_message = None

    try:
        with client.messages.stream(
            model="claude-sonnet-4-6",
            max_tokens=4096,
            system=build_system_prompt(),
            messages=[{"role": "user", "content": user_message}],
        ) as stream:
            response = stream.get_final_message()
        plan_markdown = response.content[0].text
    except anthropic.AuthenticationError:
        error_message = "API key is invalid or missing."
    except anthropic.RateLimitError:
        error_message = "Rate limit hit. Please wait a moment and try again."
    except Exception as e:
        error_message = f"Something went wrong generating your plan: {str(e)}"

    return render_template(
        "plan.html",
        plan_markdown=plan_markdown,
        error_message=error_message,
        name=name,
        weeks_available=weeks_available,
        target_verbal=target_verbal,
        target_quant=target_quant,
    )


if __name__ == "__main__":
    app.run(debug=True)
