from datetime import datetime
import csv
import io
import subprocess
from functools import wraps

from flask import (
    Flask,
    flash,
    redirect,
    render_template_string,
    request,
    send_file,
    url_for,
)
from flask_login import (
    LoginManager,
    UserMixin,
    current_user,
    login_required,
    login_user,
    logout_user,
)
from flask_sqlalchemy import SQLAlchemy
from sqlalchemy import or_
from werkzeug.security import check_password_hash, generate_password_hash

app = Flask(__name__)
app.config["SECRET_KEY"] = "change-this-secret-key"
app.config["SQLALCHEMY_DATABASE_URI"] = "sqlite:///network_monitor.db"
app.config["SQLALCHEMY_TRACK_MODIFICATIONS"] = False

PING_ATTEMPTS = 4
PING_TIMEOUT_SECONDS = 2

STATUS_UP = "UP"
STATUS_DOWN = "DOWN"
STATUS_UNSTABLE = "UNSTABLE"
STATUS_UNKNOWN = "UNKNOWN"
STATUS_MAINTENANCE = "MAINTENANCE"

ROLES = ["Admin", "Viewer", "Technician"]

db = SQLAlchemy(app)
login_manager = LoginManager(app)
login_manager.login_view = "login"
login_manager.login_message = "Please log in first."


class User(UserMixin, db.Model):
    id = db.Column(db.Integer, primary_key=True)
    username = db.Column(db.String(80), unique=True, nullable=False)
    password_hash = db.Column(db.String(255), nullable=False)
    role = db.Column(db.String(20), nullable=False, default="Viewer")
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

    def set_password(self, raw_password: str) -> None:
        self.password_hash = generate_password_hash(raw_password)

    def check_password(self, raw_password: str) -> bool:
        return check_password_hash(self.password_hash, raw_password)


class Device(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(120), nullable=False)
    ip_address = db.Column(db.String(64), unique=True, nullable=False)
    lab = db.Column(db.String(50), nullable=False)
    device_type = db.Column(db.String(50), nullable=False, default="PC")
    status = db.Column(db.String(20), nullable=False, default=STATUS_UNKNOWN)
    is_monitored = db.Column(db.Boolean, default=True)
    failure_count = db.Column(db.Integer, nullable=False, default=0)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    last_checked_at = db.Column(db.DateTime, nullable=True)


