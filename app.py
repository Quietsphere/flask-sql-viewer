from flask import Flask, render_template
import sqlite3

app = Flask(__name__)

def get_data():
    conn = sqlite3.connect("mydata.db")
    cursor = conn.cursor()
    cursor.execute("SELECT * FROM my_table")
    rows = cursor.fetchall()
    headers = [description[0] for description in cursor.description]
    conn.close()
    return headers, rows

@app.route("/")
def index():
    headers, rows = get_data()
    return render_template("index.html", headers=headers, rows=rows)

if __name__ == "__main__":
    app.run(debug=True)
