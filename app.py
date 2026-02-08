import psycopg2

from psycopg2.extras import RealDictCursor
from psycopg2 import IntegrityError
import json
from flask import Flask, request, session, redirect, render_template, jsonify, send_from_directory
from datetime import datetime
import os
from werkzeug.security import generate_password_hash, check_password_hash
from functools import wraps

app = Flask(__name__)
app.secret_key = "mirror_secret_key_change_later"

# ----------------------------
# DATABASE CONNECTION
# ----------------------------
def get_db_connection():
    return psycopg2.connect(os.environ["DATABASE_URL"])

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
            password VARCHAR(255) NOT NULL
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

    conn.commit()
    cursor.close()
    conn.close()

# Run init on startup (Render-safe)
with app.app_context():
    init_db()

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
        username = request.form["username"]
        password = request.form["password"]
        cursor.execute("SELECT * FROM users WHERE username=%s", (username,))
        user = cursor.fetchone()

        if user and check_password_hash(user["password"], password):
            session["user_id"] = user["id"]
            cursor.close()
            conn.close()
            return redirect("/dashboard")
        else:
            message = "Invalid credentials."

    cursor.close()
    conn.close()
    return render_template("login.html", active_view="signin", error=message)

@app.route("/register", methods=["GET", "POST"])
def register():
    conn = get_db_connection()
    cursor = conn.cursor(cursor_factory=RealDictCursor)
    message = ""

    if request.method == "POST":
        username = request.form["username"]
        fullname = request.form.get("fullname", "")
        password = generate_password_hash(request.form["password"])

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

    # --- Fetch todos ---
    cursor.execute("SELECT * FROM todo WHERE user_id=%s ORDER BY created_at DESC LIMIT 3", (user_id,))
    for t in cursor.fetchall():
        todos_list.append({
            "id": t["id"],
            "type": "todo",
            "content": t["text"],
            "completed": bool(t["completed"])
        })


    cursor.execute("SELECT * FROM habit WHERE user_id=%s", (user_id,))
    today = datetime.now().strftime("%Y-%m-%d")
    for h in cursor.fetchall():
        # Safe JSON parsing
        try:
            history = json.loads(h["completion_history"] or "{}")
            if not isinstance(history, dict):
                history = {}
        except (json.JSONDecodeError, TypeError):
            history = {}

        habits_list.append({
            "id": h["id"],
            "type": "habit",
            "content": h["name"],
            "streak": 0,  # can calculate later if you add streak logic
            "completed": history.get(today, False)
        })

    # --- Fetch moods ---
    cursor.execute("SELECT * FROM mood WHERE user_id=%s ORDER BY id DESC LIMIT 3", (user_id,))
    for m in cursor.fetchall():
        mood_list.append({
            "id": m["id"],
            "type": "mood",
            "content": m["mood_name"].lower()
        })

    cursor.close()
    conn.close()
    return render_template("dashboard.html", initial_data=todos_list + habits_list + mood_list)
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
        data = request.json
        cursor.execute("""
            INSERT INTO todo (id, user_id, text, category, priority, due_date, completed, created_at)
            VALUES (%s,%s,%s,%s,%s,%s,%s,%s)
        """, (
            data["id"], user_id, data["text"],
            data.get("category",""), data.get("priority",""),
            datetime.strptime(data["dueDate"], "%Y-%m-%d") if data.get("dueDate") else None,
            False,
            datetime.fromisoformat(data["createdAt"]) if data.get("createdAt") else datetime.now()
        ))
        conn.commit()
        cursor.close()
        conn.close()
        return jsonify({"isOk": True})

    cursor.execute("SELECT * FROM todo WHERE user_id=%s", (user_id,))
    rows = cursor.fetchall()
    cursor.close()
    conn.close()
    return jsonify({"isOk": True, "todos": rows})

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
        cursor.execute("UPDATE todo SET completed=%s WHERE id=%s AND user_id=%s",
                       (request.json.get("completed", False), id, user_id))

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
        data = request.json
        # Make sure completion_history is always JSON string
        history = json.dumps(data.get("completion_history", {}))
        cursor.execute("""
            INSERT INTO habit (id, user_id, name, icon, category, frequency, completion_history, created_at)
            VALUES (%s,%s,%s,%s,%s,%s,%s,%s)
        """, (
            data["id"], user_id, data["name"], data.get("icon",""),
            data.get("category",""), data.get("frequency",""), history, datetime.now()
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
    data = request.json

    conn = get_db_connection()
    cursor = conn.cursor()

    cursor.execute("""
        UPDATE habit
        SET completion_history = %s
        WHERE id = %s AND user_id = %s
    """, (
        data.get("completion_history", "{}"),
        id,
        user_id
    ))

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
        data = request.json
        cursor.execute("""
            INSERT INTO mood (user_id, mood_emoji, mood_name, reflection, date, timestamp)
            VALUES (%s,%s,%s,%s,%s,%s)
        """, (
            user_id, data["mood_emoji"], data["mood_name"],
            data["reflection"], data["date"], data["timestamp"]
        ))
        conn.commit()

    cursor.execute("SELECT * FROM mood WHERE user_id=%s ORDER BY timestamp DESC", (user_id,))
    rows = cursor.fetchall()
    cursor.close()
    conn.close()
    return jsonify(rows)
