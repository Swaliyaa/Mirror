import psycopg2
from psycopg2.extras import RealDictCursor
from psycopg2 import IntegrityError
import json
from flask import Flask, request, session, redirect, render_template, jsonify, send_from_directory
from datetime import datetime, timedelta, time
import os
from werkzeug.security import generate_password_hash, check_password_hash
from functools import wraps

# NEW: imports for email + tokens
import smtplib
from email.mime.text import MIMEText
import secrets
from urllib.parse import urlencode
from zoneinfo import ZoneInfo  # timezone support

app = Flask(__name__)
app.secret_key = "mirror_secret_key_change_later"

# NEW: Gmail + base URL config (via env vars)
GMAIL_USER = os.environ.get("GMAIL_USER")
GMAIL_APP_PASSWORD = os.environ.get("GMAIL_APP_PASSWORD")
BASE_URL = os.environ.get("BASE_URL", "http://localhost:5000")


# ----------------------------
# DATABASE CONNECTION
# ----------------------------
def get_db_connection():
    return psycopg2.connect(
        os.environ["DATABASE_URL"],
        sslmode="require"
    )


# ----------------------------
# LOGIN REQUIRED DECORATOR
# ----------------------------
def login_required(f):
    @wraps(f)
    def decorated_function(*args, **kwargs):
        if "user_id" not in session:
            if request.path.startswith('/api/'):
                return jsonify({"error": "Unauthorized"}), 401
            return redirect("/login")
        return f(*args, **kwargs)
    return decorated_function


# ----------------------------
# INITIALIZE DATABASE
# ----------------------------
def init_db():
    conn = get_db_connection()
    cursor = conn.cursor()

    cursor.execute("""
        CREATE TABLE IF NOT EXISTS users (
            id SERIAL PRIMARY KEY,
            username VARCHAR(255) UNIQUE NOT NULL,
            fullname VARCHAR(255),
            password VARCHAR(255) NOT NULL,
            timezone VARCHAR(50) DEFAULT 'Asia/Kolkata'
        );
    """)

    cursor.execute("""
        CREATE TABLE IF NOT EXISTS todo (
            id VARCHAR(255) PRIMARY KEY,
            user_id INTEGER REFERENCES users(id),
            text TEXT NOT NULL,
            category VARCHAR(100),
            priority VARCHAR(50),
            due_date DATE,
            completed BOOLEAN DEFAULT FALSE,
            created_at TIMESTAMP
        );
    """)

    cursor.execute("""
        CREATE TABLE IF NOT EXISTS habit (
            id VARCHAR(255) PRIMARY KEY,
            user_id INTEGER REFERENCES users(id),
            name VARCHAR(255) NOT NULL,
            icon VARCHAR(100),
            category VARCHAR(100),
            frequency VARCHAR(50),
            completion_history TEXT,
            created_at TIMESTAMP
        );
    """)

    cursor.execute("""
        CREATE TABLE IF NOT EXISTS mood (
            id SERIAL PRIMARY KEY,
            user_id INTEGER REFERENCES users(id),
            mood_emoji VARCHAR(10),
            mood_name VARCHAR(50),
            reflection TEXT,
            date DATE,
            timestamp BIGINT
        );
    """)

    cursor.execute("""
        CREATE TABLE IF NOT EXISTS password_reset_tokens (
            id SERIAL PRIMARY KEY,
            user_id INTEGER REFERENCES users(id) ON DELETE CASCADE,
            token VARCHAR(255) UNIQUE NOT NULL,
            expires_at TIMESTAMP NOT NULL
        );
    """)

    conn.commit()
    cursor.close()
    conn.close()


with app.app_context():
    init_db()


# ----------------------------
# EMAIL HELPER
# ----------------------------
def send_reset_email(to_email, reset_link):
    if not GMAIL_USER or not GMAIL_APP_PASSWORD:
        print("Gmail env vars not set; cannot send email.", flush=True)
        return

    subject = "Reset your password - The Mirror"
    body = f"""Hi,

We received a request to reset the password for your account on The Mirror.

To reset your password, click this link:

{reset_link}

If you did not request this, you can safely ignore this email.

– The Mirror
"""

    msg = MIMEText(body)
    msg["Subject"] = subject
    msg["From"] = GMAIL_USER
    msg["To"] = to_email

    try:
        server = smtplib.SMTP("smtp.gmail.com", 587, timeout=10)
        server.starttls()
        server.login(GMAIL_USER, GMAIL_APP_PASSWORD)
        server.sendmail(GMAIL_USER, [to_email], msg.as_string())
        server.quit()
        print("Reset email sent successfully", flush=True)
    except Exception as e:
        print("SMTP ERROR:", e, flush=True)


