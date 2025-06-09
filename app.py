import os
from flask import Flask, render_template
from sqlalchemy import create_engine
import pandas as pd

app = Flask(__name__)

# --- SQL Server connection info ---
server = 'tankfluid-db.cxc6esakoxki.us-east-2.rds.amazonaws.com'
database = 'FluidData'
username = 'sqladmin'
password = 'LAS2025!'
driver = 'ODBC Driver 17 for SQL Server'

connection_string = f"mssql+pyodbc://{username}:{password}@{server}/{database}?driver={driver.replace(' ', '+')}"
engine = create_engine(connection_string)

# --- This is the missing get_data function ---
def get_data(query):
    df = pd.read_sql(query, engine)
    headers = df.columns.tolist()
    rows = df.values.tolist()
    return headers, rows

# --- Routes ---
@app.route("/")
def index():
    headers, rows = get_data("SELECT * FROM my_table")
    return render_template("index.html", headers=headers, rows=rows)

@app.route("/tanklevels")
def tank_levels():
    headers, rows = get_data("SELECT * FROM TankLevels ORDER BY ReadingTimestamp DESC")
    return render_template("tank_levels.html", headers=headers, rows=rows)

# --- Run the app ---
if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port)
