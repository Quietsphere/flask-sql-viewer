import os
from flask import (
    Flask, render_template, request, session, redirect, url_for, flash
)
from sqlalchemy import create_engine, text
import pandas as pd
from datetime import datetime, date, timedelta
from dateutil.relativedelta import relativedelta
from werkzeug.security import generate_password_hash, check_password_hash
import secrets

app = Flask(__name__)
app.secret_key = os.environ.get("SECRET_KEY") or secrets.token_hex(16)

# --- SQL Server connection info ---
server = 'tankfluid-db.cxc6esakoxki.us-east-2.rds.amazonaws.com'
database = 'FluidData'
username = 'sqladmin'
password = 'LAS2025!'
driver = 'ODBC Driver 17 for SQL Server'

connection_string = f"mssql+pyodbc://{username}:{password}@{server}/{database}?driver={driver.replace(' ', '+')}"
engine = create_engine(connection_string)

def get_date_range(request_args):
    today = date.today()
    start_date = request_args.get("start_date")
    end_date = request_args.get("end_date")
    offset = request_args.get("month_offset", 0, type=int)

    if not start_date and not end_date:
        first_day = (today.replace(day=1) + relativedelta(months=offset))
        last_day = (first_day + relativedelta(months=1)) - relativedelta(days=1)
        return first_day, last_day

    start = datetime.strptime(start_date, "%Y-%m-%d").date() if start_date else today.replace(day=1)
    end = datetime.strptime(end_date, "%Y-%m-%d").date() if end_date else today
    return start, end

def get_data(query, params=None):
    df = pd.read_sql(text(query), engine, params=params)
    return list(df.columns), df.values.tolist()

def get_distinct_values(column):
    query = text(f"SELECT DISTINCT [{column}] FROM Transactions ORDER BY [{column}]")
    with engine.connect() as conn:
        result = conn.execute(query)
        rows = result.fetchall()
    return [str(row[0]) for row in rows if row[0] is not None]

def login_required(f):
    from functools import wraps
    @wraps(f)
    def decorated_function(*args, **kwargs):
        if "user_id" not in session:
            flash("Please log in to access this page.", "warning")
            return redirect(url_for("login"))
        return f(*args, **kwargs)
    return decorated_function

def admin_required(f):
    from functools import wraps
    @wraps(f)
    def decorated_function(*args, **kwargs):
        if not session.get("is_admin"):
            flash("You must be an admin to access this page.", "warning")
            return redirect(url_for("dashboard"))
        return f(*args, **kwargs)
    return decorated_function

@app.route("/")
@login_required
def dashboard():
    return render_template("index.html")

@app.route("/login", methods=["GET", "POST"])
def login():
    if request.method == "POST":
        email = request.form["email"].strip()
        password = request.form["password"]

        with engine.connect() as conn:
            user_row = conn.execute(
                text("SELECT UserID, Email, PasswordHash, IsAdmin FROM Users WHERE Email = :email"),
                {"email": email}
            ).fetchone()

        if user_row and check_password_hash(user_row.PasswordHash, password):
            session["user_id"] = user_row.UserID
            session["email"] = user_row.Email
            session["is_admin"] = user_row.IsAdmin
            flash("Logged in successfully!", "success")
            return redirect(url_for("dashboard"))
        else:
            flash("Invalid email or password.", "danger")

    return render_template("login.html")

@app.route("/logout")
@login_required
def logout():
    session.clear()
    flash("You have been logged out.", "info")
    return redirect(url_for("login"))

@app.route("/transactions")
@login_required
def transactions():
    try:
        month_offset = int(request.args.get("month_offset", 0))
    except ValueError:
        month_offset = 0

    start_str = request.args.get("start_date")
    end_str = request.args.get("end_date")

    if start_str and end_str:
        selected_start = datetime.strptime(start_str, "%Y-%m-%d")
        selected_end = datetime.strptime(end_str, "%Y-%m-%d") + timedelta(days=1) - timedelta(seconds=1)
    else:
        today = datetime.today()
        selected_start = datetime(today.year, today.month, 1) + relativedelta(months=month_offset)
        selected_end = selected_start + relativedelta(months=1) - timedelta(seconds=1)

    base_query = "FROM Transactions WHERE [TransactionEndTime] BETWEEN :start AND :end"
    params = {"start": selected_start, "end": selected_end}
    query_parts = []

    def add_multiselect_filter(column_name, param_name=None):
        values = request.args.getlist(param_name or column_name)
        if values:
            placeholders = [f":{column_name}_{i}" for i in range(len(values))]
            query_parts.append(f"[{column_name}] IN ({', '.join(placeholders)})")
            for i, v in enumerate(values):
                params[f"{column_name}_{i}"] = v

    for column in ["StationID", "TransactionType", "DriverName", "CompanyName", "TruckID", "Product", "TankID"]:
        add_multiselect_filter(column)

    filter_clause = " AND " + " AND ".join(query_parts) if query_parts else ""

    data_query = f"SELECT * {base_query}{filter_clause} ORDER BY [TransactionEndTime] DESC"
    headers, rows = get_data(data_query, params)

    sum_query = f"SELECT SUM(VolumeDelivered) as TotalVolume {base_query}{filter_clause}"
    with engine.connect() as conn:
        total_volume = conn.execute(text(sum_query), params).scalar() or 0

    total_volume = round(total_volume, 2)
    date_range = f"{selected_start.strftime('%b %d, %Y')} – {selected_end.strftime('%b %d, %Y')}"

    filters = {col: get_distinct_values(col) for col in ["StationID", "TransactionType", "DriverName", "CompanyName", "TruckID", "Product", "TankID"]}

    return render_template("transactions.html", headers=headers, rows=rows, start=selected_start.date(),
                           end=selected_end.date(), date_range=date_range, month_offset=month_offset,
                           filters=filters, total_volume=total_volume)