# ----------------------------
# STATIC ROUTES
# ----------------------------
@app.route('/_sdk/<path:filename>')
def serve_sdk(filename):
    return send_from_directory('static/js', filename)


# ----------------------------
# AUTH ROUTES
# ----------------------------
@app.route("/")
def home():
    if "user_id" in session:
        return redirect("/dashboard")
    return render_template("welcome.html")


@app.route("/login", methods=["GET", "POST"])
@app.route("/login.html", methods=["GET", "POST"])
def login():
    conn = get_db_connection()
    cursor = conn.cursor(cursor_factory=RealDictCursor)
    message = ""

    if request.method == "POST":
        username = request.form.get("username", "").strip()
        password = request.form.get("password", "")

        if not username or not password:
            message = "All fields are required."
            cursor.close()
            conn.close()
            return render_template("login.html", active_view="signin", error=message)

        cursor.execute("SELECT * FROM users WHERE username=%s", (username,))
        user = cursor.fetchone()

        if user and check_password_hash(user["password"], password):
            session["user_id"] = user["id"]
            cursor.close()
            conn.close()
            return redirect("/dashboard")

        if not user:
            message = "No account found. Please sign up first."
            active_view = "signup"
        else:
            message = "Incorrect password. Try again."
            active_view = "signin"

        cursor.close()
        conn.close()
        return render_template("login.html", active_view=active_view, error=message)

    cursor.close()
    conn.close()
    return render_template("login.html", active_view="signin", error=message)


@app.route("/register", methods=["GET", "POST"])
def register():
    conn = get_db_connection()
    cursor = conn.cursor(cursor_factory=RealDictCursor)
    message = ""

    if request.method == "POST":
        username = request.form.get("username", "").strip()
        fullname = request.form.get("fullname", "").strip()
        password_raw = request.form.get("password", "")

        if not username or not password_raw:
            message = "All fields are required."
            cursor.close()
            conn.close()
            return render_template("login.html", active_view="signup", error=message)

        password = generate_password_hash(password_raw)

        try:
            cursor.execute(
                "INSERT INTO users (username, password, fullname) VALUES (%s, %s, %s)",
                (username, password, fullname)
            )
            conn.commit()
            cursor.close()
            conn.close()
            return redirect("/login")
        except IntegrityError:
            conn.rollback()
            message = "Username already exists."

    cursor.close()
    conn.close()
    return render_template("login.html", active_view="signup", error=message)


@app.route("/logout")
@login_required
def logout():
    session.clear()
    return redirect("/login")


# ----------------------------
# FORGOT / RESET PASSWORD
# ----------------------------
@app.route("/forgot-password", methods=["POST"])
def forgot_password():
    email = request.json.get("email") if request.is_json else request.form.get("email")
    if not email:
        return jsonify({"isOk": False, "message": "Email is required."}), 400

    conn = get_db_connection()
    cursor = conn.cursor(cursor_factory=RealDictCursor)

    cursor.execute("SELECT id FROM users WHERE username=%s", (email,))
    user = cursor.fetchone()

    if not user:
        cursor.close()
        conn.close()
        return jsonify({"isOk": True, "message": "If an account exists, a reset link has been sent."})

    user_id = user["id"]

    token = secrets.token_urlsafe(32)
    expires_at = datetime.utcnow() + timedelta(hours=1)

    cursor.execute("DELETE FROM password_reset_tokens WHERE user_id=%s", (user_id,))

    cursor.execute(
        "INSERT INTO password_reset_tokens (user_id, token, expires_at) VALUES (%s, %s, %s)",
        (user_id, token, expires_at)
    )
    conn.commit()
    cursor.close()
    conn.close()

    query = urlencode({"token": token})
    reset_link = f"{BASE_URL}/reset-password?{query}"

    send_reset_email(email, reset_link)

    return jsonify({"isOk": True, "message": "If an account exists, a reset link has been sent."})


