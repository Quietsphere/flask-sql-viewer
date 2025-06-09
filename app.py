import os
from flask import Flask, render_template
from sqlalchemy import create_engine
import pandas as pd

app = Flask(__name__)

# your DB code...

@app.route("/")
def index():
    headers, rows = get_data("SELECT * FROM my_table")
    return render_template("index.html", headers=headers, rows=rows)

@app.route("/tanklevels")
def tank_levels():
    headers, rows = get_data("SELECT * FROM TankLevels ORDER BY ReadingTimestamp DESC")
    return render_template("tank_levels.html", headers=headers, rows=rows)

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port)
