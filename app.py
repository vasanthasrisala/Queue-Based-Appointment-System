from flask import Flask, render_template, request, redirect, url_for, flash, session
import sqlite3
from datetime import datetime, timedelta
import hashlib
import uuid

app = Flask(__name__)
app.secret_key = "supersecretkey"
DATABASE = "hospital.db"


def get_db_connection():
    conn = sqlite3.connect(DATABASE)
    conn.row_factory = sqlite3.Row
    return conn


def hash_password(password):
    return hashlib.sha256(password.encode()).hexdigest()


def init_db():
    conn = get_db_connection()
    cursor = conn.cursor()

    cursor.execute("""
        CREATE TABLE IF NOT EXISTS users (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            username TEXT NOT NULL UNIQUE,
            email TEXT NOT NULL UNIQUE,
            password TEXT NOT NULL,
            created_at TEXT NOT NULL
        )
    """)

    cursor.execute("""
        CREATE TABLE IF NOT EXISTS doctors (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT NOT NULL,
            specialization TEXT NOT NULL,
            consultation_fee REAL NOT NULL DEFAULT 500.0
        )
    """)

    cursor.execute("""
        CREATE TABLE IF NOT EXISTS appointments (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            patient_name TEXT NOT NULL,
            age INTEGER NOT NULL,
            gender TEXT NOT NULL,
            phone TEXT NOT NULL,
            appointment_date TEXT NOT NULL,
            appointment_time TEXT NOT NULL,
            reason TEXT NOT NULL,
            is_emergency INTEGER DEFAULT 0,
            status TEXT DEFAULT 'Waiting',
            payment_status TEXT DEFAULT 'Pending',
            created_at TEXT NOT NULL,
            followup_group TEXT DEFAULT NULL,
            user_id INTEGER,
            doctor_id INTEGER,
            FOREIGN KEY (user_id) REFERENCES users(id),
            FOREIGN KEY (doctor_id) REFERENCES doctors(id)
        )
    """)

    cursor.execute("""
        CREATE TABLE IF NOT EXISTS payments (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            appointment_id INTEGER NOT NULL,
            user_id INTEGER NOT NULL,
            amount REAL NOT NULL,
            payment_method TEXT NOT NULL,
            transaction_id TEXT NOT NULL UNIQUE,
            status TEXT DEFAULT 'Success',
            paid_at TEXT NOT NULL,
            FOREIGN KEY (appointment_id) REFERENCES appointments(id),
            FOREIGN KEY (user_id) REFERENCES users(id)
        )
    """)

    existing_doctors = cursor.execute("SELECT COUNT(*) FROM doctors").fetchone()[0]
    if existing_doctors == 0:
        doctors = [
            ('Dr. Arjun Rao',      'Cardiologist',       800.0),
            ('Dr. Priya Sharma',   'Dermatologist',      600.0),
            ('Dr. Srinivas Reddy', 'Orthopedic Surgeon', 900.0),
            ('Dr. Meena Patel',    'Gynecologist',       700.0),
            ('Dr. Kiran Kumar',    'Neurologist',        850.0),
            ('Dr. Suresh Babu',    'General Physician',  400.0),
            ('Dr. Anitha Nair',    'Pediatrician',       500.0),
            ('Dr. Ramesh Iyer',    'ENT Specialist',     550.0),
            ('Dr. Divya Menon',    'Ophthalmologist',    600.0),
            ('Dr. Vijay Chandra',  'Psychiatrist',       750.0),
        ]
        cursor.executemany(
            "INSERT INTO doctors (name, specialization, consultation_fee) VALUES (?, ?, ?)",
            doctors
        )

    conn.commit()
    conn.close()


def login_required(f):
    from functools import wraps
    @wraps(f)
    def decorated(*args, **kwargs):
        if "user_id" not in session:
            flash("Please login to access this page.")
            return redirect(url_for("login"))
        return f(*args, **kwargs)
    return decorated


@app.route("/")
def index():
    return render_template("index.html")


@app.route("/register", methods=["GET", "POST"])
def register():
    if request.method == "POST":
        username = request.form["username"].strip()
        email    = request.form["email"].strip()
        password = request.form["password"]
        confirm  = request.form["confirm_password"]

        if password != confirm:
            flash("Passwords do not match.")
            return redirect(url_for("register"))

        if len(password) < 6:
            flash("Password must be at least 6 characters.")
            return redirect(url_for("register"))

        hashed     = hash_password(password)
        created_at = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

        try:
            conn = get_db_connection()
            conn.execute("""
                INSERT INTO users (username, email, password, created_at)
                VALUES (?, ?, ?, ?)
            """, (username, email, hashed, created_at))
            conn.commit()
            conn.close()
            flash("Registration successful! Please login.")
            return redirect(url_for("login"))
        except sqlite3.IntegrityError:
            flash("Username or email already exists.")
            return redirect(url_for("register"))

    return render_template("register.html")


