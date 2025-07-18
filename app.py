import os

# Only load .env when running locally (not on Render or most cloud platforms)
if os.environ.get("RENDER") is None:
    try:
        from dotenv import load_dotenv
        load_dotenv()
    except ImportError:
        pass  # Don't crash if dotenv isn't available

from flask import (
    Flask, render_template, request, session, redirect, url_for, flash
)
from sqlalchemy import create_engine, text, bindparam
import pandas as pd
from datetime import datetime, date, timedelta
from zoneinfo import ZoneInfo
from dateutil.relativedelta import relativedelta
from werkzeug.security import generate_password_hash, check_password_hash
import secrets

app = Flask(__name__)
app.secret_key = os.environ.get("SECRET_KEY") or secrets.token_hex(16)

db_env = os.environ.get('DB_ENV', 'aws')  # Default to AWS
if db_env == 'azure':
    connection_string = os.environ.get('DATABASE_URL_AZURE')
else:
    connection_string = os.environ.get('DATABASE_URL_AWS')

if not connection_string:
    raise RuntimeError("Database connection string is not set!")
engine = create_engine(connection_string)

def get_user_site_access(user_id):
    with engine.connect() as conn:
        result = conn.execute(
            text("""
                SELECT Sites.SiteID AS id, Sites.SiteName AS name, USA.HasFullAccess, COALESCE(USA.DaysToEmptySpan, 7) AS span_days
                FROM UserSiteAccess USA
                JOIN Sites ON Sites.SiteID = USA.SiteID
                WHERE USA.UserID = :user_id
            """),
            {"user_id": user_id}
        )
        return [dict(row._mapping) for row in result]

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
    return list(df.columns), df.to_dict(orient="records")  # ✅ return list of dicts


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


def get_product_colors():
    with engine.connect() as conn:
        result = conn.execute(text("SELECT ProductName, ColorHex FROM ProductColors"))
        return {row.ProductName: row.ColorHex for row in result}

def get_site_products(site_id, span_days=7):
    query = """
    SELECT Product, CurrentInventory, PreviousInventory, Movement, LastReading, PreviousReading
    FROM v_ProductInventoryAndMovement
    WHERE SiteID = :site_id
    """
    with engine.connect() as conn:
        result = conn.execute(text(query), {"site_id": site_id})
        return [
            {
                "name": row.Product,
                "current_inventory": row.CurrentInventory,
                "previous_inventory": row.PreviousInventory,
                "inventory_delta": row.Movement,
                "last_reading": (
                    row.LastReading.strftime('%Y-%m-%d %H:%M')
                    if hasattr(row.LastReading, "strftime") else str(row.LastReading)
                ) if row.LastReading else "unknown",
                "previous_reading": (
                    row.PreviousReading.strftime('%Y-%m-%d %H:%M')
                    if hasattr(row.PreviousReading, "strftime") else str(row.PreviousReading)
                ) if row.PreviousReading else "unknown"
            }
            for row in result
        ]


@app.route('/', methods=['GET', 'POST'])
@login_required
def index():
    user_id = session["user_id"]
    is_admin = session.get("is_admin", False)

    # Regular users: handle span setting updates (POST)
    if not is_admin and request.method == "POST":
       pass  # TODO: Handle updates to DaysToEmptySpan

    # Get sites for sidebar, etc.
    user_sites = get_user_site_access(user_id)
    if not user_sites:
        if is_admin:
            # Admin: load all sites with a fixed span_days = 7
            with engine.connect() as conn:
                result = conn.execute(text("SELECT SiteID, SiteName FROM Sites"))
                user_sites = [{"id": row.SiteID, "name": row.SiteName, "span_days": 7} for row in result]
        else:
            flash("You do not have any sites assigned yet. Please contact your administrator.", "warning")
            return redirect(url_for("logout"))

    # Figure out the current site and the span setting
    site_id = request.args.get("site_id")
    site_ids = {str(s["id"]) for s in user_sites}
    if not site_id or site_id not in site_ids:
        site_id = str(user_sites[0]["id"])
    current_site = next(s for s in user_sites if str(s["id"]) == str(site_id))

    # --- KEY: Only regular users can set their own span, admins are fixed at 7
    if is_admin:
        span_days = 7
    else:
        span_days = current_site.get('span_days', 7)

    products = get_site_products(site_id, span_days=span_days)
    product_colors = get_product_colors()

    return render_template(
        "index.html",
        user_sites=user_sites,
        current_site=current_site,
        products=products,
        product_colors=product_colors,
        span_days=span_days,
        is_admin=is_admin
    )