@app.route("/reset-password", methods=["GET", "POST"])
def reset_password():
    token = request.args.get("token") or request.form.get("token")
    if not token:
        return "Invalid reset link.", 400

    conn = get_db_connection()
    cursor = conn.cursor(cursor_factory=RealDictCursor)

    cursor.execute(
        "SELECT pr.user_id FROM password_reset_tokens pr WHERE pr.token=%s AND pr.expires_at > %s",
        (token, datetime.utcnow())
    )
    row = cursor.fetchone()

    if not row:
        cursor.close()
        conn.close()
        return "This reset link is invalid or has expired.", 400

    user_id = row["user_id"]

    if request.method == "GET":
        cursor.close()
        conn.close()
        return render_template("reset_password.html", token=token)

    new_password = request.form.get("password")
    confirm = request.form.get("confirm")

    if not new_password or new_password != confirm:
        cursor.close()
        conn.close()
        return render_template("reset_password.html", token=token, error="Passwords do not match.")

    hashed = generate_password_hash(new_password)
    cursor.execute("UPDATE users SET password=%s WHERE id=%s", (hashed, user_id))
    cursor.execute("DELETE FROM password_reset_tokens WHERE user_id=%s", (user_id,))
    conn.commit()
    cursor.close()
    conn.close()

    return redirect("/login")


# ----------------------------
# DASHBOARD
# ----------------------------
@app.route("/dashboard")
@app.route("/dashboard.html")
@login_required
def dashboard():
    user_id = session["user_id"]
    conn = get_db_connection()
    cursor = conn.cursor(cursor_factory=RealDictCursor)

    todos_list, habits_list, mood_list = [], [], []

    cursor.execute("""
        SELECT id, text, completed,
               COALESCE(due_date::date, created_at::date, CURRENT_DATE) AS effective_date
        FROM todo
        WHERE user_id=%s
        ORDER BY created_at DESC
        LIMIT 5
    """, (user_id,))

    for t in cursor.fetchall():
        todos_list.append({
            "id": t["id"],
            "type": "todo",
            "content": t["text"],
            "completed": bool(t["completed"]),
            "date": t["effective_date"].isoformat()
        })

    cursor.execute("SELECT * FROM habit WHERE user_id=%s", (user_id,))
    today_date = datetime.now().date()
    today_str = today_date.strftime("%Y-%m-%d")

    for h in cursor.fetchall():
        try:
            history = json.loads(h["completion_history"] or "{}")
            if not isinstance(history, dict):
                history = {}
        except (json.JSONDecodeError, TypeError):
            history = {}

        streak = 0
        d = today_date
        while True:
            key = d.strftime("%Y-%m-%d")
            if history.get(key):
                streak += 1
                d = d - timedelta(days=1)
            else:
                break

        habits_list.append({
            "id": h["id"],
            "type": "habit",
            "content": h["name"],
            "streak": streak,
            "completed": history.get(today_str, False)
        })

    cursor.execute(
        "SELECT * FROM mood WHERE user_id=%s ORDER BY id DESC LIMIT 3",
        (user_id,)
    )
    for m in cursor.fetchall():
        mood_list.append({
            "id": m["id"],
            "type": "mood",
            "content": m["mood_name"],
            "date": m["date"].isoformat()
        })

    cursor.close()
    conn.close()
    return render_template(
        "dashboard.html",
        initial_data=todos_list + habits_list + mood_list
    )


# ----------------------------
# PAGE ROUTES
# ----------------------------
@app.route("/todo")
@app.route("/todo/")
@login_required
def todo_page():
    return render_template("todo.html")


@app.route("/habit")
@app.route("/habit/")
@login_required
def habit_page():
    return render_template("habit.html")


@app.route("/mood")
@app.route("/mood/")
@login_required
def mood_page():
    return render_template("mood.html")