@app.route("/login", methods=["GET", "POST"])
def login():
    if request.method == "POST":
        username = request.form["username"].strip()
        password = request.form["password"]
        hashed   = hash_password(password)

        conn = get_db_connection()
        user = conn.execute("""
            SELECT * FROM users WHERE username = ? AND password = ?
        """, (username, hashed)).fetchone()
        conn.close()

        if user:
            session["user_id"]  = user["id"]
            session["username"] = user["username"]
            flash(f"Welcome back, {user['username']}!")
            return redirect(url_for("index"))
        else:
            flash("Invalid username or password.")
            return redirect(url_for("login"))

    return render_template("login.html")


@app.route("/logout")
def logout():
    session.clear()
    flash("You have been logged out.")
    return redirect(url_for("login"))


# ─── STEP 1: Fill booking form ────────────────────────────────────────────────
@app.route("/book", methods=["GET", "POST"])
@login_required
def book():
    conn    = get_db_connection()
    doctors = conn.execute(
        "SELECT id, name, specialization, consultation_fee FROM doctors ORDER BY specialization, name"
    ).fetchall()
    conn.close()

    if request.method == "POST":
        patient_name     = request.form["patient_name"]
        age              = request.form["age"]
        gender           = request.form["gender"]
        phone            = request.form["phone"]
        appointment_date = request.form["appointment_date"]
        appointment_time = request.form["appointment_time"]
        reason           = request.form["reason"]
        is_emergency     = 1 if request.form.get("is_emergency") == "on" else 0
        doctor_id        = request.form["doctor_id"]

        # Slot conflict check
        conn     = get_db_connection()
        conflict = conn.execute("""
            SELECT id FROM appointments
            WHERE doctor_id = ?
              AND appointment_date = ?
              AND appointment_time = ?
              AND status != 'Cancelled'
        """, (doctor_id, appointment_date, appointment_time)).fetchone()

        if conflict:
            conn.close()
            flash("This time slot is already booked for the selected doctor. Please choose a different time.", "warning")
            return render_template("book.html", doctors=doctors, form=request.form)

        # Save appointment with payment_status = Pending
        created_at = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        cursor     = conn.execute("""
            INSERT INTO appointments (
                patient_name, age, gender, phone, appointment_date,
                appointment_time, reason, is_emergency, created_at,
                user_id, doctor_id, status, payment_status
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'Waiting', 'Pending')
        """, (
            patient_name, age, gender, phone, appointment_date,
            appointment_time, reason, is_emergency, created_at,
            session["user_id"], doctor_id
        ))
        appointment_id = cursor.lastrowid
        conn.commit()
        conn.close()

        # Go to payment page
        return redirect(url_for("payment", appointment_id=appointment_id))

    return render_template("book.html", doctors=doctors, form={})


# ─── STEP 2: Payment page ─────────────────────────────────────────────────────
@app.route("/payment/<int:appointment_id>", methods=["GET", "POST"])
@login_required
def payment(appointment_id):
    conn = get_db_connection()
    appointment = conn.execute("""
        SELECT a.*, d.name AS doctor_name, d.specialization, d.consultation_fee
        FROM appointments a
        JOIN doctors d ON a.doctor_id = d.id
        WHERE a.id = ? AND a.user_id = ?
    """, (appointment_id, session["user_id"])).fetchone()
    conn.close()

    if not appointment:
        flash("Appointment not found.")
        return redirect(url_for("book"))

    if appointment["payment_status"] == "Paid":
        flash("Payment already completed for this appointment.")
        return redirect(url_for("queue"))

    if request.method == "POST":
        payment_method = request.form["payment_method"]
        # Basic validation per method
        if payment_method == "card":
            card_number = request.form.get("card_number", "").replace(" ", "")
            expiry      = request.form.get("expiry", "")
            cvv         = request.form.get("cvv", "")
            if len(card_number) != 16 or not card_number.isdigit():
                flash("Enter a valid 16-digit card number.", "warning")
                return render_template("payment.html", appointment=appointment)
            if not expiry or len(expiry) != 5:
                flash("Enter a valid expiry date (MM/YY).", "warning")
                return render_template("payment.html", appointment=appointment)
            if len(cvv) != 3 or not cvv.isdigit():
                flash("Enter a valid 3-digit CVV.", "warning")
                return render_template("payment.html", appointment=appointment)

        elif payment_method == "upi":
            upi_id = request.form.get("upi_id", "").strip()
            if "@" not in upi_id or len(upi_id) < 5:
                flash("Enter a valid UPI ID (e.g. name@upi).", "warning")
                return render_template("payment.html", appointment=appointment)

        elif payment_method == "netbanking":
            bank = request.form.get("bank", "")
            if not bank:
                flash("Please select a bank.", "warning")
                return render_template("payment.html", appointment=appointment)

        # Generate transaction ID and record payment
        transaction_id = "TXN" + uuid.uuid4().hex[:10].upper()
        paid_at        = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

        conn = get_db_connection()
        conn.execute("""
            INSERT INTO payments (appointment_id, user_id, amount, payment_method, transaction_id, status, paid_at)
            VALUES (?, ?, ?, ?, ?, 'Success', ?)
        """, (
            appointment_id, session["user_id"],
            appointment["consultation_fee"],
            payment_method, transaction_id, paid_at
        ))
        conn.execute("""
            UPDATE appointments SET payment_status = 'Paid' WHERE id = ?
        """, (appointment_id,))
        conn.commit()
        conn.close()

        flash(f"Payment of ₹{appointment['consultation_fee']:.0f} successful! Transaction ID: {transaction_id}", "success")
        return redirect(url_for("payment_success", appointment_id=appointment_id))

    return render_template("payment.html", appointment=appointment)