class StatusHistory(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    device_id = db.Column(db.Integer, db.ForeignKey("device.id"), nullable=False)
    device = db.relationship("Device", backref="history")
    status = db.Column(db.String(20), nullable=False)
    details = db.Column(db.String(255), nullable=True)
    checked_at = db.Column(db.DateTime, default=datetime.utcnow)


class AuditLog(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    username = db.Column(db.String(80), nullable=False)
    action = db.Column(db.String(255), nullable=False)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)


@login_manager.user_loader
def load_user(user_id):
    return db.session.get(User, int(user_id))

def log_action(username: str, action: str) -> None:
    db.session.add(AuditLog(username=username, action=action))
    db.session.commit()


def role_required(*allowed_roles):
    def decorator(view_func):
        @wraps(view_func)
        def wrapped(*args, **kwargs):
            if not current_user.is_authenticated:
                return login_manager.unauthorized()
            if current_user.role not in allowed_roles:
                flash("You do not have permission to access this page.", "danger")
                return redirect(url_for("dashboard"))
            return view_func(*args, **kwargs)

        return wrapped

    return decorator


def ping_once(ip: str) -> bool:
    result = subprocess.run(
        ["ping", "-c", "1", "-W", str(PING_TIMEOUT_SECONDS), ip],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    return result.returncode == 0


def determine_ping_result(ip: str) -> tuple[int, str]:
    successes = 0
    for _ in range(PING_ATTEMPTS):
        if ping_once(ip):
            successes += 1
    details = f"{successes}/{PING_ATTEMPTS} successful pings"
    return successes, details


def update_device_status(device: Device) -> bool:
    if not device.is_monitored:
        old_status = device.status
        device.status = STATUS_MAINTENANCE
        device.last_checked_at = datetime.utcnow()
        db.session.add(
            StatusHistory(
                device_id=device.id,
                status=STATUS_MAINTENANCE,
                details="Monitoring disabled for maintenance",
            )
        )
        db.session.commit()
        return old_status != device.status and device.status in [STATUS_DOWN, STATUS_UNSTABLE]

    successes, details = determine_ping_result(device.ip_address)
    old_status = device.status

    if successes == PING_ATTEMPTS:
        device.status = STATUS_UP
        device.failure_count = 0
    elif successes > 0:
        device.status = STATUS_UNSTABLE
        device.failure_count = 0
    else:
        device.failure_count += 1
        if device.failure_count >= 2:
            device.status = STATUS_DOWN
        else:
            device.status = STATUS_UNSTABLE
            details += " | waiting for second consecutive failure before marking DOWN"

    device.last_checked_at = datetime.utcnow()

    db.session.add(
        StatusHistory(device_id=device.id, status=device.status, details=details)
    )

    if old_status != device.status and device.status in [STATUS_DOWN, STATUS_UNSTABLE]:
        db.session.add(
            AuditLog(
                username="system",
                action=f"ALERT: {device.name} changed from {old_status} to {device.status}",
            )
        )

    db.session.commit()
    return old_status != device.status and device.status in [STATUS_DOWN, STATUS_UNSTABLE]


def render_page(title: str, body_template: str, **context):
    page_template = """
    <!DOCTYPE html>
    <html lang="en">
    <head>
        <meta charset="UTF-8">
        <meta name="viewport" content="width=device-width, initial-scale=1.0">
        <title>{{ title }}</title>
        {% if auto_refresh %}
<meta http-equiv="refresh" content="5">
        {% endif %}
        <style>
            * { box-sizing: border-box; }
            body { margin: 0; font-family: Arial, sans-serif; background: #f4f7fb; color: #1f2937; }
            .wrapper { max-width: 1180px; margin: 24px auto; padding: 0 16px; }
            .topbar {
                background: linear-gradient(135deg, #0f172a, #1d4ed8);
                color: white; padding: 18px 22px; border-radius: 16px;
                display: flex; justify-content: space-between; align-items: center; gap: 12px;
                flex-wrap: wrap;
            }
            .topbar h1 { margin: 0; font-size: 28px; }
            .muted { color: #dbeafe; font-size: 14px; }
            .actions { display: flex; gap: 10px; flex-wrap: wrap; }
            .btn, button {
                border: none; border-radius: 10px; padding: 10px 14px; cursor: pointer;
                background: #111827; color: white; text-decoration: none; font-size: 14px;
            }
            .btn.light { background: white; color: #111827; }
            .btn.green { background: #166534; }
            .btn.red { background: #991b1b; }
            .btn.orange { background: #b45309; }
            .grid { display: grid; grid-template-columns: repeat(auto-fit, minmax(180px, 1fr)); gap: 14px; margin: 18px 0; }
            .card { background: white; border-radius: 14px; padding: 18px; border: 1px solid #e5e7eb; box-shadow: 0 8px 18px rgba(0,0,0,.05); }
            .card-title { color: #6b7280; font-size: 14px; margin-bottom: 8px; }
            .card-value { font-size: 28px; font-weight: 700; }
            .panel { background: white; border-radius: 16px; border: 1px solid #e5e7eb; box-shadow: 0 8px 18px rgba(0,0,0,.05); padding: 16px; margin-top: 16px; overflow-x: auto; }
            table { width: 100%; border-collapse: collapse; }
            th, td { padding: 12px 10px; border-bottom: 1px solid #e5e7eb; text-align: left; }
            thead { background: #111827; color: white; }
            .badge { display:inline-block; min-width: 95px; text-align:center; padding:6px 10px; border-radius:999px; font-size:12px; font-weight:700; }
            .UP { background:#dcfce7; color:#166534; border:1px solid #86efac; }
            .DOWN { background:#fee2e2; color:#991b1b; border:1px solid #fca5a5; }
            .UNSTABLE { background:#fef3c7; color:#92400e; border:1px solid #fcd34d; }
            .UNKNOWN { background:#e5e7eb; color:#374151; border:1px solid #d1d5db; }
            .MAINTENANCE { background:#dbeafe; color:#1d4ed8; border:1px solid #93c5fd; }
            .toolbar { display:flex; gap:10px; flex-wrap:wrap; align-items:end; }
            input, select {
                width:100%; padding:10px 12px; border:1px solid #d1d5db; border-radius:10px; font-size:14px;
            }
            .field { min-width:180px; flex:1; }
            .flash { padding: 12px 14px; border-radius: 10px; margin: 12px 0; }
            .flash.success { background: #dcfce7; color: #166534; }
            .flash.danger { background: #fee2e2; color: #991b1b; }
            .flash.info { background: #dbeafe; color: #1d4ed8; }
            .login-box { max-width: 420px; margin: 60px auto; background:white; padding:22px; border-radius:16px; border:1px solid #e5e7eb; box-shadow:0 8px 18px rgba(0,0,0,.06); }
            .small { font-size: 12px; color: #6b7280; }
            .inline-form { display:inline; }
        </style>
    </head>
    <body>
        <div class="wrapper">
            {% with messages = get_flashed_messages(with_categories=true) %}
                {% if messages %}
                    {% for category, message in messages %}
                        <div class="flash {{ category }}">{{ message }}</div>
                    {% endfor %}
                {% endif %}
            {% endwith %}
            """ + body_template + """
        </div>

        {% if play_alert %}
        <audio autoplay>
            <source src="data:audio/wav;base64,UklGRlQAAABXQVZFZm10IBAAAAABAAEAESsAACJWAAACABAAZGF0YQgAAAAA" type="audio/wav">
        </audio>
        {% endif %}
    </body>
    </html>
    """
    return render_template_string(page_template, title=title, **context)


@app.route("/init")
def init_db():
    db.create_all()

    if not User.query.filter_by(username="admin").first():
        admin = User(username="admin", role="Admin")
        admin.set_password("admin123")

        viewer = User(username="viewer", role="Viewer")
        viewer.set_password("viewer123")

        technician = User(username="tech", role="Technician")
        technician.set_password("tech123")

        db.session.add_all([admin, viewer, technician])

    if Device.query.count() == 0:
        sample_devices = [
            Device(name="Main Router", ip_address="10.0.12.1", lab="Core", device_type="Router"),
            Device(name="Faculty A Router", ip_address="10.0.12.2", lab="Lab A", device_type="Router"),
            Device(name="Faculty B Router", ip_address="10.0.13.2", lab="Lab B", device_type="Router"),
            Device(name="PC-A1", ip_address="192.168.10.10", lab="Lab A", device_type="PC"),
            Device(name="PC-A2", ip_address="192.168.10.20", lab="Lab A", device_type="PC"),
            Device(name="PC-B1", ip_address="192.168.20.10", lab="Lab B", device_type="PC"),
            Device(name="PC-B2", ip_address="192.168.20.20", lab="Lab B", device_type="PC"),
        ]
        db.session.add_all(sample_devices)

    db.session.commit()
    return "Database initialized. Login with admin/admin123, viewer/viewer123, tech/tech123"


@app.route("/login", methods=["GET", "POST"])
def login():
    if request.method == "POST":
        username = request.form.get("username", "").strip()
        password = request.form.get("password", "")
        user = User.query.filter_by(username=username).first()

        if user and user.check_password(password):
            login_user(user)
            log_action(user.username, "Logged in")
            flash("Login successful.", "success")
            return redirect(url_for("dashboard"))

        flash("Invalid username or password.", "danger")

    body = """
    <div class='login-box'>
        <h2>Login</h2>
        <p style='color:#6b7280'>Access the campus network monitor dashboard</p>
        <form method='POST'>
            <div class='field' style='margin-bottom:12px;'>
                <label>Username</label>
                <input name='username' required>
            </div>
            <div class='field' style='margin-bottom:12px;'>
                <label>Password</label>
                <input name='password' type='password' required>
            </div>
            <button type='submit'>Login</button>
        </form>
    </div>
    """
    return render_page("Login", body, play_alert=False, auto_refresh=False)

@app.route("/logout")
@login_required
def logout():
    username = current_user.username
    logout_user()
    log_action(username, "Logged out")
    flash("Logged out successfully.", "info")
    return redirect(url_for("login"))


@app.route("/")
@login_required
def dashboard():
    query = request.args.get("q", "").strip()
    lab_filter = request.args.get("lab", "").strip()
    status_filter = request.args.get("status", "").strip()

    devices_query = Device.query
    if query:
        devices_query = devices_query.filter(
            or_(
                Device.name.ilike(f"%{query}%"),
                Device.ip_address.ilike(f"%{query}%"),
                Device.device_type.ilike(f"%{query}%"),
            )
        )
    if lab_filter:
        devices_query = devices_query.filter(Device.lab == lab_filter)
    if status_filter:
        devices_query = devices_query.filter(Device.status == status_filter)

    devices = devices_query.order_by(Device.lab.asc(), Device.name.asc()).all()

    alert_needed = False
    for device in devices:
        changed_to_problem = update_device_status(device)
        if changed_to_problem:
            alert_needed = True

    all_devices = Device.query.order_by(Device.lab.asc(), Device.name.asc()).all()
    up_count = sum(1 for d in all_devices if d.status == STATUS_UP)
    down_count = sum(1 for d in all_devices if d.status == STATUS_DOWN)
    unstable_count = sum(1 for d in all_devices if d.status == STATUS_UNSTABLE)
    maintenance_count = sum(1 for d in all_devices if d.status == STATUS_MAINTENANCE)
    unknown_count = sum(1 for d in all_devices if d.status == STATUS_UNKNOWN)
    labs = [row[0] for row in db.session.query(Device.lab).distinct().all()]

    body = """
    <div class='topbar'>
        <div>
            <h1>Campus Network Monitor</h1>
            <div class='muted'>User: {{ current_user.username }} | Role: {{ current_user.role }}</div>
        </div>
        <div class='actions'>
            {% if current_user.role in ['Admin', 'Technician'] %}
            <a class='btn light' href='{{ url_for("add_device") }}'>Add Device</a>
            {% endif %}
            <a class='btn light' href='{{ url_for("history") }}'>History</a>
            <a class='btn light' href='{{ url_for("logs") }}'>Logs</a>
            <a class='btn green' href='{{ url_for("export_report") }}'>Export Report</a>
            <a class='btn red' href='{{ url_for("logout") }}'>Logout</a>
        </div>
    </div>

    <div class='grid'>
        <div class='card'><div class='card-title'>Total Devices</div><div class='card-value'>{{ total }}</div></div>
        <div class='card'><div class='card-title'>UP</div><div class='card-value'>{{ up_count }}</div></div>
        <div class='card'><div class='card-title'>DOWN</div><div class='card-value'>{{ down_count }}</div></div>
        <div class='card'><div class='card-title'>UNSTABLE</div><div class='card-value'>{{ unstable_count }}</div></div>
        <div class='card'><div class='card-title'>MAINTENANCE</div><div class='card-value'>{{ maintenance_count }}</div></div>
        <div class='card'><div class='card-title'>UNKNOWN</div><div class='card-value'>{{ unknown_count }}</div></div>
    </div>

    <div class='panel'>
        <form method='GET' class='toolbar'>
            <div class='field'>
                <label>Search</label>
                <input type='text' name='q' value='{{ request.args.get("q", "") }}' placeholder='Name, IP, type'>
            </div>
            <div class='field'>
                <label>Lab</label>
                <select name='lab'>
                    <option value=''>All Labs</option>
                    {% for lab in labs %}
                    <option value='{{ lab }}' {% if request.args.get('lab') == lab %}selected{% endif %}>{{ lab }}</option>
                    {% endfor %}
                </select>
            </div>
            <div class='field'>
                <label>Status</label>
                <select name='status'>
                    <option value=''>All Statuses</option>
                    {% for status in statuses %}
                    <option value='{{ status }}' {% if request.args.get('status') == status %}selected{% endif %}>{{ status }}</option>
                    {% endfor %}
                </select>
            </div>
            <button type='submit'>Apply</button>
            <a class='btn' href='{{ url_for("dashboard") }}'>Reset</a>
        </form>
    </div>

    <div class='panel'>
        <table>
            <thead>
                <tr>
                    <th>Device</th>
                    <th>IP</th>
                    <th>Lab</th>
                    <th>Type</th>
                    <th>Status</th>
                    <th>Last Check</th>
                    <th>Failure Count</th>
                    {% if current_user.role in ['Admin', 'Technician'] %}
                    <th>Actions</th>
                    {% endif %}
                </tr>
            </thead>
            <tbody>
                {% for d in devices %}
                <tr>
                    <td>{{ d.name }}</td>
                    <td>{{ d.ip_address }}</td>
                    <td>{{ d.lab }}</td>
                    <td>{{ d.device_type }}</td>
                    <td><span class='badge {{ d.status }}'>{{ d.status }}</span></td>
                    <td>{{ d.last_checked_at.strftime('%Y-%m-%d %H:%M:%S') if d.last_checked_at else 'Never' }}</td>
                    <td>{{ d.failure_count }}</td>
                    {% if current_user.role in ['Admin', 'Technician'] %}
                    <td>
                        <form method='POST' action='{{ url_for("delete_device", device_id=d.id) }}' onsubmit='return confirm("Delete this device?")' class='inline-form'>
                            <button type='submit' class='btn red'>Delete</button>
                        </form>
                    </td>
                    {% endif %}
                </tr>
                {% endfor %}
            </tbody>
        </table>
        <p class='small'>Auto refresh every 5 seconds. Device becomes DOWN only after 2 consecutive full ping failures.</p>
    </div>
    """

    return render_page(
        "Dashboard",
        body,
        play_alert=alert_needed,
        auto_refresh=True,
        current_user=current_user,
        total=len(all_devices),
        up_count=up_count,
        down_count=down_count,
        unstable_count=unstable_count,
        maintenance_count=maintenance_count,
        unknown_count=unknown_count,
        devices=devices,
        labs=labs,
        statuses=[STATUS_UP, STATUS_DOWN, STATUS_UNSTABLE, STATUS_UNKNOWN, STATUS_MAINTENANCE],
        request=request,
    )


@app.route("/add-device", methods=["GET", "POST"])
@login_required
@role_required("Admin", "Technician")
def add_device():
    if request.method == "POST":
        name = request.form.get("name", "").strip()
        ip_address = request.form.get("ip_address", "").strip()
        lab = request.form.get("lab", "").strip()
        device_type = request.form.get("device_type", "PC").strip()

        existing = Device.query.filter_by(ip_address=ip_address).first()
        if existing:
            flash("A device with this IP already exists.", "danger")
        else:
            device = Device(name=name, ip_address=ip_address, lab=lab, device_type=device_type)
            db.session.add(device)
            db.session.commit()
            log_action(current_user.username, f"Added device {name} ({ip_address})")
            flash("Device added successfully.", "success")
            return redirect(url_for("dashboard"))

    body = """
    <div class='topbar'>
        <div>
            <h1>Add Device</h1>
            <div class='muted'>Only Admin and Technician can add devices</div>
        </div>
        <div class='actions'>
            <a class='btn light' href='{{ url_for("dashboard") }}'>Back</a>
        </div>
    </div>

    <div class='panel'>
        <form method='POST' class='toolbar'>
            <div class='field'><label>Name</label><input name='name' required></div>
            <div class='field'><label>IP Address</label><input name='ip_address' required></div>
            <div class='field'>
                <label>Lab</label>
                <select name='lab' required>
                    <option value='Lab A'>Lab A</option>
                    <option value='Lab B'>Lab B</option>
                    <option value='Core'>Core</option>
                </select>
            </div>
            <div class='field'>
                <label>Device Type</label>
                <select name='device_type'>
                    <option value='PC'>PC</option>
                    <option value='Router'>Router</option>
                    <option value='Switch'>Switch</option>
                    <option value='Printer'>Printer</option>
                    <option value='Server'>Server</option>
                </select>
            </div>
            <button type='submit'>Save</button>
        </form>
    </div>
    """
    return render_page("Add Device", body, play_alert=False)


@app.route("/delete-device/<int:device_id>", methods=["POST"])
@login_required
@role_required("Admin", "Technician")
def delete_device(device_id):
    device = Device.query.get_or_404(device_id)
    device_name = device.name

    StatusHistory.query.filter_by(device_id=device.id).delete()
    db.session.delete(device)
    db.session.commit()

    log_action(current_user.username, f"Deleted device {device_name}")
    flash("Device deleted successfully.", "success")
    return redirect(url_for("dashboard"))


@app.route("/history")
@login_required
def history():
    rows = StatusHistory.query.order_by(StatusHistory.checked_at.desc()).limit(200).all()
    body = """
    <div class='topbar'>
        <div>
            <h1>Status History</h1>
            <div class='muted'>Last 200 checks</div>
        </div>
        <div class='actions'>
            <a class='btn light' href='{{ url_for("dashboard") }}'>Back</a>
        </div>
    </div>
    <div class='panel'>
        <table>
            <thead><tr><th>Time</th><th>Device</th><th>Status</th><th>Details</th></tr></thead>
            <tbody>
                {% for row in rows %}
                <tr>
                    <td>{{ row.checked_at.strftime('%Y-%m-%d %H:%M:%S') }}</td>
                    <td>{{ row.device.name }}</td>
                    <td><span class='badge {{ row.status }}'>{{ row.status }}</span></td>
                    <td>{{ row.details }}</td>
                </tr>
                {% endfor %}
            </tbody>
        </table>
    </div>
    """
    return render_page("History", body, rows=rows, play_alert=False)


@app.route("/logs")
@login_required
@role_required("Admin")
def logs():
    rows = AuditLog.query.order_by(AuditLog.created_at.desc()).limit(200).all()
    body = """
    <div class='topbar'>
        <div>
            <h1>Audit Logs</h1>
            <div class='muted'>Admin only</div>
        </div>
        <div class='actions'>
            <a class='btn light' href='{{ url_for("dashboard") }}'>Back</a>
        </div>
    </div>
    <div class='panel'>
        <table>
            <thead><tr><th>Time</th><th>User</th><th>Action</th></tr></thead>
            <tbody>
                {% for row in rows %}
                <tr>
                    <td>{{ row.created_at.strftime('%Y-%m-%d %H:%M:%S') }}</td>
                    <td>{{ row.username }}</td>
                    <td>{{ row.action }}</td>
                </tr>
                {% endfor %}
            </tbody>
        </table>
    </div>
    """
    return render_page("Logs", body, rows=rows, play_alert=False)


@app.route("/export-report")
@login_required
def export_report():
    output = io.StringIO()
    writer = csv.writer(output)
    writer.writerow(["Device", "IP Address", "Lab", "Type", "Status", "Last Checked", "Failure Count"])

    for device in Device.query.order_by(Device.lab.asc(), Device.name.asc()).all():
        writer.writerow([
            device.name,
            device.ip_address,
            device.lab,
            device.device_type,
            device.status,
            device.last_checked_at.strftime('%Y-%m-%d %H:%M:%S') if device.last_checked_at else "Never",
            device.failure_count,
        ])

    memory = io.BytesIO()
    memory.write(output.getvalue().encode("utf-8-sig"))
    memory.seek(0)
    return send_file(
        memory,
        as_attachment=True,
        download_name="network_report.csv",
        mimetype="text/csv",
    )


if __name__ == "__main__":
    with app.app_context():
        db.create_all()
    app.run(host="0.0.0.0", port=5000, debug=True)