# ----------------------------
# API: TODOS
# ----------------------------
@app.route("/api/todos", methods=["GET", "POST"])
@login_required
def api_todos():
    user_id = session["user_id"]
    conn = get_db_connection()
    cursor = conn.cursor(cursor_factory=RealDictCursor)

    if request.method == "POST":
        data = request.get_json()
        if not data:
            cursor.close()
            conn.close()
            return jsonify({"isOk": False, "message": "Invalid JSON"}), 400

        text = data.get("text", "").strip()
        if not text:
            cursor.close()
            conn.close()
            return jsonify({"isOk": False, "message": "Task text is required."}), 400

        if len(text) > 300:
            cursor.close()
            conn.close()
            return jsonify({"isOk": False, "message": "Task too long."}), 400

        raw_due = data.get("dueDate")
        if raw_due:
            if len(raw_due) == 10:
                due = datetime.strptime(raw_due, "%Y-%m-%d").date()
            else:
                due = datetime.fromisoformat(raw_due).date()
        else:
            due = None

        cursor.execute(
            """
            INSERT INTO todo (id, user_id, text, category, priority, due_date, completed, created_at)
            VALUES (%s,%s,%s,%s,%s,%s,%s,%s)
            """,
            (
                data["id"],
                user_id,
                text,
                data.get("category", ""),
                data.get("priority", ""),
                due,
                False,
                datetime.fromisoformat(data["createdAt"]) if data.get("createdAt") else datetime.now(),
            ),
        )
        conn.commit()
        cursor.close()
        conn.close()
        return jsonify({"isOk": True})

    cursor.execute(
        """
        SELECT id, text, category, priority, completed,
               COALESCE(due_date::date, created_at::date, CURRENT_DATE) AS effective_date
        FROM todo
        WHERE user_id=%s
        ORDER BY created_at DESC
        """,
        (user_id,),
    )
    rows = cursor.fetchall()

    todos = []
    for t in rows:
        todos.append(
            {
                "id": t["id"],
                "text": t["text"],
                "category": t["category"],
                "priority": t["priority"],
                "completed": bool(t["completed"]),
                "dueDate": t["effective_date"].isoformat(),
            }
        )

    cursor.close()
    conn.close()
    return jsonify({"isOk": True, "todos": todos})


# ----------------------------
# API: TOGGLE / CLEAR
# ----------------------------
@app.route("/api/todos/<id>", methods=["PUT", "DELETE"])
@login_required
def api_todo_item(id):
    user_id = session["user_id"]
    conn = get_db_connection()
    cursor = conn.cursor()

    if request.method == "DELETE":
        cursor.execute("DELETE FROM todo WHERE id=%s AND user_id=%s", (id, user_id))
    else:
        data = request.get_json() or {}
        cursor.execute(
            "UPDATE todo SET completed=%s WHERE id=%s AND user_id=%s",
            (bool(data.get("completed", False)), id, user_id)
        )

    conn.commit()
    cursor.close()
    conn.close()
    return jsonify({"isOk": True})


@app.route("/api/todos/clear-completed", methods=["DELETE"])
@login_required
def clear_completed():
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute(
        "DELETE FROM todo WHERE completed=TRUE AND user_id=%s",
        (session["user_id"],)
    )
    conn.commit()
    cursor.close()
    conn.close()
    return jsonify({"isOk": True})


# ----------------------------
# API: HABITS
# ----------------------------
@app.route("/api/habits", methods=["GET", "POST"])
@login_required
def api_habits():
    user_id = session["user_id"]
    conn = get_db_connection()
    cursor = conn.cursor(cursor_factory=RealDictCursor)

    if request.method == "POST":
        data = request.get_json()
        if not data:
            cursor.close()
            conn.close()
            return jsonify({"isOk": False, "message": "Invalid JSON"}), 400

        name = data.get("name", "").strip()
        if not name:
            cursor.close()
            conn.close()
            return jsonify({"isOk": False, "message": "Habit name required."}), 400

        history = json.dumps(data.get("completion_history", {}))
        cursor.execute("""
            INSERT INTO habit (id, user_id, name, icon, category, frequency, completion_history, created_at)
            VALUES (%s,%s,%s,%s,%s,%s,%s,%s)
        """, (
            data["id"], user_id, name, data.get("icon", ""),
            data.get("category", ""), data.get("frequency", ""), history, datetime.now()
        ))
        conn.commit()

    cursor.execute("SELECT * FROM habit WHERE user_id=%s", (user_id,))
    rows = cursor.fetchall()
    cursor.close()
    conn.close()
    return jsonify(rows)