# ─── STEP 3: Payment success receipt ─────────────────────────────────────────
@app.route("/payment/success/<int:appointment_id>")
@login_required
def payment_success(appointment_id):
    conn = get_db_connection()
    data = conn.execute("""
        SELECT a.*, d.name AS doctor_name, d.specialization, d.consultation_fee,
               p.transaction_id, p.payment_method, p.paid_at, p.amount
        FROM appointments a
        JOIN doctors d ON a.doctor_id = d.id
        JOIN payments p ON p.appointment_id = a.id
        WHERE a.id = ? AND a.user_id = ?
    """, (appointment_id, session["user_id"])).fetchone()
    conn.close()

    if not data:
        flash("Receipt not found.")
        return redirect(url_for("queue"))

    return render_template("payment_success.html", data=data)


@app.route("/queue")
@login_required
def queue():
    conn = get_db_connection()
    appointments = conn.execute("""
        SELECT a.*, d.name AS doctor_name, d.specialization
        FROM appointments a
        LEFT JOIN doctors d ON a.doctor_id = d.id
        WHERE a.status = 'Waiting'
        ORDER BY
            a.is_emergency DESC,
            a.appointment_date ASC,
            a.created_at ASC
    """).fetchall()
    conn.close()
    return render_template("queue.html", appointments=appointments)


@app.route("/doctor", methods=["GET", "POST"])
@login_required
def doctor():
    conn = get_db_connection()

    if request.method == "POST":
        action         = request.form.get("action")
        appointment_id = request.form.get("appointment_id")

        if action == "complete":
            conn.execute("UPDATE appointments SET status = 'Completed' WHERE id = ?", (appointment_id,))
            conn.commit()
            flash("Appointment marked as completed.")
        elif action == "cancel":
            conn.execute("UPDATE appointments SET status = 'Cancelled' WHERE id = ?", (appointment_id,))
            conn.commit()
            flash("Appointment cancelled.")

        conn.close()
        return redirect(url_for("doctor"))

    appointments = conn.execute("""
        SELECT a.*, d.name AS doctor_name, d.specialization
        FROM appointments a
        LEFT JOIN doctors d ON a.doctor_id = d.id
        ORDER BY
            CASE WHEN a.status='Waiting' THEN 1 ELSE 2 END,
            a.is_emergency DESC,
            a.appointment_date ASC,
            a.created_at ASC
    """).fetchall()
    conn.close()
    return render_template("doctor.html", appointments=appointments)


@app.route("/followup/<int:appointment_id>", methods=["GET", "POST"])
@login_required
def followup(appointment_id):
    conn        = get_db_connection()
    appointment = conn.execute("SELECT * FROM appointments WHERE id = ?", (appointment_id,)).fetchone()

    if not appointment:
        conn.close()
        flash("Appointment not found.")
        return redirect(url_for("doctor"))

    if request.method == "POST":
        followup_days = int(request.form["followup_days"])

        if followup_days < 1 or followup_days > 7:
            conn.close()
            flash("Follow-up days must be between 1 and 7.")
            return redirect(url_for("followup", appointment_id=appointment_id))

        followup_group = f"FU-{appointment_id}-{datetime.now().strftime('%Y%m%d%H%M%S')}"
        start_date     = datetime.strptime(appointment["appointment_date"], "%Y-%m-%d")

        for i in range(1, followup_days + 1):
            next_date = (start_date + timedelta(days=i)).strftime("%Y-%m-%d")
            conn.execute("""
                INSERT INTO appointments (
                    patient_name, age, gender, phone, appointment_date,
                    appointment_time, reason, is_emergency, status,
                    created_at, followup_group, user_id, doctor_id, payment_status
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, 'Waiting', ?, ?, ?, ?, 'Paid')
            """, (
                appointment["patient_name"],
                appointment["age"],
                appointment["gender"],
                appointment["phone"],
                next_date,
                appointment["appointment_time"],
                f"Follow-up: {appointment['reason']}",
                0,
                datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                followup_group,
                session["user_id"],
                appointment["doctor_id"]
            ))

        conn.commit()
        conn.close()
        flash(f"{followup_days} follow-up appointments created successfully.")
        return redirect(url_for("doctor"))

    conn.close()
    return render_template("followup.html", appointment=appointment)


if __name__ == "__main__":
    init_db()
    app.run(debug=True)
