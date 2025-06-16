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

def get_user_site_access(user_id):
    with engine.connect() as conn:
        rows = conn.execute(text("""
            SELECT usa.SiteID, s.SiteName, usa.HasFullAccess
            FROM UserSiteAccess usa
            JOIN Sites s ON usa.SiteID = s.SiteID
            WHERE usa.UserID = :user_id
        """), {"user_id": user_id}).fetchall()
    return rows

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
def sync_assets_from_data():
    with engine.begin() as conn:
        existing_names = {
            row[0] for row in conn.execute(text("SELECT AssetName FROM Assets")).fetchall()
        }

        # Sync TankIDs
        tanks = conn.execute(text("SELECT DISTINCT TankID FROM TankLevels WHERE TankID IS NOT NULL")).fetchall()
        for row in tanks:
            name = row.TankID
            if name and name not in existing_names:
                conn.execute(text("""
                    INSERT INTO Assets (AssetName, AssetType, Description)
                    VALUES (:name, 'Tank', 'Auto-imported from TankLevels')
                """), {"name": name})
                existing_names.add(name)

        # Sync StationIDs
        stations = conn.execute(text("SELECT DISTINCT StationID FROM Transactions WHERE StationID IS NOT NULL")).fetchall()
        for row in stations:
            name = row.StationID
            if name and name not in existing_names:
                conn.execute(text("""
                    INSERT INTO Assets (AssetName, AssetType, Description)
                    VALUES (:name, 'Station', 'Auto-imported from Transactions')
                """), {"name": name})
                existing_names.add(name)


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
    try:
        month_offset = int(request.args.get('month_offset', 0))
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
                           start=selected_start.date(), end=selected_end.date(),
                           month_offset=month_offset, date_range=date_range_str)


@app.route("/admin", methods=["GET", "POST"])
@admin_required
def admin_panel():
    print("Form data:", request.form)

    message_handled = False

    # --- Create new user ---
    if "new_user_email" in request.form:
        email = request.form["new_user_email"].strip().lower()
        password = request.form["new_user_password"]
        is_admin = bool(request.form.get("new_user_is_admin"))

        with engine.connect() as conn:
            existing = conn.execute(
                text("SELECT 1 FROM Users WHERE LOWER(Email) = :email"),
                {"email": email}
            ).fetchone()

        if existing:
            flash("A user with that email already exists.", "warning")
        else:
            password_hash = generate_password_hash(password)
            try:
                with engine.begin() as conn:
                    conn.execute(text("""
                        INSERT INTO Users (Email, PasswordHash, IsAdmin)
                        VALUES (:email, :password_hash, :is_admin)
                    """), {
                        "email": email,
                        "password_hash": password_hash,
                        "is_admin": int(is_admin)
                    })
                flash("User created successfully.", "success")
            except Exception as e:
                flash(f"Error creating user: {e}", "danger")
        message_handled = True

    # --- Create new site ---
    elif request.form.get("form_type") == "create_site":
        site_name = request.form.get("new_site_name", "").strip()
        if site_name:
            try:
                with engine.begin() as conn:
                    conn.execute(text("""
                        INSERT INTO Sites (SiteName) VALUES (:site_name)
                    """), {"site_name": site_name})
                flash("Site created successfully.", "success")
            except Exception as e:
                flash(f"Error creating site: {e}", "danger")
        else:
            flash("Site name cannot be empty.", "warning")
        return redirect(url_for("admin_sites"))  # <-- this is required



    # --- Edit site ---
    elif "edit_site_id" in request.form:
        site_id = request.form["edit_site_id"]
        site_name = request.form["edit_site_name"].strip()
        try:
            with engine.begin() as conn:
                conn.execute(text("""
                    UPDATE Sites SET SiteName = :site_name WHERE SiteID = :site_id
                """), {"site_name": site_name, "site_id": site_id})
            flash("Site updated successfully.", "success")
        except Exception as e:
            flash(f"Error updating site: {e}", "danger")
        message_handled = True

    # --- Delete site ---
    elif "delete_site_id" in request.form:
        site_id = request.form["delete_site_id"]
        try:
            with engine.begin() as conn:
                conn.execute(text("DELETE FROM Sites WHERE SiteID = :site_id"), {"site_id": site_id})
            flash("Site deleted.", "info")
        except Exception as e:
            flash(f"Error deleting site: {e}", "danger")
        message_handled = True

    # --- Fetch data for display ---
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

    ret# Add to app.py