@app.route("/login", methods=["GET", "POST"])
def login():
    if request.method == "POST":
        email = request.form["email"].strip()
        password = request.form["password"]

        with engine.connect() as conn:
            user_row = conn.execute(
                text("SELECT UserID, Email, PasswordHash, IsAdmin, CompanyName FROM Users WHERE Email = :email"),
                {"email": email}
            ).fetchone()

        if user_row and check_password_hash(user_row.PasswordHash, password):
            session["user_id"] = user_row.UserID
            session["email"] = user_row.Email
            session["is_admin"] = user_row.IsAdmin
            session["company_name"] = user_row.CompanyName or ""
            flash("Logged in successfully!", "success")

            central_time = datetime.now(ZoneInfo("America/Chicago"))

            with engine.begin() as conn:
                conn.execute(text("""
                    UPDATE Users SET LastLogin = :login_time WHERE UserID = :user_id
                """), {
                    "login_time": central_time,
                    "user_id": user_row.UserID
                })

            return redirect(url_for("index"))
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

    params = {"start": selected_start, "end": selected_end}
    query_parts = []

    user_id = session["user_id"]
    company_name = session.get("company_name")
    access = get_user_site_access(user_id)

    full_site_ids = [a['id'] for a in access if a.get('HasFullAccess')]
    limited_site_ids = [a['id'] for a in access if not a.get('HasFullAccess')]


    with engine.connect() as conn:
        full_station_ids = []
        limited_station_ids = []

        if full_site_ids:
            result = conn.execute(
                text("SELECT AssetName FROM Assets WHERE SiteID IN :site_ids AND AssetType = 'Station'")
                .bindparams(bindparam("site_ids", expanding=True)),
                {"site_ids": full_site_ids}
            )
            full_station_ids = [row[0] for row in result.fetchall()]

        if limited_site_ids and company_name:
            result = conn.execute(
                text("SELECT AssetName FROM Assets WHERE SiteID IN :site_ids AND AssetType = 'Station'")
                .bindparams(bindparam("site_ids", expanding=True)),
                {"site_ids": limited_site_ids}
            )
            limited_station_ids = [row[0] for row in result.fetchall()]

    station_filter_clauses = []
    if full_station_ids:
        placeholders = [f":fs_{i}" for i in range(len(full_station_ids))]
        station_filter_clauses.append(f"t.StationID IN ({', '.join(placeholders)})")
        for i, sid in enumerate(full_station_ids):
            params[f"fs_{i}"] = sid

    if limited_station_ids:
        placeholders = [f":ls_{i}" for i in range(len(limited_station_ids))]
        clause = f"(t.StationID IN ({', '.join(placeholders)}) AND t.CompanyName = :company_name)"
        station_filter_clauses.append(clause)
        for i, sid in enumerate(limited_station_ids):
            params[f"ls_{i}"] = sid
        params["company_name"] = company_name

    if station_filter_clauses:
        query_parts.append("(" + " OR ".join(station_filter_clauses) + ")")

    def add_multiselect_filter(column_name):
        values = request.args.getlist(column_name)
        if values:
            placeholders = [f":{column_name}_{i}" for i in range(len(values))]
            query_parts.append(f"t.[{column_name}] IN ({', '.join(placeholders)})")
            for i, v in enumerate(values):
                params[f"{column_name}_{i}"] = v

    for col in ["TransactionType", "DriverName", "CompanyName", "TruckID", "Product"]:
        add_multiselect_filter(col)

    # Custom handling for Station and Site filters
    station_values = request.args.getlist("Station")
    if station_values:
        placeholders = [f":station_{i}" for i in range(len(station_values))]
        query_parts.append(f"a.LocalName IN ({', '.join(placeholders)})")
        for i, val in enumerate(station_values):
            params[f"station_{i}"] = val

    site_values = request.args.getlist("Site")
    if site_values:
        placeholders = [f":site_{i}" for i in range(len(site_values))]
        query_parts.append(f"s.SiteName IN ({', '.join(placeholders)})")
        for i, val in enumerate(site_values):
            params[f"site_{i}"] = val

    filter_clause = " AND " + " AND ".join(query_parts) if query_parts else ""

    base_query = f"""
        FROM Transactions t
        JOIN Assets a ON t.StationID = a.AssetName
        JOIN Sites s ON a.SiteID = s.SiteID
        WHERE t.[TransactionEndTime] BETWEEN :start AND :end
        {filter_clause}
    """

    data_query = f"""
        SELECT 
            t.ID AS TransactionID,
            a.LocalName AS Station,
            s.SiteName AS Site,
            t.TransactionType,
            t.VolumeDelivered,
            t.TransactionStartTime,
            t.TransactionEndTime,
            t.DriverName,
            t.CompanyName,
            t.TruckID,
            t.Product
        {base_query}
        ORDER BY t.[TransactionEndTime] DESC
    """

    headers, rows = get_data(data_query, params)

    sum_query = f"SELECT SUM(t.VolumeDelivered) as TotalVolume {base_query}"
    with engine.connect() as conn:
        total_volume = conn.execute(text(sum_query), params).scalar() or 0

    total_volume = round(total_volume, 2)
    date_range = f"{selected_start.strftime('%b %d, %Y')} - {selected_end.strftime('%b %d, %Y')}"

    filters = {}
    for col in ["TransactionType", "DriverName", "CompanyName", "TruckID", "Product"]:
        filters[col] = get_distinct_values(col)

    with engine.connect() as conn:
        station_rows = conn.execute(text("""
            SELECT DISTINCT a.LocalName
            FROM Transactions t
            JOIN Assets a ON t.StationID = a.AssetName
            WHERE a.AssetType = 'Station'
            ORDER BY a.LocalName
        """)).fetchall()
        filters["Station"] = [row[0] for row in station_rows]

        site_rows = conn.execute(text("""
            SELECT DISTINCT s.SiteName
            FROM Transactions t
            JOIN Assets a ON t.StationID = a.AssetName
            JOIN Sites s ON a.SiteID = s.SiteID
            ORDER BY s.SiteName
        """)).fetchall()
        filters["Site"] = [row[0] for row in site_rows]

    return render_template("transactions.html", headers=headers, rows=rows,
                           start=selected_start.date(), end=selected_end.date(),
                           date_range=date_range, month_offset=month_offset,
                           filters=filters, total_volume=total_volume)