# ----------------------------
# API: UPDATE HABIT (TOGGLE)
# ----------------------------
@app.route("/api/habits/<id>", methods=["PUT"])
@login_required
def update_habit(id):
    user_id = session["user_id"]
    data = request.get_json() or {}

    today = datetime.now().strftime("%Y-%m-%d")
    completed = bool(data.get("completed"))

    conn = get_db_connection()
    cursor = conn.cursor(cursor_factory=RealDictCursor)

    cursor.execute(
        "SELECT completion_history FROM habit WHERE id=%s AND user_id=%s",
        (id, user_id)
    )
    row = cursor.fetchone()

    try:
        history = json.loads(row["completion_history"] or "{}")
        if not isinstance(history, dict):
            history = {}
    except (json.JSONDecodeError, TypeError):
        history = {}

    history[today] = completed

    cursor.execute(
        """
        UPDATE habit
        SET completion_history=%s
        WHERE id=%s AND user_id=%s
        """,
        (json.dumps(history), id, user_id)
    )

    conn.commit()
    cursor.close()
    conn.close()

    return jsonify({"isOk": True})


@app.route("/api/habits/<id>", methods=["DELETE"])
@login_required
def delete_habit(id):
    user_id = session["user_id"]
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute(
        "DELETE FROM habit WHERE id=%s AND user_id=%s",
        (id, user_id)
    )
    conn.commit()
    cursor.close()
    conn.close()
    return jsonify({"isOk": True})


# ----------------------------
# API: MOODS
# ----------------------------
@app.route("/api/moods", methods=["GET", "POST"])
@login_required
def api_moods():
    user_id = session["user_id"]
    conn = get_db_connection()
    cursor = conn.cursor(cursor_factory=RealDictCursor)

    if request.method == "POST":
        data = request.get_json()
        if not data:
            cursor.close()
            conn.close()
            return jsonify({"isOk": False, "message": "Invalid JSON"}), 400

        mood_name = (data.get("mood_name") or "").strip()
        reflection = (data.get("reflection") or "").strip()

        if not mood_name:
            cursor.close()
            conn.close()
            return jsonify({"isOk": False, "message": "Mood required."}), 400

        if len(reflection) > 1000:
            cursor.close()
            conn.close()
            return jsonify({"isOk": False, "message": "Reflection too long."}), 400

        cursor.execute("""
            INSERT INTO mood (user_id, mood_emoji, mood_name, reflection, date, timestamp)
            VALUES (%s,%s,%s,%s,%s,%s)
        """, (
            user_id,
            data.get("mood_emoji", ""),
            mood_name,
            reflection,
            data["date"],
            data["timestamp"]
        ))
        conn.commit()

    cursor.execute(
        "SELECT * FROM mood WHERE user_id=%s ORDER BY timestamp DESC",
        (user_id,)
    )
    rows = cursor.fetchall()
    cursor.close()
    conn.close()
    return jsonify(rows)


# ----------------------------
# API: SUMMARY ENDPOINTS
# ----------------------------
@app.route("/api/summary/today-todos", methods=["GET"])
@login_required
def summary_today_todos():
    user_id = session["user_id"]
    conn = get_db_connection()
    cursor = conn.cursor(cursor_factory=RealDictCursor)

    cursor.execute("""
        SELECT id, text, completed,
               COALESCE(due_date::date, created_at::date) AS effective_date
        FROM todo
        WHERE user_id = %s
          AND completed = FALSE
        ORDER BY created_at DESC
    """, (user_id,))
    rows = cursor.fetchall()

    cursor.close()
    conn.close()

    total = len(rows)
    completed = 0
    pending = total

    return jsonify({
        "isOk": True,
        "total": total,
        "completed": completed,
        "pending": pending,
        "items": rows
    })


@app.route("/api/summary/recent-habits", methods=["GET"])
@login_required
def summary_recent_habits():
    user_id = session["user_id"]
    days = int(request.args.get("days", 3))
    today = datetime.now().date()
    start_date = today - timedelta(days=days - 1)

    conn = get_db_connection()
    cursor = conn.cursor(cursor_factory=RealDictCursor)

    cursor.execute("""
        SELECT id, name, completion_history
        FROM habit
        WHERE user_id = %s
    """, (user_id,))
    habits = cursor.fetchall()

    summaries = []
    for h in habits:
        try:
            history = json.loads(h["completion_history"] or "{}")
            if not isinstance(history, dict):
                history = {}
        except (json.JSONDecodeError, TypeError):
            history = {}

        streak_count = 0
        days_logged = 0

        for i in range(days):
            d = (start_date + timedelta(days=i)).strftime("%Y-%m-%d")
            if d in history:
                days_logged += 1
                if history.get(d):
                    streak_count += 1

        summaries.append({
            "id": h["id"],
            "name": h["name"],
            "days_window": days,
            "days_logged": days_logged,
            "completed_days": streak_count
        })

    cursor.close()
    conn.close()

    return jsonify({
        "isOk": True,
        "start_date": start_date.isoformat(),
        "end_date": today.isoformat(),
        "habits": summaries
    })