# Add to app.py

@app.route("/admin/users", methods=["GET", "POST"])
@admin_required
def admin_users():
    if request.method == "POST":
        form_type = request.form.get("form_type")

        if form_type == "create_user":
            user_name = request.form.get("user_name", "").strip()
            email = request.form["email"].strip().lower()
            password = request.form["password"]
            is_admin = bool(request.form.get("is_admin"))

            with engine.connect() as conn:
                existing = conn.execute(
                    text("SELECT 1 FROM Users WHERE LOWER(Email) = :email"),
                    {"email": email}
                ).fetchone()

            if existing:
                flash("A user with that email already exists.", "warning")
            else:
                password_hash = generate_password_hash(password)
                with engine.begin() as conn:
                    conn.execute(text("""
                        INSERT INTO Users (UserName, Email, PasswordHash, IsAdmin)
                        VALUES (:user_name, :email, :password_hash, :is_admin)
                    """), {
                        "user_name": user_name,
                        "email": email,
                        "password_hash": password_hash,
                        "is_admin": int(is_admin)
                    })
                flash("User created successfully.", "success")

        elif form_type == "edit_user":
            user_id = request.form["user_id"]
            email = request.form["email"].strip().lower()
            is_admin = bool(request.form.get("is_admin"))
            user_name = request.form.get("user_name", "").strip()

            # Save user info
            with engine.begin() as conn:
                conn.execute(text("""
                    UPDATE Users
                    SET Email = :email, IsAdmin = :is_admin, UserName =:user_name
                    WHERE UserID = :user_id
                """), {
                    "email": email,
                    "is_admin": int(is_admin),
                    "user_name": user_name,
                    "user_id": user_id
                })

                # Remove old site assignments
                conn.execute(text("DELETE FROM UserSiteAccess WHERE UserID = :user_id"), {"user_id": user_id})

                # Insert new assignments
                site_ids = request.form.getlist("site_ids")
                for site_id in site_ids:
                    full_access = request.form.get(f"full_access_{site_id}") == "1"
                    conn.execute(text("""
                        INSERT INTO UserSiteAccess (UserID, SiteID, HasFullAccess)
                        VALUES (:user_id, :site_id, :has_full_access)
                    """), {
                        "user_id": user_id,
                        "site_id": site_id,
                        "has_full_access": int(full_access)
                    })

            flash("User and site access updated.", "success")


        elif form_type == "delete_user":
            user_id = request.form["user_id"]
            with engine.begin() as conn:
                conn.execute(text("DELETE FROM Users WHERE UserID = :user_id"), {"user_id": user_id})
            flash("User deleted.", "info")

        elif form_type == "assign_site":
            user_id = request.form["user_id"]
            site_id = request.form["site_id"]
            has_full_access = bool(request.form.get("has_full_access"))

            with engine.begin() as conn:
                existing = conn.execute(text("""
                    SELECT 1 FROM UserSiteAccess
                    WHERE UserID = :user_id AND SiteID = :site_id
                """), {
                    "user_id": user_id,
                    "site_id": site_id
                }).fetchone()

                if existing:
                    flash("This user already has access to the selected site.", "warning")
                else:
                    conn.execute(text("""
                        INSERT INTO UserSiteAccess (UserID, SiteID, HasFullAccess)
                        VALUES (:user_id, :site_id, :has_full_access)
                    """), {
                        "user_id": user_id,
                        "site_id": site_id,
                        "has_full_access": int(has_full_access)
                    })
                    flash("Site access assigned successfully.", "success")

    with engine.connect() as conn:
        users = conn.execute(text("SELECT UserID, Email, UserName, IsAdmin FROM Users ORDER BY UserName")).fetchall()
        assignments = conn.execute(text("""
            SELECT usa.UserID, u.Email, s.SiteName, usa.SiteID, usa.HasFullAccess, u.UserName
            FROM UserSiteAccess usa
            JOIN Users u ON usa.UserID = u.UserID
            JOIN Sites s ON usa.SiteID = s.SiteID
            ORDER BY u.UserName, s.SiteName
        """)).fetchall()
        sites = conn.execute(text("SELECT SiteID, SiteName FROM Sites ORDER BY SiteName")).fetchall()

    return render_template("admin_users.html", users=users, assignments=assignments, sites=sites)