@app.route("/tanklevels")
@login_required
def tanklevels():
    from datetime import datetime, timedelta
    from dateutil.relativedelta import relativedelta

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
    tank_filter = request.args.get("tank")
    params = {"start": start, "end": end}
    where_clauses = ["[ReadingTimestamp] BETWEEN :start AND :end"]

    # --- Access Filtering ---
    user_id = session["user_id"]
    is_admin = session.get("is_admin", False)
    user_company = session.get("company_name")
    visible_tank_ids = []

    if not is_admin:
        access = get_user_site_access(user_id)
        # Adapt for your access object shape
        full_sites = [a.SiteID if hasattr(a, "SiteID") else a["id"] for a in access if (a.HasFullAccess if hasattr(a, "HasFullAccess") else a.get("HasFullAccess"))]
        limited_sites = [a.SiteID if hasattr(a, "SiteID") else a["id"] for a in access if not (a.HasFullAccess if hasattr(a, "HasFullAccess") else a.get("HasFullAccess"))]

        assigned_site_ids = set(full_sites + limited_sites)

        if assigned_site_ids:
            with engine.connect() as conn:
                result = conn.execute(
                    text("""
                        SELECT AssetName, SiteID, CompanyName
                        FROM Assets
                        WHERE AssetType = 'Tank'
                    """)
                )
                for row in result:
                    site_id = row.SiteID
                    tank_company = row.CompanyName
                    asset_name = row.AssetName

                    if site_id in full_sites:
                        visible_tank_ids.append(asset_name)
                    elif site_id in limited_sites and tank_company and tank_company == user_company:
                        visible_tank_ids.append(asset_name)

        if visible_tank_ids:
            placeholders = [f":tank_{i}" for i in range(len(visible_tank_ids))]
            where_clauses.append(f"TankID IN ({', '.join(placeholders)})")
            for i, tank_id in enumerate(visible_tank_ids):
                params[f"tank_{i}"] = tank_id
        else:
            return render_template("tanklevels.html", headers=[], rows=[],
                                   start=selected_start.date(), end=selected_end.date(),
                                   month_offset=month_offset,
                                   date_range=f"{selected_start:%b %d, %Y} - {selected_end:%b %d, %Y}",
                                   chart_data=[], chart_trends=[])

    if tank_filter:
        where_clauses.append("PreferredTankName LIKE :tank")
        params["tank"] = f"%{tank_filter}%"

    # --- Main Table Query ---
    query = f"""
        SELECT *
        FROM vw_TankLevelsWithName
        WHERE {' AND '.join(where_clauses)}
        ORDER BY [ReadingTimestamp] DESC
    """
    headers, rows = get_data(query, params)

    unique_sites = sorted(set(row['SiteName'] for row in rows if row.get('SiteName')))
    tank_names = sorted(set(row['PreferredTankName'] for row in rows if row.get('PreferredTankName')))

    # --- Asset Info ---
    with engine.connect() as conn:
        asset_info = {
            row.AssetName: {
                "LocalName": row.LocalName
            }
            for row in conn.execute(text("SELECT AssetName, LocalName FROM Assets WHERE AssetType = 'Tank'"))
        }

    # --- Bar Chart Query (Most Recent Readings) ---
    access_clauses = [clause for clause in where_clauses if not clause.startswith("[ReadingTimestamp]")]
    access_filter = f" AND {' AND '.join(access_clauses)}" if access_clauses else ""

    bar_query = f"""
        SELECT
            PreferredTankName,
            SiteName,
            Volume,
            ReadingTimestamp,
            Capacity,
            Product
        FROM vw_TankLevelsWithName
        WHERE [ReadingTimestamp] = (
            SELECT MAX(t2.ReadingTimestamp)
            FROM vw_TankLevelsWithName t2
            WHERE t2.TankID = vw_TankLevelsWithName.TankID
        )
        {access_filter}
    """
    with engine.connect() as conn:
        color_map = dict(conn.execute(text("SELECT ProductName, ColorHex FROM ProductColors")).fetchall())

    bar_data = []
    with engine.connect() as conn:
        bar_rows = conn.execute(text(bar_query), params).fetchall()
        for row in bar_rows:
            product = getattr(row, "Product", None) or ""
            bar_data.append({
                "PreferredTankName": row.PreferredTankName,
                "SiteName": row.SiteName or "Unassigned",
                "Volume": float(row.Volume),
                "Capacity": float(row.Capacity) if row.Capacity is not None else None,
                "Timestamp": row.ReadingTimestamp.isoformat() if row.ReadingTimestamp else "",
                "Product": product,
                "ColorHex": color_map.get(product, "#3692eb")
            })

    # --- Line Chart Trend Data ---
    trend_data = []
    for row in rows:
        label = row.get("PreferredTankName")
        timestamp = row.get("ReadingTimestamp")
        volume = row.get("Volume")

        if label and timestamp and volume is not None:
            trend_data.append({
                "PreferredTankName": label,
                "Timestamp": timestamp.isoformat(),
                "Volume": float(volume)
            })
    trend_data.sort(key=lambda p: (p["PreferredTankName"], p["Timestamp"]))

    return render_template("tanklevels.html",
                           headers=headers,
                           rows=rows,
                           start=selected_start.date(),
                           end=selected_end.date(),
                           month_offset=month_offset,
                           date_range=f"{selected_start:%b %d, %Y} - {selected_end:%b %d, %Y}",
                           chart_data=bar_data or [],
                           chart_trends=trend_data or [],
                           unique_sites=unique_sites,
                           tank_names=tank_names)


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
            company_name = request.form.get("company_name", "").strip() or None

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
                        INSERT INTO Users (UserName, Email, PasswordHash, IsAdmin, CompanyName)
                        VALUES (:user_name, :email, :password_hash, :is_admin, :company_name)
                    """), {
                        "user_name": user_name,
                        "email": email,
                        "password_hash": password_hash,
                        "is_admin": int(is_admin),
                        "company_name": company_name
                    })
                flash("User created successfully.", "success")

        elif form_type == "edit_user":
            user_id = request.form["user_id"]
            email = request.form["email"].strip().lower()
            is_admin = bool(request.form.get("is_admin"))
            user_name = request.form.get("user_name", "").strip()
            company_name = request.form.get("company_name", "").strip() or None

            # Save user info
            with engine.begin() as conn:
                conn.execute(text("""
                    UPDATE Users
                    SET Email = :email, IsAdmin = :is_admin, UserName = :user_name, CompanyName = :company_name
                    WHERE UserID = :user_id
                """), {
                    "email": email,
                    "is_admin": int(is_admin),
                    "user_name": user_name,
                    "user_id": user_id,
                    "company_name": company_name
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
        users = conn.execute(text("SELECT UserID, Email, UserName, IsAdmin, CompanyName, LastLogin FROM Users ORDER BY UserName")).fetchall()
        assignments = conn.execute(text("""
            SELECT usa.UserID, u.Email, s.SiteName, usa.SiteID, usa.HasFullAccess, u.UserName
            FROM UserSiteAccess usa
            JOIN Users u ON usa.UserID = u.UserID
            JOIN Sites s ON usa.SiteID = s.SiteID
            ORDER BY u.UserName, s.SiteName
        """)).fetchall()

        company_rows = conn.execute(text("""
            SELECT DISTINCT CompanyName FROM Users WHERE CompanyName IS NOT NULL
            UNION
            SELECT DISTINCT CompanyName FROM Transactions WHERE CompanyName IS NOT NULL
        """)).fetchall()
        company_names = sorted(set(r[0] for r in company_rows if r[0]))

        sites = conn.execute(text("SELECT SiteID, SiteName FROM Sites ORDER BY SiteName")).fetchall()

    return render_template("admin_users.html", users=users, assignments=assignments, sites=sites, company_names=company_names)

@app.route('/admin/products', methods=['GET', 'POST'])
@admin_required
def admin_products():
    if request.method == 'POST':
        form_type = request.form.get('form_type')
        product_name = request.form.get('product_name')
        
        if form_type == 'update_product_color':
            color_hex = request.form.get(f'color_hex_{product_name}')
            with engine.begin() as conn:
                conn.execute(text("""
                    MERGE ProductColors AS pc
                    USING (SELECT :product_name AS ProductName) AS vals
                    ON pc.ProductName = vals.ProductName
                    WHEN MATCHED THEN
                        UPDATE SET ColorHex = :color_hex
                    WHEN NOT MATCHED THEN
                        INSERT (ProductName, ColorHex) VALUES (:product_name, :color_hex);
                """), {"product_name": product_name, "color_hex": color_hex})
            flash(f"Color for '{product_name}' updated!", "success")
            return redirect(url_for('admin_products'))

        elif form_type == 'delete_product_color':
            with engine.begin() as conn:
                conn.execute(
                    text("DELETE FROM ProductColors WHERE ProductName = :product_name"),
                    {"product_name": product_name}
                )
            flash(f"Color for '{product_name}' removed.", "info")
            return redirect(url_for('admin_products'))

    with engine.connect() as conn:
        colored_products = conn.execute(text("""
            SELECT ProductName, ColorHex FROM ProductColors ORDER BY ProductName
        """)).fetchall()
        missing_products = conn.execute(text("""
            SELECT ProductName FROM vw_ProductsMissingColor ORDER BY ProductName
        """)).fetchall()

    return render_template(
        'admin_products.html',
        colored_products=colored_products,
        missing_products=missing_products
    )



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
            company_name = request.form.get("company_name", "").strip() or None
            capacity_str = request.form.get("capacity", "").strip()
            capacity = float(capacity_str) if capacity_str else None  # Convert if not blank

            with engine.begin() as conn:
                conn.execute(text("""
                    UPDATE Assets
                    SET LocalName = :local_name,
                        AssetType = :asset_type,
                        Description = :description,
                        SiteID = :site_id,
                        CompanyName = :company_name,
                        Capacity = :capacity
                    WHERE AssetID = :asset_id
                """), {
                    "local_name": local_name,
                    "asset_type": asset_type,
                    "description": description,
                    "site_id": site_id,
                    "company_name": company_name,
                    "capacity": capacity,
                    "asset_id": asset_id
                })
            flash("Asset updated successfully.", "success")
            return redirect(url_for("admin_sites"))

# Fetch distinct companies from Users and Transactions
    with engine.connect() as conn:
        sites = conn.execute(text("SELECT SiteID, SiteName FROM Sites ORDER BY SiteName")).fetchall()
        assets = conn.execute(text("""
            SELECT a.AssetID, a.AssetName, a.LocalName, a.AssetType, a.Description, a.SiteID, s.SiteName, a.CompanyName, a.Capacity
            FROM Assets a
            LEFT JOIN Sites s ON a.SiteID = s.SiteID
            ORDER BY a.AssetName
        """)).fetchall()

        company_rows = conn.execute(text("""
            SELECT DISTINCT CompanyName FROM Users WHERE CompanyName IS NOT NULL
            UNION
            SELECT DISTINCT CompanyName FROM Transactions WHERE CompanyName IS NOT NULL
        """)).fetchall()
        company_names = sorted(set(r[0] for r in company_rows if r[0]))


    return render_template("admin_sites.html", sites=sites, assets=assets, company_names=company_names)



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