@app.route("/tanklevels")
@login_required
def tanklevels():
    month_offset = int(request.args.get('month_offset', 0))
    today = datetime.today()
    selected_start = datetime(today.year, today.month, 1) + relativedelta(months=month_offset)
    selected_end = selected_start + relativedelta(months=1) - timedelta(seconds=1)
    start, end = selected_start.strftime("%Y-%m-%d %H:%M:%S"), selected_end.strftime("%Y-%m-%d %H:%M:%S")
    tank = request.args.get("tank")

    query = "SELECT * FROM TankLevels WHERE [ReadingTimestamp] BETWEEN :start AND :end"
    params = {"start": start, "end": end}
    if tank:
        query += " AND TankName LIKE :tank"
        params["tank"] = f"%{tank}%"
    query += " ORDER BY [ReadingTimestamp] DESC"

    headers, rows = get_data(query, params)
    date_range_str = f"{selected_start.strftime('%b %d, %Y')} - {selected_end.strftime('%b %d, %Y')}"
    return render_template("tanklevels.html", headers=headers, rows=rows,
                           month_offset=month_offset, date_range=date_range_str)

@app.route("/admin")
@admin_required
def admin_panel():
    with engine.connect() as conn:
        users = conn.execute(text("SELECT UserID, Email, IsAdmin FROM Users ORDER BY Email")).fetchall()
        sites = conn.execute(text("SELECT SiteID, SiteName FROM Sites ORDER BY SiteName")).fetchall()
        assignments = conn.execute(text("""
            SELECT u.Email, s.SiteName
            FROM UserSiteAccess usa
            JOIN Users u ON usa.UserID = u.UserID
            JOIN Sites s ON usa.SiteID = s.SiteID
            ORDER BY u.Email, s.SiteName
        """)).fetchall()
    return render_template("admin.html", users=users, sites=sites, assignments=assignments)

@app.route("/reset_password_request", methods=["GET", "POST"])
def reset_password_request():
    if request.method == "POST":
        email = request.form["email"].strip()
        with engine.connect() as conn:
            user = conn.execute(text("SELECT UserID FROM Users WHERE Email = :email"), {"email": email}).fetchone()
            if user:
                token = secrets.token_urlsafe(32)
                expiration = datetime.utcnow() + timedelta(hours=1)
                conn.execute(text("""
                    UPDATE Users SET ResetToken = :token, TokenExpiration = :expiration
                    WHERE Email = :email
                """), {"token": token, "expiration": expiration, "email": email})
                flash(f"Reset link: {url_for('reset_password', token=token, _external=True)}", "info")
            else:
                flash("Email address not found.", "warning")
        return redirect(url_for("login"))
    return render_template("reset_password_request.html")

@app.route("/reset_password/<token>", methods=["GET", "POST"])
def reset_password(token):
    with engine.connect() as conn:
        user = conn.execute(text("""
            SELECT UserID, TokenExpiration FROM Users WHERE ResetToken = :token
        """), {"token": token}).fetchone()

    if not user or user.TokenExpiration < datetime.utcnow():
        flash("Invalid or expired token.", "danger")
        return redirect(url_for("login"))

    if request.method == "POST":
        password = request.form["password"]
        confirm = request.form["confirm_password"]
        if password != confirm:
            flash("Passwords do not match.", "danger")
            return render_template("reset_password.html", token=token)

        password_hash = generate_password_hash(password)
        with engine.connect() as conn:
            conn.execute(text("""
                UPDATE Users SET PasswordHash = :password, ResetToken = NULL, TokenExpiration = NULL
                WHERE ResetToken = :token
            """), {"password": password_hash, "token": token})

        flash("Password has been reset. Please log in.", "success")
        return redirect(url_for("login"))

    return render_template("reset_password.html", token=token)

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port)