@app.route("/admin/sites", methods=["GET", "POST"])
@admin_required
def admin_sites():
    sync_assets_from_data()  # Ensure assets are synced from source tables

    if request.method == "POST":
        form_type = request.form.get("form_type")

        if form_type == "create_site":
            site_name = request.form.get("site_name", "").strip()
            if site_name:
                try:
                    with engine.begin() as conn:
                        conn.execute(text("""
                            INSERT INTO Sites (SiteName) VALUES (:site_name)
                        """), {"site_name": site_name})
                    flash("Site created successfully.", "success")
                except Exception as e:
                    flash(f"Error creating site: {e}", "danger")
            else:
                flash("Site name cannot be empty.", "warning")
            return redirect(url_for("admin_sites"))

        elif form_type == "edit_site":
            site_id = request.form.get("edit_site_id")
            site_name = request.form.get("edit_site_name", "").strip()
            try:
                with engine.begin() as conn:
                    conn.execute(text("""
                        UPDATE Sites SET SiteName = :site_name WHERE SiteID = :site_id
                    """), {"site_name": site_name, "site_id": site_id})
                flash("Site updated successfully.", "success")
            except Exception as e:
                flash(f"Error updating site: {e}", "danger")
            return redirect(url_for("admin_sites"))

        elif form_type == "delete_site":
            site_id = request.form.get("delete_site_id")
            try:
                with engine.begin() as conn:
                    conn.execute(text("DELETE FROM Sites WHERE SiteID = :site_id"), {"site_id": site_id})
                flash("Site deleted.", "info")
            except Exception as e:
                flash(f"Error deleting site: {e}", "danger")
            return redirect(url_for("admin_sites"))

        elif form_type == "assign_site_asset":
            asset_id = request.form["asset_id"]
            site_id = request.form["site_id"]
            with engine.begin() as conn:
                conn.execute(text("""
                    UPDATE Assets SET SiteID = :site_id WHERE AssetID = :asset_id
                """), {"site_id": site_id, "asset_id": asset_id})
            flash("Asset assigned to site.", "success")
            return redirect(url_for("admin_sites"))

        elif form_type == "update_asset_details":
            asset_id = request.form["asset_id"]
            local_name = request.form.get("local_name", "").strip()
            asset_type = request.form.get("asset_type", "").strip()
            description = request.form.get("description", "").strip()
            site_id = request.form.get("site_id") or None

            with engine.begin() as conn:
                conn.execute(text("""
                    UPDATE Assets
                    SET LocalName = :local_name,
                        AssetType = :asset_type,
                        Description = :description,
                        SiteID = :site_id
                    WHERE AssetID = :asset_id
                """), {
                    "local_name": local_name,
                    "asset_type": asset_type,
                    "description": description,
                    "site_id": site_id,
                    "asset_id": asset_id
                })
            flash("Asset updated successfully.", "success")
            return redirect(url_for("admin_sites"))

    # On GET or after POST actions, fetch updated data
    with engine.connect() as conn:
        sites = conn.execute(text("SELECT SiteID, SiteName FROM Sites ORDER BY SiteName")).fetchall()
        assets = conn.execute(text("""
            SELECT a.AssetID, a.AssetName, a.LocalName, a.AssetType, a.Description, a.SiteID, s.SiteName
            FROM Assets a
            LEFT JOIN Sites s ON a.SiteID = s.SiteID
            ORDER BY a.AssetName
        """)).fetchall()

    return render_template("admin_sites.html", sites=sites, assets=assets)



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