@app.route("/api/summary/recent-moods", methods=["GET"])
@login_required
def summary_recent_moods():
    user_id = session["user_id"]
    days = int(request.args.get("days", 7))
    today = datetime.now().date()
    start_date = today - timedelta(days=days - 1)

    conn = get_db_connection()
    cursor = conn.cursor(cursor_factory=RealDictCursor)

    cursor.execute("""
        SELECT id, mood_name, mood_emoji, reflection, date, timestamp
        FROM mood
        WHERE user_id = %s
          AND date >= %s
        ORDER BY date DESC, timestamp DESC
    """, (user_id, start_date))
    rows = cursor.fetchall()

    mood_counts = {}
    for r in rows:
        name = r["mood_name"]
        mood_counts[name] = mood_counts.get(name, 0) + 1

    cursor.close()
    conn.close()

    return jsonify({
        "isOk": True,
        "start_date": start_date.isoformat(),
        "end_date": today.isoformat(),
        "entries": rows,
        "counts": mood_counts
    })


# ----------------------------
# API: REMINDERS (timezone-aware)
# ----------------------------
@app.route("/api/reminders", methods=["GET"])
@login_required
def api_reminders():
    user_id = session["user_id"]
    conn = get_db_connection()
    cursor = conn.cursor(cursor_factory=RealDictCursor)

    user_tz = ZoneInfo("Asia/Kolkata")

    now = datetime.now(user_tz)
    today = now.date()
    tomorrow = today + timedelta(days=1)
    now_t = now.time()

    night_start = time(21, 0)
    night_end = time(0, 0)
    in_night_window = now_t >= night_start or now_t < night_end

    cursor.execute("""
        SELECT id, text, completed, due_date, created_at
        FROM todo
        WHERE user_id = %s
        ORDER BY created_at DESC
    """, (user_id,))
    todos = cursor.fetchall()

    def todo_effective_date_user(row):
        if row["due_date"]:
            return row["due_date"]
        created = row["created_at"]
        if created is None:
            return today
        if created.tzinfo is None:
            created = created.replace(tzinfo=ZoneInfo("UTC"))
        return created.astimezone(user_tz).date()

    due_today = [t for t in todos if not t["completed"] and todo_effective_date_user(t) == today]
    due_tomorrow = [t for t in todos if not t["completed"] and todo_effective_date_user(t) == tomorrow]
    past_due = [t for t in todos if not t["completed"] and todo_effective_date_user(t) < today]

    cursor.execute("SELECT id, name, completion_history FROM habit WHERE user_id = %s", (user_id,))
    habits = cursor.fetchall()

    today_str = today.strftime("%Y-%m-%d")
    habits_not_done_today = []
    for h in habits:
        try:
            history = json.loads(h["completion_history"] or "{}")
            if not isinstance(history, dict):
                history = {}
        except (json.JSONDecodeError, TypeError):
            history = {}
        if not history.get(today_str, False):
            habits_not_done_today.append(h["name"])

    cursor.execute("""
        SELECT date
        FROM mood
        WHERE user_id = %s
        ORDER BY date DESC
        LIMIT 1
    """, (user_id,))
    last_mood = cursor.fetchone()

    cursor.close()
    conn.close()

    mood_logged_today = last_mood is not None and last_mood["date"] == today

    due_today_names = [t["text"] for t in due_today]
    past_due_names = [t["text"] for t in past_due]
    pending_today_names = [t["text"] for t in due_today]
    due_tomorrow_names = [t["text"] for t in due_tomorrow]

    reminders = {
        "todo_due_today": due_today_names,
        "todo_overdue": past_due_names,
        "todo_pending_today": pending_today_names if in_night_window else [],
        "todo_tomorrow": due_tomorrow_names if in_night_window else [],
        "habits_not_done": habits_not_done_today if in_night_window else [],
        "show_mood_missing": (not mood_logged_today) if in_night_window else False,
    }

    return jsonify({
        "isOk": True,
        "reminders": reminders,
        "serverTime": datetime.utcnow().isoformat(),
        "userTime": now.isoformat()
    })


# ----------------------------
# WAKE ROUTE
# ----------------------------
@app.route("/wake")
def wake():
    return "Mirror FYP awake! 💫"

