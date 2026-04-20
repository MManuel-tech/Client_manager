# app.py (PART 1)
import os
import shutil
import io
from datetime import datetime, date 
from textwrap import wrap
from io import BytesIO

from flask import (
    Flask, render_template_string, request, redirect, url_for,
    send_from_directory, send_file, current_app, flash
)
from flask_sqlalchemy import SQLAlchemy
from flask_login import LoginManager, UserMixin, login_user, logout_user, login_required
from werkzeug.utils import secure_filename
from flask import flash 


# ReportLab / PDF helpers (used later)
from reportlab.lib.pagesizes import A4
from reportlab.lib.utils import ImageReader
from reportlab.pdfgen import canvas
from reportlab.lib.units import mm

# --- App setup ---
import os

app = Flask(__name__)
app.secret_key = 'cargobloc_secret_key'

DATABASE_URL = os.getenv("DATABASE_URL")

if not DATABASE_URL:
    raise RuntimeError("DATABASE_URL is not set. App cannot start.")

app.config['SQLALCHEMY_DATABASE_URI'] = DATABASE_URL
app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False
app.config['UPLOAD_FOLDER'] = 'uploads'

db = SQLAlchemy(app)
login_manager = LoginManager(app)
login_manager.login_view = 'login'

# -----------------------
# Helpers referencing current_app
# -----------------------
def get_static_file(filename):
    """Return absolute path to a file in the `static/` folder."""
    return os.path.join(current_app.root_path, 'static', filename)

def link_callback(uri, rel):
    """Helper when building PDFs that reference /static/ paths."""
    if uri.startswith('/static/'):
        path = os.path.join(current_app.root_path, uri.lstrip('/'))
        return path
    return uri

# -----------------------
# MODELS
# -----------------------
class User(UserMixin, db.Model):
    id = db.Column(db.Integer, primary_key=True)
    username = db.Column(db.String(50), unique=True, nullable=False)
    password = db.Column(db.String(50), nullable=False)

class Client(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(120))
    email = db.Column(db.String(120))
    phone = db.Column(db.String(50))
    notes = db.Column(db.Text)
    bls = db.relationship('BL', backref='client', cascade="all, delete-orphan")
    documents = db.relationship('ClientDocument', backref='client', cascade="all, delete-orphan")

class BL(db.Model):
    __tablename__ = 'bl'
    __table_args__ = (
    db.UniqueConstraint('client_id', 'bl_number', name='uq_client_bl'),
    )
    id = db.Column(db.Integer, primary_key=True)
    bl_number = db.Column(db.String(100))
    amount_total = db.Column(db.Float, default=0)
    amount_paid = db.Column(db.Float, default=0)
    document = db.Column(db.String(200))
    client_id = db.Column(db.Integer, db.ForeignKey('client.id'))
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

    receipt_bls = db.relationship(
        'ReceiptBL',
        backref='bl',
        cascade="all, delete-orphan"
    )
    

    @property
    def amount_unpaid(self):
        return max((self.amount_total or 0) - (self.amount_paid or 0), 0)

class ClientDocument(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    filename = db.Column(db.String(200))
    description = db.Column(db.String(200))
    client_id = db.Column(db.Integer, db.ForeignKey('client.id'))

class HouseBL(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    exporter = db.Column(db.String(200))
    bl_number = db.Column(db.String(100))
    forwarding_agent = db.Column(db.String(200))
    consignee = db.Column(db.String(200))
    notify_party = db.Column(db.String(200))
    vessel = db.Column(db.String(100))
    voyage = db.Column(db.String(100))
    port_loading = db.Column(db.String(100))
    port_discharge = db.Column(db.String(100))
    place_delivery = db.Column(db.String(100))
    marks_numbers = db.Column(db.Text)
    pkgs = db.Column(db.String(100))
    description_goods = db.Column(db.Text)
    gross_weight = db.Column(db.String(100))
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

class Receipt(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    client_id = db.Column(db.Integer, db.ForeignKey('client.id'))
    amount = db.Column(db.Float, nullable=False)
    date = db.Column(db.Date, default=datetime.utcnow)
    method = db.Column(db.String(100))
    reference = db.Column(db.String(100))
    description = db.Column(db.String(255))
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

    client = db.relationship('Client', backref='receipts')

    receipt_bls = db.relationship(
        'ReceiptBL',
        backref='receipt',
        cascade="all, delete-orphan"
    )

class ReceiptBL(db.Model):
    id = db.Column(db.Integer, primary_key=True)

    receipt_id = db.Column(
        db.Integer,
        db.ForeignKey('receipt.id'),
        nullable=False
    )

    bl_id = db.Column(
        db.Integer,
        db.ForeignKey('bl.id'),
        nullable=False
    )

    amount_applied = db.Column(db.Float, default=0)

    created_at = db.Column(db.DateTime, default=datetime.utcnow)
# -----------------------
# ACTIVITY LOG MODEL
# -----------------------
class Activity(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    action = db.Column(db.String(150), nullable=False)
    reference = db.Column(db.String(250))
    user = db.Column(db.String(120))
    created_at = db.Column(db.DateTime, default=datetime.utcnow, index=True)


# -----------------------
# ACTIVITY LOGGER (SAFE)
# -----------------------
def add_activity(action, reference=None, user=None):
    try:
        db.session.add(
            Activity(
                action=action,
                reference=reference,
                user=user
            )
        )
        db.session.commit()
    except Exception as e:
        print("⚠ Activity log error:", e)
        db.session.rollback() 
#------------------------        
#  BL HELPER
#------------------------
def bl_exists_for_client(client_id, bl_number):
    return (
        BL.query
        .filter(
            BL.client_id == client_id,
            db.func.lower(BL.bl_number) == bl_number.lower()
        )
        .first()
        is not None
    )

         
# -----------------------
# LOGIN MANAGEMENT
# -----------------------
@login_manager.user_loader
def load_user(user_id):
    return User.query.get(int(user_id))

@app.before_request
def create_default_user():
    # create admin if DB empty; password requested earlier: Cargo@conso123
    # NOTE: this runs before every request but checks quickly if DB empty.
    try:
        if not User.query.first():
            db.session.add(User(username='admin', password='Cargo@conso123'))
            db.session.commit()
            print("✅ Default login → username: admin | password: Cargo@conso123")
    except Exception:
        # If DB not ready (rare), ignore; db.create_all is run in __main__.
        pass

# -----------------------
# ROUTES (part 1)
# -----------------------
@app.route('/login', methods=['GET', 'POST'])
def login():
    error = None
    if request.method == 'POST':
        username = request.form.get('username', '').strip()
        password = request.form.get('password', '').strip()
        remember = request.form.get('remember') == 'on'

        user = User.query.filter_by(username=username, password=password).first()
        if user:
            login_user(user, remember=remember)
            return redirect(url_for('home'))
        else:
            error = "Invalid username or password."

    return render_template_string(LOGIN_HTML, error=error)

@app.route('/forgot-password', methods=['GET', 'POST'])
def forgot_password():
    info = None
    if request.method == 'POST':
        email = request.form.get('email', '').strip()
        if email:
            # placeholder
            info = f"If {email} is registered, a reset link will be sent shortly."
        else:
            info = "Please enter a valid email."
    return render_template_string(FORGOT_HTML, info=info)

@app.route('/logout')
@login_required
def logout():
    logout_user()
    return redirect(url_for('login'))

@app.route('/')
@login_required
def home():
    q = request.args.get('q', '').strip()
    date_str = request.args.get('date', '').strip()

    query = Client.query

    if q:
        query = query.filter(
            (Client.name.ilike(f'%{q}%')) |
            (Client.bls.any(BL.bl_number.ilike(f'%{q}%')))
        )

    if date_str:
        try:
            date_obj = datetime.strptime(date_str, "%Y-%m-%d").date()
            query = query.join(BL).filter(db.func.date(BL.created_at) == date_obj)
        except ValueError:
            pass

    clients = query.all()

    # =========================
    # DASHBOARD METRICS
    # =========================
    total_billed = 0.0
    total_paid = 0.0
    total_unpaid = 0.0

    cleared = part_paid = owing = 0
    overdue_30 = overdue_60 = overdue_90 = 0

    today = date.today()
    receipts_today = receipts_7 = receipts_30 = 0

    activities = []

    # small helper to normalize created_at -> date object or None
    def to_date_obj(dt):
        if dt is None:
            return None
        if isinstance(dt, datetime):
            return dt.date()
        # assume already a date-like object
        return dt

    for c in clients:
        for bl in getattr(c, "bls", []) or []:
            amt_total = float(bl.amount_total or 0)
            amt_paid = float(bl.amount_paid or 0)
            total_billed += amt_total
            total_paid += amt_paid
            unpaid = max(amt_total - amt_paid, 0.0)
            total_unpaid += unpaid

            # STATUS DISTRIBUTION
            if unpaid <= 0:
                cleared += 1
            elif amt_paid > 0:
                part_paid += 1
            else:
                owing += 1

            # OVERDUE RISK (if BL has created_at)
            created_dt = getattr(bl, "created_at", None)
            created_date = to_date_obj(created_dt)
            if created_date and unpaid > 0:
                age = (today - created_date).days
                if age >= 90:
                    overdue_90 += 1
                elif age >= 60:
                    overdue_60 += 1
                elif age >= 30:
                    overdue_30 += 1

            # ACTIVITY (keep original datetime if present)
            if created_dt:
                activities.append({
                    "text": f"BL added: {getattr(bl, 'bl_number', '')} ({getattr(c, 'name', 'Client')})",
                    "time": created_dt
                })

        # receipts for this client
        for r in getattr(c, "receipts", []) or []:
            created_dt = getattr(r, "created_at", None)
            created_date = to_date_obj(created_dt)
            if created_dt:
                activities.append({
                    "text": f"Receipt generated for {getattr(c, 'name', 'Client')} (₵{(r.amount or 0):,.2f})",
                    "time": created_dt
                })
            if created_date:
                delta = (today - created_date).days
                if delta == 0:
                    receipts_today += 1
                if delta <= 7:
                    receipts_7 += 1
                if delta <= 30:
                    receipts_30 += 1

    # House BL activity (recent)
    for h in HouseBL.query.order_by(HouseBL.created_at.desc()).limit(10).all():
        activities.append({
            "text": f"House BL created: {getattr(h, 'bl_number', '')}",
            "time": getattr(h, "created_at", None)
        })

    # sort activities newest first; missing times are treated as very old
    activities.sort(key=lambda x: x.get("time") or datetime.min, reverse=True)

    return render_template_string(
        HOME_HTML,
        clients=clients,
        total_billed=total_billed,
        total_paid=total_paid,
        total_unpaid=total_unpaid,

        cleared=cleared,
        part_paid=part_paid,
        owing=owing,

        overdue_30=overdue_30,
        overdue_60=overdue_60,
        overdue_90=overdue_90,

        receipts_today=receipts_today,
        receipts_7=receipts_7,
        receipts_30=receipts_30,

        activities=activities[:10],
        q=q,
        selected_date=date_str
    )

@app.route('/add_client', methods=['POST'])
@login_required
def add_client():
    c = Client(name=request.form['name'],
               email=request.form.get('email'),
               phone=request.form.get('phone'),
               notes=request.form.get('notes'))
    db.session.add(c)
    db.session.commit()
    return redirect(url_for('clients_page'))

@app.route('/client/<int:client_id>', methods=['GET', 'POST'])
@login_required
def client_detail(client_id):
    client = Client.query.get_or_404(client_id)
    info = None

    # ===== FINANCE SUMMARY =====
    total_billed = sum((bl.amount_total or 0) for bl in client.bls)
    total_paid = sum((bl.amount_paid or 0) for bl in client.bls)
    total_unpaid = total_billed - total_paid

    if total_billed == 0:
        finance_status = "No BLs"
    elif total_unpaid <= 0:
        finance_status = "Cleared"
    elif total_paid > 0:
        finance_status = "Part Paid"
    else:
        finance_status = "Owing"

    # ================= POST ACTIONS =================
    if request.method == 'POST':
        action = request.form.get('action')

        # ===== ADD SINGLE BL =====
        if action == 'add_bl':
            bl_number = request.form.get('bl_number', '').strip()

            if not bl_number:
                flash("BL number is required.", "warning")
                return redirect(url_for('client_detail', client_id=client.id))

            if bl_exists_for_client(client.id, bl_number):
                flash(f"⚠ BL '{bl_number}' has already been booked for this client.", "warning")
                return redirect(url_for('client_detail', client_id=client.id))

            try:
                total = float(request.form.get('amount_total') or 0)
            except:
                total = 0.0

            try:
                paid = float(request.form.get('amount_paid') or 0)
            except:
                paid = 0.0

            file = request.files.get('bl_document')
            filename = None
            if file and file.filename:
                filename = secure_filename(file.filename)
                os.makedirs(app.config['UPLOAD_FOLDER'], exist_ok=True)
                file.save(os.path.join(app.config['UPLOAD_FOLDER'], filename))

            db.session.add(BL(
                bl_number=bl_number,
                amount_total=total,
                amount_paid=paid,
                document=filename,
                client=client
            ))
            db.session.commit()

            flash("BL added successfully.", "success")
            return redirect(url_for('client_detail', client_id=client.id))

        # ===== ADD MULTIPLE BLs =====
        elif action == 'add_multi_bl':
            bl_numbers = request.form.getlist('bl_number[]')
            totals = request.form.getlist('amount_total[]')
            paids = request.form.getlist('amount_paid[]')

            skipped = []
            added = 0

            for i in range(len(bl_numbers)):
                bl_number = bl_numbers[i].strip()
                if not bl_number:
                    continue

                if bl_exists_for_client(client.id, bl_number):
                    skipped.append(bl_number)
                    continue

                try:
                    total = float(totals[i]) if totals[i] else 0.0
                except:
                    total = 0.0

                try:
                    paid = float(paids[i]) if paids[i] else 0.0
                except:
                    paid = 0.0

                db.session.add(BL(
                    bl_number=bl_number,
                    amount_total=total,
                    amount_paid=paid,
                    client=client
                ))
                added += 1

            db.session.commit()

            if added:
                flash(f"{added} BL(s) added successfully.", "success")

            if skipped:
                flash(
                    "⚠ These BLs were skipped (already exist): " + ", ".join(skipped),
                    "warning"
                )

            return redirect(url_for('client_detail', client_id=client.id))

        # ===== UPLOAD CLIENT DOCUMENT =====
        elif action == 'add_doc':
            file = request.files.get('client_document')
            if file and file.filename:
                filename = secure_filename(file.filename)
                os.makedirs(app.config['UPLOAD_FOLDER'], exist_ok=True)
                file.save(os.path.join(app.config['UPLOAD_FOLDER'], filename))

                desc = request.form.get('doc_desc', '')
                db.session.add(ClientDocument(
                    filename=filename,
                    description=desc,
                    client=client
                ))
                db.session.commit()

            return redirect(url_for('client_detail', client_id=client.id))

        # ===== EXPORT SELECTED BLs =====
        elif action == 'export_selected_bl':
            bl_ids = [int(x) for x in request.form.getlist('bl_ids') if x.isdigit()]

            bls = (
                BL.query
                .filter(BL.client_id == client.id, BL.id.in_(bl_ids))
                .all()
                if bl_ids else []
            )

            if not bls:
                info = "⚠ Please select at least one BL to export."
                return render_template_string(
                    CLIENT_HTML,
                    client=client,
                    info=info,
                    total_billed=total_billed,
                    total_paid=total_paid,
                    total_unpaid=total_unpaid,
                    finance_status=finance_status
                )

            pdf_path = os.path.join(
                app.config['UPLOAD_FOLDER'],
                f"{client.name}_selected_{datetime.now().strftime('%Y%m%d%H%M%S')}.pdf"
            )

            os.makedirs(app.config['UPLOAD_FOLDER'], exist_ok=True)
            create_bl_pdf(client, bls, pdf_path)

            return send_from_directory(
                app.config['UPLOAD_FOLDER'],
                os.path.basename(pdf_path),
                as_attachment=True
            )

        # ===== EDIT CLIENT =====
        elif action == 'edit_client':
            client.name = request.form.get('name', client.name)
            client.email = request.form.get('email', client.email)
            client.phone = request.form.get('phone', client.phone)
            client.notes = request.form.get('notes', client.notes)
            db.session.commit()
            return redirect(url_for('client_detail', client_id=client.id))

    # ================= GET RENDER =================
    return render_template_string(
        CLIENT_HTML,
        client=client,
        info=info,
        total_billed=total_billed,
        total_paid=total_paid,
        total_unpaid=total_unpaid,
        finance_status=finance_status
    )   

@app.route('/client/<int:client_id>/delete')
@login_required
def delete_client(client_id):
    db.session.delete(Client.query.get_or_404(client_id))
    db.session.commit()
    return redirect(url_for('home'))

@app.route('/bl/<int:bl_id>/delete')
@login_required
def delete_bl(bl_id):
    bl = BL.query.get_or_404(bl_id)
    cid = bl.client_id
    # remove associated file if exists
    if bl.document:
        p = os.path.join(app.config['UPLOAD_FOLDER'], bl.document)
        try:
            if os.path.exists(p):
                os.remove(p)
        except:
            pass
    db.session.delete(bl)
    db.session.commit()
    return redirect(url_for('client_detail', client_id=cid))

@app.route('/uploads/<path:filename>')
def uploaded_file(filename):
    return send_from_directory(app.config['UPLOAD_FOLDER'], filename)

@app.route('/client/<int:client_id>/export')
@login_required
def export_client_pdf(client_id):
    client = Client.query.get_or_404(client_id)
    pdf_path = os.path.join(app.config['UPLOAD_FOLDER'], f"{client.name}summary{datetime.now().strftime('%Y%m%d%H%M%S')}.pdf")
    os.makedirs(app.config['UPLOAD_FOLDER'], exist_ok=True)
    create_bl_pdf(client, client.bls, pdf_path)
    return send_from_directory(app.config['UPLOAD_FOLDER'], os.path.basename(pdf_path), as_attachment=True)

@app.route('/export_all_filtered')
@login_required
def export_all_filtered():
    date_str = request.args.get('date', '').strip()

    query = Client.query
    if date_str:
        try:
            date_obj = datetime.strptime(date_str, "%Y-%m-%d").date()
            query = query.join(BL).filter(db.func.date(BL.created_at) == date_obj)
        except ValueError:
            pass

    clients = query.all()
    all_bls = [bl for c in clients for bl in c.bls]

    if not all_bls:
        flash("No BLs found for the selected date.", "info")
        return redirect(url_for('home'))

    pdf_path = os.path.join(app.config['UPLOAD_FOLDER'], f"CargoBloc_Export_{datetime.now().strftime('%Y%m%d_%H%M%S')}.pdf")
    os.makedirs(app.config['UPLOAD_FOLDER'], exist_ok=True)

    # Create the PDF using your existing helper
    fake_client = type("ClientSummary", (), {"name": f"Filtered BLs ({date_str or 'All'})", "email": "", "phone": ""})()
    create_bl_pdf(fake_client, all_bls, pdf_path)

    return send_from_directory(app.config['UPLOAD_FOLDER'], os.path.basename(pdf_path), as_attachment=True)

@app.route('/house_bl', methods=['GET', 'POST'])
@login_required
def house_bl():
    if request.method == 'POST':
        new_bl = HouseBL(
            exporter=request.form.get('exporter'),
            bl_number=request.form.get('bl_number'),
            forwarding_agent=request.form.get('forwarding_agent'),
            consignee=request.form.get('consignee'),
            notify_party=request.form.get('notify_party'),
            vessel=request.form.get('vessel'),
            voyage=request.form.get('voyage'),
            port_loading=request.form.get('port_loading'),
            port_discharge=request.form.get('port_discharge'),
            place_delivery=request.form.get('place_delivery'),
            marks_numbers=request.form.get('marks_numbers'),
            pkgs=request.form.get('pkgs'),
            description_goods=request.form.get('description_goods'),
            gross_weight=request.form.get('gross_weight')
        )
        db.session.add(new_bl)
        db.session.commit()
        return redirect(url_for('house_bl'))

    all_hbls = HouseBL.query.order_by(HouseBL.created_at.desc()).all()
    return render_template_string(HOUSE_BL_HTML, hbls=all_hbls)

@app.route('/edit_house_bl/<int:hbl_id>', methods=['GET', 'POST'])
@login_required
def edit_house_bl(hbl_id):
    hbl = HouseBL.query.get_or_404(hbl_id)

    if request.method == 'POST':
        hbl.exporter = request.form.get('exporter')
        hbl.bl_number = request.form.get('bl_number')
        hbl.forwarding_agent = request.form.get('forwarding_agent')
        hbl.consignee = request.form.get('consignee')
        hbl.notify_party = request.form.get('notify_party')
        hbl.vessel = request.form.get('vessel')
        hbl.voyage = request.form.get('voyage')
        hbl.port_loading = request.form.get('port_loading')
        hbl.port_discharge = request.form.get('port_discharge')
        hbl.place_delivery = request.form.get('place_delivery')
        hbl.marks_numbers = request.form.get('marks_numbers')
        hbl.pkgs = request.form.get('pkgs')
        hbl.description_goods = request.form.get('description_goods')
        hbl.gross_weight = request.form.get('gross_weight')
        db.session.commit()
        return redirect(url_for('house_bl'))

    return render_template_string(EDIT_HOUSE_BL_HTML, hbl=hbl)

@app.route('/export_house_bl/<int:hbl_id>')
@login_required
def export_house_bl(hbl_id):
    # Builds overlay with reportlab and merges with template using PyPDF2
    from PyPDF2 import PdfReader, PdfWriter

    hbl = HouseBL.query.get_or_404(hbl_id)

    # === Setup paths ===
    base_template = os.path.join(app.config['UPLOAD_FOLDER'], 'CARGOBLOC_HOUSE_BL_TEMPLETE[1].pdf')
    export_filename = f"HouseBL_{hbl.bl_number}_{datetime.now().strftime('%Y%m%d%H%M%S')}.pdf"
    export_path = os.path.join(app.config['UPLOAD_FOLDER'], export_filename)

    # Ensure template exists
    if not os.path.exists(base_template):
        return f"❌ Template not found: {base_template}", 404

    # === Step 1: Create overlay with ReportLab ===
    packet = io.BytesIO()
    c = canvas.Canvas(packet, pagesize=A4)
    c.setFont("Helvetica", 7)

    def draw_wrapped_text(x, y, text, width_chars=50, line_height=9):
        if not text:
            return
        text_obj = c.beginText(x, y)
        for line in wrap(text, width_chars):
            text_obj.textLine(line)
        c.drawText(text_obj)

    # --- top section ---
    draw_wrapped_text(40, 640,  hbl.exporter, 60)            # Exporter
    draw_wrapped_text(450, 658, hbl.bl_number, 25)           # BL number
    draw_wrapped_text(40, 570,  hbl.consignee, 60)           # Consignee
    draw_wrapped_text(420, 587, hbl.forwarding_agent, 40)    # Forwarding agent
    draw_wrapped_text(40, 495,  hbl.notify_party, 60)        # Notify party
    draw_wrapped_text(40, 427,  hbl.vessel, 30)              # Vessel
    draw_wrapped_text(40, 400,  hbl.voyage, 20)              # Voyage
    draw_wrapped_text(191, 399, hbl.port_loading, 20)        # Port of loading
    draw_wrapped_text(332, 400, hbl.port_discharge, 20)      # Port of discharge
    draw_wrapped_text(462, 400, hbl.place_delivery, 25)      # Place of delivery

    # --- goods section ---
    draw_wrapped_text(45, 320,  hbl.marks_numbers, 20)       # Marks and numbers
    draw_wrapped_text(150, 320, hbl.pkgs, 15)                # Packages
    draw_wrapped_text(180, 320, hbl.description_goods, 55)   # Description of goods
    draw_wrapped_text(510, 320, hbl.gross_weight, 10)        # Gross weight

    c.save()

    # === Step 2: Merge overlay with template ===
    packet.seek(0)
    overlay_pdf = PdfReader(packet)
    base_pdf = PdfReader(open(base_template, "rb"))
    output = PdfWriter()

    base_page = base_pdf.pages[0]
    base_page.merge_page(overlay_pdf.pages[0])
    output.add_page(base_page)

    with open(export_path, "wb") as f:
        output.write(f)

    # === Step 3: Send generated file to browser ===
    return send_from_directory(app.config['UPLOAD_FOLDER'], export_filename, as_attachment=True)
    
    # app.py (PART 3)

@app.route('/clients')
@login_required
def clients_page():
    q = request.args.get('q', '').strip()
    date_str = request.args.get('date', '').strip()

    query = Client.query

    if q:
        query = query.filter(
            (Client.name.ilike(f'%{q}%')) |
            (Client.bls.any(BL.bl_number.ilike(f'%{q}%')))
        )

    if date_str:
        try:
            date_obj = datetime.strptime(date_str, "%Y-%m-%d").date()
            query = query.join(BL).filter(db.func.date(BL.created_at) == date_obj)
        except ValueError:
            pass

    clients = query.order_by(Client.name).all()

    # ➜ ADD STATUS + TOTALS PER CLIENT
    client_data = []

    for c in clients:
        total_billed = sum((bl.amount_total or 0) for bl in c.bls)
        total_paid = sum((bl.amount_paid or 0) for bl in c.bls)
        unpaid = total_billed - total_paid

        if total_billed == 0:
            status = "no-bl"
        elif unpaid <= 0:
            status = "cleared"
        elif total_paid > 0:
            status = "part"
        else:
            status = "owing"

        client_data.append({
            "client": c,
            "total_billed": total_billed,
            "total_paid": total_paid,
            "unpaid": unpaid,
            "status": status
        })

    return render_template_string(
        CLIENTS_PAGE_HTML,
        clients=client_data,
        q=q,
        selected_date=date_str
    )

@app.route('/generate_receipt', methods=['GET', 'POST'])
@login_required
def generate_receipt():

    clients = Client.query.order_by(Client.name).all()
    receipt = None
    bls = []

    # -------------------------
    # LOAD BLs (GET)
    # -------------------------
    client_id = request.args.get('client_id', type=int)
    if client_id:
        bls = (
            db.session.query(
                BL,
                db.func.max(Receipt.created_at).label("last_receipted_date")
            )
            .outerjoin(ReceiptBL, ReceiptBL.bl_id == BL.id)
            .outerjoin(Receipt, Receipt.id == ReceiptBL.receipt_id)
            .filter(BL.client_id == client_id)
            .group_by(BL.id)
            .order_by(BL.created_at.asc())
            .all()
        )

    # -------------------------
    # CREATE RECEIPT (POST)
    # -------------------------
    if request.method == 'POST':

        # ----- Description handling -----
        desc_type = request.form.get('description_type')
        custom_desc = request.form.get('custom_description', '').strip()

        final_description = (
            custom_desc if desc_type == 'custom' and custom_desc else desc_type
        )

        # ----- Core fields -----
        client_id = int(request.form.get('client_id'))
        total_amount = float(request.form.get('amount') or 0)
        issued_by = request.form.get('issued_by')

        payment_type = request.form.get('payment_type')
        transaction_id = request.form.get('transaction_id')

        receipt = Receipt(
            client_id=client_id,
            amount=total_amount,
            method=payment_type,
            reference=transaction_id if payment_type != 'Cash' else None,
            description=f"{final_description} | Issued by: {issued_by}"
        )

        db.session.add(receipt)
        db.session.flush()  # get receipt.id safely

        # ----- Apply payments to BLs -----
        remaining = total_amount

        # ✅ FIX: cast BL IDs to integers (Postgres-safe)
        raw_bl_ids = request.form.getlist('bl_ids')

        # ✅ force integer casting
        raw_bl_ids = request.form.getlist('bl_ids')
        bl_ids = [int(x) for x in raw_bl_ids if x.isdigit()]

        if bl_ids:
            selected_bls = (
                BL.query
                .filter(BL.id.in_(bl_ids))
                .order_by(BL.created_at.asc())
                .all()
            )

            for bl in selected_bls:
                if remaining <= 0:
                    break

                unpaid = (bl.amount_total or 0) - (bl.amount_paid or 0)
                if unpaid <= 0:
                    continue

                applied = min(unpaid, remaining)
                bl.amount_paid = (bl.amount_paid or 0) + applied
                remaining -= applied

                db.session.add(
                    ReceiptBL(
                        receipt_id=receipt.id,
                        bl_id=bl.id,
                        amount_applied=applied
                    )
                )

        db.session.commit()

        return redirect(
            url_for('download_receipt_pdf', receipt_id=receipt.id)
        )

    # -------------------------
    # RENDER PAGE (GET)
    # -------------------------
    return render_template_string(
        RECEIPT_UI_HTML,
        clients=clients,
        bls=bls,
        receipt=receipt,
        selected_client_id=client_id
    )

@app.route('/receipts')
@login_required
def receipt_history():

    q = request.args.get('q', '').strip()
    date_str = request.args.get('date', '').strip()

    # BASE QUERY (must exist first)
    receipts = (
        Receipt.query
        .join(Client)
        .order_by(Receipt.created_at.desc())
    )

    # DATE FILTER
    if date_str:
        try:
            date_obj = datetime.strptime(date_str, "%Y-%m-%d").date()
            receipts = receipts.filter(
                db.func.date(Receipt.created_at) == date_obj
            )
        except ValueError:
            pass

    # TEXT / BL FILTER
    if q:
        receipts = (
            receipts
            .outerjoin(ReceiptBL)
            .outerjoin(BL)
            .filter(
                (Client.name.ilike(f"%{q}%")) |
                (Receipt.id.cast(db.String).ilike(f"%{q}%")) |
                (BL.bl_number.ilike(f"%{q}%"))
            )
            .distinct()
        )

    receipts = receipts.all()

    return render_template_string(
        RECEIPT_HISTORY_HTML,
        receipts=receipts,
        q=q,
        selected_date=date_str
    )

@app.route('/receipt/<int:receipt_id>/preview')
@login_required
def receipt_preview(receipt_id):

    receipt = Receipt.query.get_or_404(receipt_id)
    client = Client.query.get(receipt.client_id)

    bl_rows = (
        db.session.query(ReceiptBL, BL)
        .join(BL, ReceiptBL.bl_id == BL.id)
        .filter(ReceiptBL.receipt_id == receipt.id)
        .all()
    )

    return render_template_string(
        RECEIPT_PREVIEW_HTML,
        receipt=receipt,
        client=client,
        bl_rows=bl_rows,
        today=receipt.created_at.strftime("%d %b %Y")
    )
@app.route('/export/dashboard.csv')
@login_required
def export_dashboard_csv():
    import csv
    from io import StringIO

    output = StringIO()
    writer = csv.writer(output)

    writer.writerow([
        "Client",
        "Total Billed",
        "Total Paid",
        "Outstanding"
    ])

    for c in Client.query.all():
        billed = sum(bl.amount_total or 0 for bl in c.bls)
        paid = sum(bl.amount_paid or 0 for bl in c.bls)
        writer.writerow([c.name, billed, paid, billed - paid])

    response = make_response(output.getvalue())
    response.headers["Content-Disposition"] = "attachment; filename=dashboard.csv"
    response.headers["Content-Type"] = "text/csv"
    return response

@app.route('/receipts_home')
@login_required
def receipts_home():
    # kept a different route name to avoid accidental overrides
    return render_template_string(RECEIPTS_HOME_HTML)

@app.route('/receipt/pdf/<int:receipt_id>')
@login_required
def download_receipt_pdf(receipt_id):

    from reportlab.lib import colors

    receipt = Receipt.query.get_or_404(receipt_id)
    client = Client.query.get(receipt.client_id)

    buffer = BytesIO()
    c = canvas.Canvas(buffer, pagesize=A4)
    width, height = A4

    # ======================================================
    # BACKGROUND (DRAW FIRST)
    # ======================================================
    bg_path = os.path.join('static', 'new_receipt.png')
    if os.path.exists(bg_path):
        try:
            bg = ImageReader(bg_path)
            c.drawImage(bg, 0, 0, width=width, height=height)
        except Exception:
            pass

    # ======================================================
    # HEADER
    # ======================================================
    c.setFillColor(colors.black)
    c.setFont("Helvetica-Bold", 11)

    header_y = height - 205
    c.drawString(60, header_y, f"Receipt No: CBL-{receipt.id:06d}")
    c.drawRightString(
        width - 60,
        header_y,
        receipt.created_at.strftime("%d %b %Y")
    )

    # ======================================================
    # RECEIVED FROM + RECEIPT SUMMARY
    # ======================================================
    box_y = height - 310
    box_h = 95

    left_x = 60
    right_x = width / 2 + 10
    box_w = (width - 140) / 2

    teal = colors.HexColor("#7FD4D8")
    CARGOBLOC_TEAL = colors.HexColor("#9edfe0")

    # ----- RECEIVED FROM -----
    c.setFillColor(teal)
    c.rect(left_x, box_y + box_h - 26, box_w, 26, fill=1, stroke=0)
    c.setFillColor(colors.white)
    c.setFont("Helvetica-Bold", 11)
    c.drawString(left_x + 10, box_y + box_h - 18, "RECEIVED FROM")

    c.setStrokeColor(CARGOBLOC_TEAL)
    c.setLineWidth(1)
    c.rect(left_x, box_y, box_w, box_h, stroke=1, fill=0)
    c.setFillColor(colors.black)

    c.setFont("Helvetica-Bold", 11)
    c.drawString(left_x + 10, box_y + box_h - 45, client.name)

    c.setFont("Helvetica", 10)
    if client.phone:
        c.drawString(left_x + 10, box_y + box_h - 65, f"Phone: {client.phone}")

    if client.email:
        c.drawString(left_x + 10, box_y + box_h - 82, f"Email: {client.email}")

    # ----- RECEIPT SUMMARY -----
    c.setFillColor(teal)
    c.rect(right_x, box_y + box_h - 26, box_w, 26, fill=1, stroke=0)
    c.setFillColor(colors.white)
    c.setFont("Helvetica-Bold", 11)
    c.drawString(right_x + 10, box_y + box_h - 18, "RECEIPT SUMMARY")

    c.setStrokeColor(CARGOBLOC_TEAL)
    c.setLineWidth(1)
    c.rect(right_x, box_y, box_w, box_h, stroke=1, fill=0)
    c.setFillColor(colors.black)

    c.setFont("Helvetica", 10)
    c.drawString(right_x + 10, box_y + box_h - 45, "Transaction ID:")
    c.drawRightString(
        right_x + box_w - 10,
        box_y + box_h - 45,
        receipt.reference or "CASH PAYMENT"
    )

    c.drawString(right_x + 10, box_y + box_h - 65, "Currency:")
    c.drawRightString(right_x + box_w - 10, box_y + box_h - 65, "GHS")

    c.drawString(right_x + 10, box_y + box_h - 82, "Payment Type:")
    c.drawRightString(
        right_x + box_w - 10,
        box_y + box_h - 82,
        receipt.method or "—"
    )

    # ======================================================
    # PAYMENT BREAKDOWN TABLE
    # ======================================================
    col_w = [40, 200, 140, 100]
    table_w = sum(col_w)
    table_x = (width - table_w) / 2
    table_y = height - 380
    row_h = 28

    # ----- TITLE BAR -----
    c.setFillColor(teal)
    c.rect(table_x, table_y + row_h, table_w, 26, fill=1, stroke=0)

    c.setFillColor(colors.white)
    c.setFont("Helvetica-Bold", 11)
    c.drawString(table_x + 10, table_y + row_h + 8, "PAYMENT BREAKDOWN")

    # ----- HEADER ROW -----
    c.setFillColor(teal)
    c.rect(table_x, table_y, table_w, row_h, fill=1, stroke=0)

    c.setFillColor(colors.black)
    c.setFont("Helvetica-Bold", 10)

    headers = ["#", "BL NUMBER", "DESCRIPTION", "AMOUNT (GHS)"]
    x = table_x
    for i, h in enumerate(headers):
        c.drawString(x + 8, table_y + 9, h)
        x += col_w[i]

    # ----- CLEAN DESCRIPTION -----
    raw_desc = receipt.description or ""
    if "| Issued by:" in raw_desc:
        clean_desc = raw_desc.split("| Issued by:")[0].strip()
    else:
        clean_desc = raw_desc.strip() or "-"

    # ----- ROWS -----
    c.setFont("Helvetica", 10)
    y = table_y - row_h
    total = 0

    items = ReceiptBL.query.filter_by(receipt_id=receipt.id).all()

    for idx, item in enumerate(items, start=1):
        bl = BL.query.get(item.bl_id)
        total += item.amount_applied

        c.rect(table_x, y, table_w, row_h, stroke=1, fill=0)

        vx = table_x
        for w in col_w[:-1]:
            vx += w
            c.line(vx, y, vx, y + row_h)

        x = table_x
        c.drawString(x + 12, y + 9, str(idx))
        x += col_w[0]

        c.drawString(x + 8, y + 9, bl.bl_number)
        x += col_w[1]

        c.drawString(x + 8, y + 9, clean_desc)
        x += col_w[2]

        c.drawRightString(x + col_w[3] - 8, y + 9, f"{item.amount_applied:,.2f}")
        y -= row_h

    # ----- TOTAL ROW -----
    c.setFillColor(colors.HexColor("#E6F4F5"))
    c.rect(table_x, y, table_w, row_h, fill=1, stroke=1)

    c.setFillColor(colors.black)
    c.setFont("Helvetica-Bold", 11)
    c.drawString(table_x + col_w[0] + col_w[1] + 8, y + 9, "TOTAL")
    c.drawRightString(table_x + table_w - 8, y + 9, f"GHS {total:,.2f}")

    # ======================================================
    # FOOTER
    # ======================================================
    footer_y = 40
    c.setLineWidth(1.4)
    c.line(70, footer_y + 15, width - 70, footer_y + 15)

    if "Issued by:" in raw_desc:
        issued_by = raw_desc.split("Issued by:")[1].strip()
    else:
        issued_by = "-"

    c.setFont("Helvetica", 8)
    c.drawCentredString(
        width / 2,
        footer_y,
        f"Generated by: {issued_by} | CargoBloc System | {receipt.created_at.strftime('%d %b %Y')}"
    )

    c.showPage()
    c.save()
    buffer.seek(0)

    return send_file(
        buffer,
        as_attachment=True,
        download_name=f"Receipt_{receipt.id:06d}.pdf",
        mimetype="application/pdf"
    )
@app.route('/breakdown', methods=['GET', 'POST'])
@login_required
def breakdown():
    if request.method == 'POST':
        # Get JSON from JS
        data = request.get_json()  # expecting { company, container, vessel, voyage, blList: [...] }
        if not data:
            return "No data sent", 400
        return generate_breakdown_pdf(data)

    return render_template_string(BREAKDOWN_HTML)



from io import BytesIO
from datetime import datetime
import random
import re

from flask import render_template_string, request, send_file
from flask_login import login_required
from reportlab.pdfgen import canvas


# =========================================================
# AI ENGINE
# =========================================================
def smart_item_engine(items, total_weight):
    def fob(desc):
        desc = desc.lower()

        if "clothing" in desc:
            return 1.20
        if "paint" in desc:
            return 2.50
        if "mattress" in desc:
            return 12.00
        if "bed" in desc:
            return 45.00
        if "rice" in desc:
            return 3.00

        return 2.00

    enriched = []
    total_value = 0.0

    for item in items:
        qty = float(item.get("qty", 1))
        unit = fob(item.get("desc", ""))
        value = qty * unit
        total_value += value

        enriched.append({
        "desc": item.get("desc", ""),
        "qty": qty,
        "qty_unit": item.get("qty_unit", "PCS"),
        "manual_weight": item.get("manual_weight"),
        "unit": unit,
       "value": value
    })

    manual_total = sum(
        item["manual_weight"]
        for item in enriched
        if item.get("manual_weight") is not None
    )

    remaining_weight = max(total_weight - manual_total, 0)

    remaining_value_total = sum(
        item["value"]
        for item in enriched
        if item.get("manual_weight") is None
    )

    for item in enriched:
        if item.get("manual_weight") is not None:
         if float(item["manual_weight"]).is_integer():
           item["weight"] = int(item["manual_weight"])
         else:
           item["weight"] = round(item["manual_weight"], 2)
        else:
              share = item["value"] / remaining_value_total if remaining_value_total > 0 else 0
              calculated_weight = remaining_weight * share

        if calculated_weight.is_integer():
           item["weight"] = int(calculated_weight)
        else:
           item["weight"] = round(calculated_weight, 2)

    return enriched


# =========================================================
# HELPER: MULTILINE TEXT
# =========================================================

def draw_multiline(c, text, x, y, width, line_height=11):
    if not text:
        return y

    c.setFont("Helvetica", 9)

    words = str(text).split()
    lines = []
    line = ""

    for w in words:
        test_line = (line + " " + w).strip()

        if c.stringWidth(test_line, "Helvetica", 9) <= width:
            line = test_line
        else:
            if line:
                lines.append(line)
            line = w

    if line:
        lines.append(line)

    offset = 0
    for l in lines:
        c.drawString(x, y - offset, l)
        offset += line_height

    return y - offset
# =========================================================
# HELPER: HEX COLOR TO RGB
# =========================================================
def hex_to_rgb(hex_color):
    if not hex_color:
        hex_color = "#cfe8ff"

    hex_color = hex_color.strip().lstrip("#")

    if len(hex_color) != 6:
        hex_color = "cfe8ff"

    try:
        return tuple(int(hex_color[i:i+2], 16) / 255 for i in (0, 2, 4))
    except:
        return (0.81, 0.91, 1.0)


# =========================================================
# HOME ROUTE
# =========================================================
@app.route('/invoices_home')
@login_required
def invoices_home():
    return render_template_string(INVOICES_HOME_HTML)


# =========================================================
# GENERATE PREVIEW ROUTE
# =========================================================
@app.route('/generate_commercial_invoice', methods=['GET', 'POST'])
@login_required
def generate_commercial_invoice():
    if request.method == 'POST':

        shipper_name = request.form.get("shipper_name", "").strip()
        shipper_address = request.form.get("shipper_address", "").strip()
        consignee_name = request.form.get("consignee_name", "").strip()
        consignee_address = request.form.get("consignee_address", "").strip()
        container_no = request.form.get("container_no", "").strip()
        container_size = request.form.get("container_size", "").strip()
        theme_color = request.form.get("theme_color", "#cfe8ff").strip()

        total_weight_raw = request.form.get("total_weight", "0").strip()
        try:
            total_weight = float(total_weight_raw)
        except:
            total_weight = 0.0

        raw_items = request.form.get("freight_input", "")
        if not raw_items.strip():
            return "Error: No items provided"

        items = []
        for line in raw_items.split("\n"):
            line = line.strip()
            if not line:
                continue

            parts = line.split("-")
            desc = parts[0].strip()

            qty = 1
            qty_unit = "PCS"
            manual_weight = None

            if len(parts) > 1:
                qty_text = parts[1].strip().lower()

                qty_match = re.search(r'(\d+(?:\.\d+)?)', qty_text)
                if qty_match:
                     qty = float(qty_match.group(1))

                if "box" in qty_text:
                    qty_unit = "BOXES"
                elif "pcs" in qty_text or "piece" in qty_text:
                    qty_unit = "PCS"
                elif "kg" in qty_text:
                    qty_unit = "KG"
                elif "tote" in qty_text:
                    qty_unit = "TOTES"
                elif "bag" in qty_text:
                    qty_unit = "BAGS"
                elif "carton" in qty_text:
                    qty_unit = "CARTONS"

                weight_match = re.search(r'(\d+(?:\.\d+)?)\s*kg', qty_text)
                if weight_match:
                   base_weight = float(weight_match.group(1))
                   manual_weight = base_weight * qty

            items.append({
                "desc": desc,
                "qty": qty,
                "qty_unit": qty_unit,
                "manual_weight": manual_weight
            })

        data = smart_item_engine(items, total_weight)

        return render_template_string(
            PREVIEW_HTML,
            shipper_name=shipper_name,
            shipper_address=shipper_address,
            consignee_name=consignee_name,
            consignee_address=consignee_address,
            container_no=container_no,
            container_size=container_size,
            theme_color=theme_color,
            total_weight=total_weight,
            freight_input=raw_items,
            items=data
        )

    return render_template_string(COMMERCIAL_INVOICE_UI_HTML)


# =========================================================
# EXPORT PDF ROUTE
# =========================================================
@app.route('/export_invoice_pdf', methods=['POST'])
@login_required
def export_invoice_pdf():
    shipper_name = request.form.get("shipper_name", "").strip()
    shipper_address = request.form.get("shipper_address", "").strip()
    consignee_name = request.form.get("consignee_name", "").strip()
    consignee_address = request.form.get("consignee_address", "").strip()
    container_no = request.form.get("container_no", "").strip()
    container_size = request.form.get("container_size", "").strip()
    theme_color = request.form.get("theme_color", "#cfe8ff").strip()

    total_weight_raw = request.form.get("total_weight", "0").strip()
    try:
        total_weight = float(total_weight_raw)
    except:
        total_weight = 0.0

    raw_items = request.form.get("freight_input", "")
    if not raw_items.strip():
        return "Error: No freight items provided"

    items = []

    for line in raw_items.split("\n"):
        line = line.strip()
        if not line:
            continue

        parts = line.split("-")
        desc = parts[0].strip()

        qty = 1
        qty_unit = "PCS"
        manual_weight = None

        if len(parts) > 1:
            qty_text = parts[1].strip().lower()

            qty_match = re.search(r'(\d+(?:\.\d+)?)', qty_text)
            if qty_match:
                qty = float(qty_match.group(1))

            if "box" in qty_text:
                qty_unit = "BOXES"
            elif "pcs" in qty_text or "piece" in qty_text:
                qty_unit = "PCS"
            elif "kg" in qty_text:
                qty_unit = "KG"
            elif "tote" in qty_text:
                qty_unit = "TOTES"
            elif "bag" in qty_text:
                qty_unit = "BAGS"
            elif "carton" in qty_text:
                qty_unit = "CARTONS"

            weight_match = re.search(r'(\d+(?:\.\d+)?)\s*kg', qty_text)
            if weight_match:
               base_weight = float(weight_match.group(1))
               manual_weight = base_weight * qty

        items.append({
            "desc": desc,
            "qty": qty,
            "qty_unit": qty_unit,
            "manual_weight": manual_weight
        })
      

    data = smart_item_engine(items, total_weight)

    invoice_no = f"INV-{datetime.now().strftime('%Y%m%d')}-{random.randint(100,999)}"
    invoice_date = datetime.now().strftime("%d %B %Y")
    freight_charge = 300.00
    r, g, b = hex_to_rgb(theme_color)

    buffer = BytesIO()
    p = canvas.Canvas(buffer)

    # =========================
    # HEADER
    # =========================
    p.setFont("Helvetica-Bold", 18)
    p.drawCentredString(300, 805, "COMMERCIAL INVOICE")

    p.setFont("Helvetica", 9)
    p.drawRightString(570, 790, f"Invoice No: {invoice_no}")
    p.drawRightString(570, 776, f"Date: {invoice_date}")

    # =========================
    # SHIPPER / CONSIGNEE BOXES
    # =========================
    box_top = 755
    box_height = 75

    # Shipper box
    p.setFillColorRGB(r, g, b)
    p.rect(35, box_top - box_height, 240, box_height, fill=1, stroke=1)

    p.setFillColorRGB(0, 0, 0)
    p.setFont("Helvetica-Bold", 10)
    p.drawString(45, box_top - 15, "SHIPPER")

    p.setFont("Helvetica", 9)
    p.drawString(45, box_top - 30, shipper_name)

    y_shipper = box_top - 44
    y_shipper = draw_multiline(p, shipper_address, 45, y_shipper, 220)

    # Consignee box
    p.setFillColorRGB(r, g, b)
    p.rect(315, box_top - box_height, 240, box_height, fill=1, stroke=1)

    p.setFillColorRGB(0, 0, 0)
    p.setFont("Helvetica-Bold", 10)
    p.drawString(325, box_top - 15, "CONSIGNEE")

    p.setFont("Helvetica", 9)
    p.drawString(325, box_top - 30, consignee_name)

    y_consignee = box_top - 44
    y_consignee = draw_multiline(p, consignee_address, 325, y_consignee, 220)

    # =========================
    # META SECTION
    # =========================
    meta_y = 655

    p.setFont("Helvetica-Bold", 9)
    p.drawString(40, meta_y, "CONTAINER NO:")
    p.setFont("Helvetica", 9)
    p.drawString(125, meta_y, container_no)

    p.setFont("Helvetica-Bold", 9)
    p.drawString(210, meta_y, "SIZE:")
    p.setFont("Helvetica", 9)
    p.drawString(245, meta_y, container_size)

    p.setFont("Helvetica-Bold", 9)
    p.drawString(310, meta_y, "TOTAL WEIGHT:")
    p.setFont("Helvetica", 9)
    p.drawString(405, meta_y, f"{total_weight:,.2f} KG")

    # =========================
    # TABLE SETTINGS
    # =========================
    total_value = 0.0
    row_height = 20
    y_min = 80

    item_x = 45
    desc_x = 85
    qty_x = 330
    unit_x = 410
    amount_x = 480
    weight_x = 555

    table_left = 40
    table_right = 560

    def draw_table_header(current_y):
        p.setFillColorRGB(r, g, b)
        p.rect(table_left, current_y - 14, table_right - table_left, 18, fill=1, stroke=0)

        p.setFillColorRGB(0, 0, 0)
        p.setFont("Helvetica-Bold", 9)

        p.drawString(item_x, current_y, "ITEM")
        p.drawString(desc_x, current_y, "DESCRIPTION")
        p.drawRightString(qty_x, current_y, "QTY")
        p.drawRightString(unit_x, current_y, "UNIT PRICE")
        p.drawRightString(amount_x, current_y, "AMOUNT")
        p.drawRightString(weight_x, current_y, "WEIGHT")

        p.setStrokeColorRGB(0.70, 0.80, 0.90)
        p.line(table_left, current_y - 8, table_right, current_y - 8)

    # =========================
    # FIRST TABLE HEADER
    # =========================
    table_y = meta_y - 35
    draw_table_header(table_y)

    y = table_y - 25
    p.setFont("Helvetica", 8)

    # =========================
    # TABLE ROWS
    # =========================
    for i, item in enumerate(data):

        amount = item["qty"] * item["unit"]
        total_value += amount

        if y < y_min:
            p.showPage()

            # reset font and colors after new page
            p.setFillColorRGB(0, 0, 0)
            p.setStrokeColorRGB(0, 0, 0)

            table_y = 760
            draw_table_header(table_y)

            y = table_y - 25
            p.setFont("Helvetica", 8)

        qty = item["qty"]
        qty_display = int(qty) if float(qty).is_integer() else qty

        desc = item["desc"] or ""
        if len(desc) > 42:
            desc = desc[:42] + "..."

        # Alternating row background
        if i % 2 == 0:
            p.setFillColorRGB(0.96, 0.98, 1.0)
            p.rect(table_left, y - 10, table_right - table_left, 16, fill=1, stroke=0)

        p.setFillColorRGB(0, 0, 0)

        qty_text = f"{qty_display} {item.get('qty_unit', 'PCS')}"

        p.drawString(item_x, y, str(i + 1))
        p.drawString(desc_x, y, desc)

        p.drawRightString(qty_x, y, qty_text)
        p.drawRightString(unit_x, y, f"{item['unit']:.2f}")
        p.drawRightString(amount_x, y, f"{amount:,.2f}")
        p.drawRightString(weight_x, y, f"{item['weight']:.2f} KG")

        p.setStrokeColorRGB(0.85, 0.90, 0.95)
        p.line(table_left, y - 8, table_right, y - 8)

        y -= row_height

    # =========================
    # TOTALS
    # =========================
    cf_total = total_value + freight_charge

    y -= 20
    p.line(350, y, 590, y)

    y -= 25
    p.setFont("Helvetica-Bold", 10)

    p.drawRightString(500, y, "FOB VALUE:")
    p.drawRightString(555, y, f"USD {total_value:,.2f}")

    y -= 18
    p.drawRightString(500, y, "FREIGHT:")
    p.drawRightString(555, y, f"USD {freight_charge:,.2f}")

    y -= 18
    p.drawRightString(500, y, "C & F VALUE:")
    p.drawRightString(555, y, f"USD {cf_total:,.2f}")

    y -= 18
    p.drawRightString(500, y, "TOTAL WEIGHT:")
    p.drawRightString(555, y, f"{total_weight:,.2f} KG")

    # Footer
    p.setStrokeColorRGB(0.75, 0.75, 0.75)
    p.line(40, 45, 590, 45)

    p.setFont("Helvetica-Oblique", 8)
    p.drawCentredString(300, 30, "CARGOBLOC LOGISTICS - Vision to Reality")

    p.save()

    buffer.seek(0)

    return send_file(
    buffer,
    as_attachment=True,
    download_name="commercial_invoice.pdf",
    mimetype="application/pdf"
    )

# -----------------------
# PDF helpers
# -----------------------
def draw_multiline(c, text, x, y, width, line_height=10):
    if not text:
        return y

    c.setFont("Helvetica", 8)

    words = text.split()
    line = ""
    current_y = y

    for word in words:
        test_line = f"{line} {word}".strip()

        if c.stringWidth(test_line, "Helvetica", 8) <= width:
            line = test_line
        else:
            c.drawString(x, current_y, line)
            current_y -= line_height
            line = word

    if line:
        c.drawString(x, current_y, line)
        current_y -= line_height

    return current_y

def create_bl_pdf(client, bls, pdf_path):
    c = canvas.Canvas(pdf_path, pagesize=A4)
    width, height = A4

    # === Letterhead background ===
    letterhead_path = os.path.join('static', 'letterhead.png')
    bg = None
    if os.path.exists(letterhead_path):
        try:
            bg = ImageReader(letterhead_path)
            c.drawImage(bg, 0, 0, width=width, height=height)
        except Exception as e:
            print("⚠ Letterhead load failed:", e)

    # === Margins ===
    TOP_MARGIN = 250
    BOTTOM_MARGIN = 100
    LEFT_MARGIN = 60
    RIGHT_MARGIN = width - 60

    # === Header ===
    c.setFont("Helvetica-Bold", 16)
    c.drawString(LEFT_MARGIN, height - TOP_MARGIN + 30, "Client Bill")
    c.setFont("Helvetica", 10)
    c.drawRightString(RIGHT_MARGIN, height - TOP_MARGIN + 34,
                      f"Generated: {datetime.now().strftime('%d %b %Y, %I:%M %p')}")

    # === Client Info ===
    y = height - TOP_MARGIN - 15
    c.setFont("Helvetica-Bold", 11)
    c.drawString(LEFT_MARGIN, y, f"Client: {client.name}")
    y -= 15
    c.setFont("Helvetica", 10)
    c.drawString(LEFT_MARGIN, y, f"Email: {client.email or '-'}")
    y -= 15
    c.drawString(LEFT_MARGIN, y, f"Phone: {client.phone or '-'}")

    # === Table Header ===
    y -= 30
    table_x = LEFT_MARGIN
    table_width = RIGHT_MARGIN - LEFT_MARGIN
    row_height = 20
    col_bl = table_x
    col_total = table_x + 220
    col_paid = table_x + 320
    col_unpaid = table_x + 400

    # Header background
    c.setFillColorRGB(0.90, 0.96, 1)
    c.rect(table_x, y - 4, table_width, row_height, stroke=0, fill=1)
    c.setFillColorRGB(0, 0, 0)
    c.setFont("Helvetica-Bold", 10)
    c.drawString(col_bl + 5, y + 2, "BL Number")
    c.drawString(col_total + 5, y + 2, "Total ")
    c.drawString(col_paid + 5, y + 2, "Paid ")
    c.drawString(col_unpaid + 5, y + 2, "Unpaid ")
    c.setStrokeColorRGB(0.7, 0.7, 0.7)
    c.line(table_x, y - 5, RIGHT_MARGIN, y - 5)
    y -= row_height

    total_billed = total_paid = 0
    c.setFont("Helvetica", 10)

    # === BL Rows ===
    for idx, bl in enumerate(bls):
        if y < BOTTOM_MARGIN + 60:
            c.showPage()
            if bg:
                c.drawImage(bg, 0, 0, width=width, height=height)
            y = height - TOP_MARGIN
            c.setFillColorRGB(0.90, 0.96, 1)
            c.rect(table_x, y - 4, table_width, row_height, stroke=0, fill=1)
            c.setFillColorRGB(0, 0, 0)
            c.setFont("Helvetica-Bold", 10)
            c.drawString(col_bl + 5, y + 2, "BL Number")
            c.drawString(col_total + 5, y + 2, "Total (₵)")
            c.drawString(col_paid + 5, y + 2, "Paid (₵)")
            c.drawString(col_unpaid + 5, y + 2, "Unpaid (₵)")
            c.line(table_x, y - 5, RIGHT_MARGIN, y - 5)
            y -= row_height
            c.setFont("Helvetica", 10)

        # Alternating background
        if idx % 2 == 1:
            c.setFillColorRGB(0.98, 0.98, 0.98)
            c.rect(table_x, y - 4, table_width, row_height, stroke=0, fill=1)

        # Row text
        c.setFillColorRGB(0, 0, 0)
        c.drawString(col_bl + 5, y + 2, str(bl.bl_number))
        c.drawRightString(col_total + 60, y + 2, f"{(bl.amount_total or 0):,.2f}")
        c.drawRightString(col_paid + 60, y + 2, f"{(bl.amount_paid or 0):,.2f}")
        c.drawRightString(col_unpaid + 60, y + 2, f"{(bl.amount_unpaid or 0):,.2f}")

        total_billed += bl.amount_total or 0
        total_paid += bl.amount_paid or 0
        y -= row_height

        # Divider lines
        c.setStrokeColorRGB(0.9, 0.9, 0.9)
        c.line(table_x, y, RIGHT_MARGIN, y)
        c.setStrokeColorRGB(0.85, 0.85, 0.85)
        for x in [col_total - 10, col_paid - 10, col_unpaid - 10, RIGHT_MARGIN]:
            c.line(x, y, x, y + row_height)

    # === Totals Row ===
    total_unpaid = total_billed - total_paid
    if y < BOTTOM_MARGIN + 60:
        c.showPage()
        if bg:
            c.drawImage(bg, 0, 0, width=width, height=height)
        y = height - TOP_MARGIN

    c.setFillColorRGB(0.85, 0.93, 1)
    c.rect(table_x, y - 4, table_width, row_height, stroke=0, fill=1)
    c.setFillColorRGB(0, 0, 0)
    c.setFont("Helvetica-Bold", 11)
    c.drawString(col_bl + 5, y + 2, "Totals")
    c.drawRightString(col_total + 60, y + 2, f"{total_billed:,.2f}")
    c.drawRightString(col_paid + 60, y + 2, f"{total_paid:,.2f}")
    c.drawRightString(col_unpaid + 60, y + 2, f"{total_unpaid:,.2f}")

    c.setStrokeColorRGB(0.75, 0.75, 0.75)
    for x in [col_total - 10, col_paid - 10, col_unpaid - 10, RIGHT_MARGIN]:
        c.line(x, y, x, y + row_height)

    # === Date Range Summary (tiny gray italic, below totals) ===
    if bls:
        dates = [bl.created_at for bl in bls if getattr(bl, "created_at", None)]
        if dates:
            start = min(dates).strftime("%d %b %Y")
            end = max(dates).strftime("%d %b %Y")
            y -= 12
            c.setFont("Helvetica-Oblique", 7)
            c.setFillColorRGB(0.75, 0.75, 0.75)
            c.drawString(LEFT_MARGIN + 5, y - 4, f"Entries created between {start} and {end}")

    # === Footer ===
    c.setFont("Helvetica-Oblique", 9)
    c.setFillColorRGB(0.25, 0.25, 0.25)
    c.drawCentredString(width / 2, 40, "CARGOBLOC LOGISTICS — Vision to Reality")

    c.save()


from io import BytesIO
from datetime import datetime

from flask import send_file
from reportlab.lib.pagesizes import A4
from reportlab.pdfgen import canvas
from reportlab.pdfbase.pdfmetrics import stringWidth


def parse_weight(value):
    try:
        value = str(value).replace(",", "").replace("KG", "").replace("kg", "").strip()
        return float(value)
    except Exception:
        return 0.0


def split_text(text, max_width, font="Helvetica", size=10):
    text = str(text).strip()
    if not text:
        return ["-"]

    words = text.split()
    lines = []
    current = ""

    for word in words:
        test = current + (" " if current else "") + word
        if stringWidth(test, font, size) <= max_width:
            current = test
        else:
            if current:
                lines.append(current)
            current = word

    if current:
        lines.append(current)

    return lines or ["-"]


def truncate_text(text, max_width, font="Helvetica", size=10):
    text = str(text).strip()
    if not text:
        return "-"

    if stringWidth(text, font, size) <= max_width:
        return text

    while text and stringWidth(text + "...", font, size) > max_width:
        text = text[:-1]

    return text + "..." if text else "..."


def draw_lines(c, lines, x, y, line_height=12):
    for i, line in enumerate(lines):
        c.drawString(x, y - (i * line_height), line)


def generate_breakdown_pdf(data):
    buffer = BytesIO()
    c = canvas.Canvas(buffer, pagesize=A4)
    width, height = A4

    # =========================
    # PAGE / TABLE SETTINGS
    # =========================
    left_margin = 60
    right_margin = 60
    table_x = left_margin
    table_width = width - left_margin - right_margin

    header_h = 25
    line_height = 12

    # Balanced columns: BL | CONSIGNEE | DESCRIPTION | PKG | WEIGHT
    col_widths = [115, 130, 160, 25, 50]
    col_x = [table_x]
    for w in col_widths[:-1]:
        col_x.append(col_x[-1] + w)

    headers = ["BL NO", "CONSIGNEE", "DESCRIPTION", "PKG", "WEIGHT"]

    def draw_table_header(y_pos):
        c.setFillColorRGB(0.15, 0.35, 0.65)
        c.rect(table_x, y_pos, table_width, header_h, fill=1, stroke=0)

        c.setFillColorRGB(1, 1, 1)
        c.setFont("Helvetica-Bold", 10)
        for i, h in enumerate(headers):
            c.drawString(col_x[i] + 5, y_pos + 8, h)

        c.setFillColorRGB(0, 0, 0)

    # =========================
    # HEADER
    # =========================
    c.setFont("Helvetica-Bold", 24)
    c.drawCentredString(width / 2, height - 80, data.get("company", "CARGOBLOC LOGISTICS"))

    c.setFont("Helvetica-Bold", 14)
    c.drawString(60, height - 110, "CARGO BREAKDOWN")

    c.setFont("Helvetica", 10)
    c.drawRightString(width - 60, height - 90, datetime.now().strftime("%d %b %Y"))

    # =========================
    # INFO SECTION
    # =========================
    y = height - 130

    def draw_info(label, value):
        nonlocal y
        c.setFont("Helvetica-Bold", 10)
        c.drawString(60, y, label)
        c.setFont("Helvetica-Bold", 10)
        c.drawString(144, y, str(value))
        y -= 18

    draw_info("CONTAINER NO:", data.get("container", "-"))
    draw_info("VESSEL:", data.get("vessel", "-"))
    draw_info("VOYAGE NO:", data.get("voyage", "-"))

    # =========================
    # TABLE START
    # =========================
    table_top = y - 80
    draw_table_header(table_top)

    y = table_top - header_h
    total_weight = 0.0

    bl_list = data.get("blList", [])

    for i, bl in enumerate(bl_list):
        bl_no = bl.get("bl", "-")
        consignee = bl.get("consignee", "-")
        description = bl.get("description", "-")
        pkg = bl.get("pkg", "-")
        weight = bl.get("weight", "-")

        consignee_lines = split_text(consignee, col_widths[1] - 10, font="Helvetica", size=10)
        description_lines = split_text(description, col_widths[2] - 10, font="Helvetica", size=10)

        # Limit extreme rows so they do not grow forever
        max_consignee_lines = 5
        max_description_lines = 6

        if len(consignee_lines) > max_consignee_lines:
            consignee_lines = consignee_lines[:max_consignee_lines]
            consignee_lines[-1] = truncate_text(consignee_lines[-1], col_widths[1] - 10)

        if len(description_lines) > max_description_lines:
            description_lines = description_lines[:max_description_lines]
            description_lines[-1] = truncate_text(description_lines[-1], col_widths[2] - 10)

        max_lines = max(len(consignee_lines), len(description_lines), 1)
        row_h = max(25, max_lines * line_height + 10)

        # Page break
        if y - row_h < 80:
            c.showPage()
            c.setFont("Helvetica", 10)
            y = height - 100
            draw_table_header(y)
            y -= header_h

        # Alternate row shading
        if i % 2 == 0:
            c.setFillColorRGB(0.95, 0.97, 1)
            c.rect(table_x, y - row_h, table_width, row_h, fill=1, stroke=0)
            c.setFillColorRGB(0, 0, 0)

        # Grid rectangle
        c.setLineWidth(0.5)
        c.rect(table_x, y - row_h, table_width, row_h)

        for x_line in col_x[1:]:
            c.line(x_line, y - row_h, x_line, y)

        # Text placement
        text_y = y - 15
        c.setFont("Helvetica", 10)

        # BL NO
        c.drawString(col_x[0] + 5, text_y, str(bl_no))

        # CONSIGNEE
        draw_lines(c, consignee_lines, col_x[1] + 5, text_y, line_height=line_height)

        # DESCRIPTION
        draw_lines(c, description_lines, col_x[2] + 5, text_y, line_height=line_height)

        # PKG
        c.drawString(col_x[3] + 5, text_y, str(pkg))

        # WEIGHT
        c.drawRightString(col_x[4] + col_widths[4] - 12, text_y, str(weight))

        # Total weight
        total_weight += parse_weight(weight)

        y -= row_h

    # =========================
    # TOTAL
    # =========================
    c.setLineWidth(0.8)
    c.line(table_x, y, table_x + table_width, y)

    y -= 20
    c.setFont("Helvetica-Bold", 11)
    c.drawRightString(table_x + table_width, y, f"TOTAL WEIGHT: {int(total_weight)} KG")

    # =========================
    # FOOTER
    # =========================
    c.setFont("Helvetica-Oblique", 9)
    c.drawCentredString(width / 2, 40, data.get("company", "CARGOBLOC LOGISTICS"))

    c.save()
    buffer.seek(0)

    return send_file(
        buffer,
        as_attachment=True,
        download_name=f"Breakdown_{datetime.now().strftime('%Y%m%d_%H%M%S')}.pdf",
        mimetype="application/pdf"
    )

# -----------------------
# HTML TEMPLATES
# -----------------------
LOGIN_HTML = """<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<title>Welcome Aboard CargoBloc</title>
<link href="https://fonts.googleapis.com/css2?family=Poppins:wght@400;600&display=swap" rel="stylesheet">
<style>
  body {
    margin: 0;
    height: 100vh;
    display: flex;
    justify-content: center;
    align-items: center;
    font-family: 'Poppins', sans-serif;
    background: url('{{ url_for('static', filename='login_bg.png') }}') no-repeat center center fixed;
    background-size: cover;
    animation: fadeIn 1s ease-in-out;
  }
  @keyframes fadeIn {
    from { opacity: 0; }
    to { opacity: 1; }
  }
  .overlay {
    position: fixed; inset: 0;
    backdrop-filter: blur(8px);
    background: rgba(255,255,255,0.25);
  }
  .login-card {
    position: relative; z-index: 1;
    background: rgba(255,255,255,0.20);
    backdrop-filter: blur(15px);
    border-radius: 16px;
    box-shadow: 0 8px 30px rgba(0,0,0,0.25);
    padding: 34px 38px;
    width: 360px;
    color: #fff;
    text-align: center;
  }
  h2 {
    margin: 0 0 18px;
    font-weight: 600;
    letter-spacing: .5px;
    color: white;
  }
  h2 span {
    display: block;
    color: #00AEEF;
    margin-top: 5px;
  }
  .err {
    text-align: left;
    background: rgba(220,38,38,0.12);
    border-left: 3px solid #dc2626;
    color: #991b1b;
    padding: 10px 12px;
    border-radius: 8px;
    font-size: 13px;
    margin-bottom: 10px;
  }
  input[type="text"], input[type="password"] {
    width: 100%;
    padding: 12px;
    border: none;
    border-radius: 8px;
    background: rgba(255,255,255,0.88);
    color: #222;
    margin: 8px 0;
    outline: none;
  }
  .pw-row {
    display: grid; grid-template-columns: 1fr auto; gap: 8px; align-items: center;
  }
  .toggle {
    background: rgba(255,255,255,0.88);
    border: none;
    border-radius: 8px;
    padding: 10px 12px;
    cursor: pointer;
    color: #222;
  }
  .row {
    display: flex;
    justify-content: space-between;
    align-items: center;
    margin-top: 6px;
  }
  .row label {
    font-size: 13px;
    display: flex;
    align-items: center;
    gap: 6px;
  }
  .row a {
    color: #93c5fd;
    text-decoration: none;
    font-size: 13px;
  }
  .row a:hover {
    text-decoration: underline;
  }
  button[type="submit"] {
    width: 100%;
    padding: 12px;
    background: linear-gradient(135deg, #007BFF, #00AEEF);
    border: none;
    border-radius: 8px;
    color: white;
    font-weight: 600;
    margin-top: 12px;
    cursor: pointer;
    transition: transform .2s ease;
  }
  button[type="submit"]:hover { transform: translateY(-1px); }
  footer {
    position: fixed;
    bottom: 14px;
    left: 0;
    right: 0;
    text-align: center;
    color: #93c5fd;
    font-size: 12px;
  }
</style>
</head>
<body>
<div class="overlay"></div>
<div class="login-card">
  <h2>Welcome Aboard <span>CargoBloc</span></h2>

  {% if error %}
    <div class="err">{{ error }}</div>
  {% endif %}

  <form method="post" autocomplete="off">
    <div class="field">
      <label for="username" style="font-size:13px;">Username</label>
      <input id="username" name="username" type="text" placeholder="Enter username" required>
    </div>

    <div class="field">
      <label for="password" style="font-size:13px;">Password</label>
      <div class="pw-row">
        <input id="password" name="password" type="password" placeholder="Enter password" required>
        <button type="button" class="toggle" onclick="
          const p=document.getElementById('password');
          p.type = (p.type==='password') ? 'text' : 'password';
          this.textContent = (p.type==='password') ? 'Show' : 'Hide';
        ">Show</button>
      </div>
    </div>

    <div class="row">
      <label><input type="checkbox" name="remember"> Remember me</label>
      <a href="{{ url_for('forgot_password') }}">Forgot Password?</a>
    </div>

    <button type="submit">Sign In</button>
  </form>
</div>

<footer>© 2025 CargoBloc Logistics</footer>
</body>
</html>"""

HOME_HTML = """<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<title>CARGOBLOC — Dashboard</title>
<link href="https://fonts.googleapis.com/css2?family=Poppins:wght@300;400;600;700&display=swap" rel="stylesheet">
<style>
:root{
  --deep-blue:#2563eb;
  --accent:#00AEEF;
  --green:#16a34a;
  --red:#dc2626;
  --text:#0b1220;
}
html,body{margin:0;height:100%;font-family:'Poppins',sans-serif;color:var(--text);}
body{
  background:url('{{ url_for('static', filename='homepage_bg.png') }}') no-repeat center center fixed;
  background-size:cover;
}
.page-wrap{
  max-width:1200px;
  margin:36px auto;
  display:grid;
  grid-template-columns:220px 1fr;
  gap:20px;
  padding:18px;
}

/* SIDEBAR */
.sidebar{
  background:#cfeaff;
  border-radius:12px;
  padding:18px;
  height:calc(100vh - 100px);
  display:flex;
  flex-direction:column;
  gap:18px;
}
.brand{display:flex;gap:12px;align-items:center;}
.brand img{height:44px;border-radius:6px;}
.brand h3{margin:0;font-size:16px;font-weight:700;}
.nav a{
  display:block;
  padding:10px 12px;
  border-radius:8px;
  text-decoration:none;
  font-weight:600;
  color:var(--text);
}
.nav a.active{background:rgba(255,255,255,0.3);}
.sidebar small{margin-top:auto;font-size:13px;}

/* MAIN */
.main{
  background:rgba(255,255,255,0.92);
  border-radius:12px;
  padding:22px;
  position:relative;
}
.watermark{
  position:absolute;
  left:50%;
  top:42%;
  transform:translate(-50%,-50%);
  opacity:0.05;
  width:520px;
  pointer-events:none;
}
header.top h1{margin:0;color:var(--deep-blue);}

/* CARDS */
.cards{display:flex;gap:12px;margin-bottom:18px;flex-wrap:wrap;}
.card{
  background:#fff;
  border-radius:12px;
  padding:16px;
  box-shadow:0 6px 18px rgba(0,0,0,0.05);
  flex:1;
}
.card h4{margin:0 0 8px;font-size:13px;color:#6b7280;}
.card .value{font-size:20px;font-weight:700;}

/* ADD CLIENT */
.add-client form{
  display:grid;
  grid-template-columns:1fr 1fr;
  gap:10px;
}
.add-client input,
.add-client textarea{
  padding:10px;
  border-radius:10px;
  border:1px solid #e5e7eb;
  background:rgba(255,255,255,0.85);
}
.add-client textarea{grid-column:1 / -1;}
.add-client button{
  grid-column:1 / -1;
  padding:12px;
  border:none;
  border-radius:12px;
  background:linear-gradient(135deg,var(--deep-blue),var(--accent));
  color:#fff;
  font-weight:600;
  cursor:pointer;
}

/* BUTTONS */
.btn{
  display:inline-flex;
  align-items:center;
  padding:10px 16px;
  border-radius:12px;
  font-weight:600;
  text-decoration:none;
  color:#fff;
  background:linear-gradient(135deg,var(--deep-blue),var(--accent));
}

/* BADGES */
.badge{
  display:inline-block;
  padding:4px 10px;
  border-radius:999px;
  font-size:12px;
  font-weight:600;
}
.badge.green{background:#dcfce7;color:#166534;}
.badge.red{background:#fee2e2;color:#991b1b;}

/* SMALL STATS ROW */
.small-stats{display:flex;gap:12px;flex-wrap:wrap;margin-top:12px;}
.small-stat{background:#fff;padding:10px;border-radius:10px;min-width:120px;box-shadow:0 4px 12px rgba(0,0,0,0.04);}
.small-stat .n{font-weight:700;font-size:16px;}
.small-stat .lbl{font-size:12px;color:#6b7280;}

/* FOOTER */
footer{text-align:center;color:#6b7280;font-size:13px;margin-top:18px;}
</style>
</head>

<body>
<div class="page-wrap">

<!-- SIDEBAR -->
<aside class="sidebar">
  <div class="brand">
    <img src="{{ url_for('static', filename='logo.png') }}">
    <div>
      <h3>CargoBloc</h3>
      <div style="font-size:12px;">Logistics Suite</div>
    </div>
  </div>

  <nav class="nav">
    <a class="active">Dashboard</a>
    <a href="{{ url_for('clients_page') }}">Clients</a>
    <a href="{{ url_for('receipts_home') }}">Receipts</a>
    <a href="{{ url_for('house_bl') }}">House BLs</a>
    <a href="{{ url_for('breakdown') }}">Breakdown</a>
    <a href="{{ url_for('invoices_home') }}">
    <i class="fas fa-file-invoice"></i>
    <span>Invoice</span>
    </a>
    <a href="{{ url_for('logout') }}">Logout</a>
  </nav>

  <small>Last login: Today</small>
</aside>

<!-- MAIN -->
<main class="main">
<img src="{{ url_for('static', filename='logo.png') }}" class="watermark">

<header class="top">
  <h1>Dashboard</h1>
</header>

<!-- STATS -->
<section class="cards">
  <div class="card">
    <h4>Total Billed</h4>
    <div class="value">₵{{ '%.2f'|format(total_billed) }}</div>
  </div>
  <div class="card">
    <h4>Total Paid</h4>
    <div class="value" style="color:var(--green);">
      ₵{{ '%.2f'|format(total_paid) }}
    </div>
  </div>
  <div class="card">
    <h4>Outstanding</h4>
    <div class="value" style="color:var(--red);">
      ₵{{ '%.2f'|format(total_unpaid) }}
    </div>
    {% if total_unpaid > 0 %}
      <span class="badge red">Overdue Risk</span>
    {% else %}
      <span class="badge green">Cleared</span>
    {% endif %}
  </div>
</section>

<!-- small quick metrics -->
<div class="small-stats">
  <div class="small-stat">
    <div class="n">{{ receipts_today }}</div>
    <div class="lbl">Receipts Today</div>
  </div>
  <div class="small-stat">
    <div class="n">{{ receipts_7 }}</div>
    <div class="lbl">Receipts (7d)</div>
  </div>
  <div class="small-stat">
    <div class="n">{{ receipts_30 }}</div>
    <div class="lbl">Receipts (30d)</div>
  </div>
  <div class="small-stat">
    <div class="n">{{ cleared }}</div>
    <div class="lbl">BLs Cleared</div>
  </div>
  <div class="small-stat">
    <div class="n">{{ owing }}</div>
    <div class="lbl">BLs Owing</div>
  </div>
</div>

<section style="display:flex;gap:18px;flex-wrap:wrap;margin-top:14px;">

<!-- LEFT -->
<div style="flex:0.45;min-width:320px;">
  <div class="card add-client">
    <h4>Add New Client</h4>
    <form method="post" action="{{ url_for('add_client') }}">
      <input name="name" placeholder="Client Name" required>
      <input name="email" placeholder="Email">
      <input name="phone" placeholder="Phone">
      <textarea name="notes" placeholder="Notes"></textarea>
      <button type="submit">Add Client</button>
    </form>
  </div>
</div>

<!-- RIGHT -->
<div style="flex:0.5;min-width:320px;">
  <div class="card" style="margin-bottom:12px;">
    <h4>Quick Actions</h4>
    <a href="{{ url_for('home') }}" class="btn">Refresh Dashboard</a>
  </div>

  <div class="card">
    <h4>Recent Activity</h4>
    <div style="font-size:13px;color:#6b7280;">
      {% for a in activities[:6] %}
        <div style="padding:8px 0;border-bottom:1px dashed #eef6ff;">
          {{ a.text }}
          {% if a.time %}
          <div style="font-size:11px;color:#9ca3af;">
            {{ a.time.strftime("%d %b %Y %H:%M") }}
          </div>
          {% endif %}
        </div>
      {% else %}
        <div>No recent activity</div>
      {% endfor %}
    </div>
  </div>
</div>

</section>

<footer>©️ 2026 CargoBloc Logistics — Vision to reality</footer>
</main>
</div>
</body>
</html>"""

CLIENT_HTML = """<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<title>Client Overview — {{ client.name }}</title>
<link href="https://fonts.googleapis.com/css2?family=Poppins:wght@400;600&display=swap" rel="stylesheet">
<style>
  :root{
    --blue:#2563eb;
    --accent:#00AEEF;
    --green:#16a34a;
    --red:#dc2626;
    --muted:#6b7280;
  }
  html,body{height:100%;margin:0;font-family:'Poppins',sans-serif;color:#0b1220;background:url('{{ url_for('static', filename='port_bg.png') }}') no-repeat center center fixed;background-size:cover;}
  .container{position:relative;max-width:980px;margin:40px auto;background:rgba(255,255,255,0.92);border-radius:14px;padding:22px;box-shadow:0 10px 30px rgba(0,0,0,0.12);overflow:hidden;}
  .watermark{position:absolute;top:50%;left:50%;transform:translate(-50%,-50%);opacity:0.06;pointer-events:none;z-index:0}
  .watermark img{width:420px;height:auto}
  h1{color:var(--blue);text-align:center;margin:6px 0 4px;font-size:22px;z-index:2;position:relative}
  p.meta{text-align:center;color:var(--muted);margin:0 0 16px;z-index:2;position:relative}

  /* Buttons */
  .action-btn{display:inline-flex;align-items:center;gap:8px;padding:9px 14px;border-radius:10px;font-weight:600;cursor:pointer;transition:all .18s; text-decoration:none}
  .action-btn.add{background:linear-gradient(135deg,rgba(37,99,235,0.88),rgba(0,174,239,0.88));color:#fff;border:0;box-shadow:0 6px 18px rgba(37,99,235,0.22)}
  .action-btn.upload{background:rgba(255,255,255,0.82);color:var(--blue);border:2px solid rgba(37,99,235,0.12)}
  .action-btn.export{background:rgba(255,255,255,0.9);color:var(--accent);border:2px solid rgba(0,174,239,0.12)}
  .action-btn.back{background:rgba(255,255,255,0.9);color:var(--blue);border:2px solid rgba(37,99,235,0.12)}
  .action-btn:active{transform:translateY(1px)}
  .icon-btn{width:36px;height:36px;border-radius:50%;display:inline-flex;align-items:center;justify-content:center;background:rgba(255,255,255,0.9);border:1px solid rgba(0,0,0,0.04);cursor:pointer;box-shadow:0 2px 8px rgba(0,0,0,0.06)}
  .icon-btn.blue{color:var(--blue);border-color:rgba(37,99,235,0.15)}
  .icon-btn.red{color:var(--red);border-color:rgba(220,38,38,0.15)}

  /* Top summary card */
  .summary-card{display:flex;gap:14px;flex-wrap:wrap;justify-content:space-between;align-items:center;background:white;padding:14px;border-radius:12px;box-shadow:0 6px 18px rgba(0,0,0,0.04);position:relative;z-index:2}
  .summary-item{flex:1;min-width:140px;text-align:center}
  .summary-item small{display:block;color:var(--muted);margin-bottom:6px}
  .badge{display:inline-block;padding:6px 12px;border-radius:999px;font-weight:700}

  /* Main card */
  .card{margin-top:18px;padding:14px;background:white;border-radius:12px;box-shadow:0 6px 20px rgba(0,0,0,0.05);position:relative;z-index:2}

  /* Add client edit box */
  #editClientForm{display:none;margin:12px auto 10px;max-width:420px;background:rgba(255,255,255,0.98);border-radius:10px;padding:14px;box-shadow:0 2px 10px rgba(0,0,0,0.06)}
  input[type="text"], input[type="email"], textarea, input[type="number"]{width:100%;padding:8px;border-radius:8px;border:1px solid #e6eefb;margin:8px 0;box-sizing:border-box;background:rgba(255,255,255,0.98)}
  .form-row{display:flex;gap:8px}
  .form-row input{flex:1}

  /* BL list */
  .controls{display:flex;gap:8px;align-items:center;flex-wrap:wrap;margin-bottom:10px}
  .controls input[type="text"]{padding:8px;border-radius:10px;border:1px solid #d1d5db;width:280px}
  .bl-list{max-height:360px;overflow-y:auto;border-radius:8px;border:1px solid #eef2f6;background:#fff;padding:6px}
  .bl-row{display:flex;align-items:center;gap:10px;padding:10px;border-radius:8px;margin-bottom:6px;align-items:flex-start}
  .bl-text{flex:1;min-width:0;overflow:hidden;white-space:nowrap;text-overflow:ellipsis}
  .bl-meta{font-size:13px;color:var(--muted);margin-top:4px}
  .bl-row.unpaid{background:linear-gradient(90deg,rgba(220,38,38,0.04),transparent);border-left:4px solid var(--red);}

  /* small helpers */
  .muted{color:var(--muted);font-size:13px}
  .top-actions{display:flex;gap:8px;align-items:center}

  @media(max-width:800px){
    .summary-card{flex-direction:column;align-items:stretch}
    .controls input[type="text"]{width:100%}
  }
</style>
</head>
<body>
  <div class="container">
    <div class="watermark"><img src="{{ url_for('static', filename='logo.png') }}" alt="logo"></div>

    <a href="{{ url_for('clients_page') }}" class="action-btn back">← Back</a>
    <h1>Client Overview</h1>

    <p class="meta">{{ client.name }} • {{ client.email or '-' }} • {{ client.phone or '-' }}</p>
    {% with messages = get_flashed_messages(with_categories=true) %}
      {% if messages %}
       {% for category, msg in messages %}
          <div style="
            margin:10px 0;
            padding:10px 14px;
            border-radius:10px;
            font-size:14px;
            background:
              {% if category == 'success' %}rgba(22,163,74,0.15)
              {% else %}rgba(220,38,38,0.15){% endif %};
            color:
              {% if category == 'success' %}#166534
              {% else %}#991b1b{% endif %};
            border-left:4px solid
              {% if category == 'success' %}#16a34a
              {% else %}#dc2626{% endif %};
          ">
            {{ msg }}
          </div>
        {% endfor %}
      {% endif %}
    {% endwith %}

    <!-- SUMMARY -->
    <div class="summary-card">
      <div class="summary-item">
        <small>Total Billed</small>
        <div class="value">₵{{ '%.2f'|format(total_billed) }}</div>
      </div>
      <div class="summary-item">
        <small>Total Paid</small>
        <div class="value" style="color:var(--green)">₵{{ '%.2f'|format(total_paid) }}</div>
      </div>
      <div class="summary-item">
        <small>Outstanding</small>
        <div class="value" style="color:var(--red)">₵{{ '%.2f'|format(total_unpaid) }}</div>
      </div>
      <div class="summary-item">
        <small>Status</small>
        <div style="margin-top:6px;">
          {% if finance_status == 'Cleared' %}
            <span class="badge" style="background:#dcfce7;color:#166534">Cleared</span>
          {% elif finance_status == 'Part Paid' %}
            <span class="badge" style="background:#fef9c3;color:#854d0e">Part Paid</span>
          {% elif finance_status == 'Owing' %}
            <span class="badge" style="background:#fee2e2;color:#991b1b">Owing</span>
          {% else %}
            <span class="badge" style="background:#eef2f6;color:#374151">No BLs</span>
          {% endif %}
        </div>
      </div>
    </div>

    <!-- EDIT CLIENT (hidden) -->
    <div id="editClientForm">
      <form method="post">
        <input type="hidden" name="action" value="edit_client">
        <input name="name" value="{{ client.name }}" placeholder="Client Name" required>
        <input name="email" value="{{ client.email }}" placeholder="Email">
        <input name="phone" value="{{ client.phone }}" placeholder="Phone">
        <textarea name="notes" rows="3" placeholder="Notes">{{ client.notes or '' }}</textarea>
        <div style="display:flex;gap:8px;justify-content:flex-end">
          <button type="button" class="action-btn upload" onclick="toggleEditClient()">Cancel</button>
          <button type="submit" class="action-btn add">Save Changes</button>
        </div>
      </form>
    </div>

    <!-- CONTROLS -->
    <div class="card">
      <div style="display:flex;justify-content:space-between;align-items:center;gap:12px;flex-wrap:wrap">
        <h3 style="margin:0">BL List</h3>

        <div class="top-actions">
          <button type="button" onclick="toggleForm()" class="action-btn add">➕ Add BL</button>
          <button type="button" onclick="toggleMultiBl()" class="action-btn add">➕➕ Add Multiple BLs</button>
          <button type="button" onclick="toggleUpload()" class="action-btn upload">Upload BL</button>
        </div>
      </div>

      <!-- SEARCH + FILTER -->
      <div class="controls" style="margin-top:12px">
        <input id="blSearchInput" type="text" placeholder="Enter BL number or keyword…" />
        <button type="button" class="action-btn upload" onclick="doSearch()">Search BL</button>
        <button type="button" class="action-btn upload" onclick="toggleUnpaidFilter()" id="unpaidBtn">Unpaid</button>
        <div style="margin-left:8px" class="muted">Select BLs → Export Selected</div>
      </div>

      <!-- ADD BL FORM -->
      <div id="addBlForm" style="display:none;margin-top:10px;padding:12px;border-radius:10px;background:rgba(255,255,255,0.98);box-shadow:0 4px 12px rgba(0,0,0,0.04);">
        <form method="post" enctype="multipart/form-data">
          <input type="hidden" name="action" value="add_bl">
          <div class="form-row">
            <input name="bl_number" placeholder="BL Number" required>
            <input name="amount_total" placeholder="Total Amount" type="number" step="0.01" required>
          </div>
          <div style="margin-top:8px">
            <input type="file" name="bl_document" accept=".pdf,.jpg,.png,.docx">
          </div>
          <div style="margin-top:8px;text-align:right">
            <button type="button" class="action-btn upload" onclick="toggleForm()">Cancel</button>
            <button type="submit" class="action-btn add">Save</button>
          </div>
        </form>
      </div>

      <!-- ADD MULTIPLE BLs -->
      <div id="addMultiBlForm" style="display:none;margin-top:10px;padding:12px;border-radius:10px;background:#fff;box-shadow:0 4px 12px rgba(0,0,0,0.04);">
        <form method="post">
          <input type="hidden" name="action" value="add_multi_bl">
          <div id="multiBlRows">
            <div class="multi-bl-row" style="display:flex;gap:8px;margin-bottom:8px">
              <input name="bl_number[]" placeholder="BL Number" required style="flex:2">
              <input name="amount_total[]" placeholder="Total ₵" type="number" step="0.01" style="flex:1">
            </div>
          </div>
          <div style="display:flex;gap:8px;margin-top:8px;justify-content:flex-end">
            <button type="button" class="action-btn upload" onclick="addBlRow()">➕ Add another BL</button>
            <button type="button" class="action-btn upload" onclick="toggleMultiBl()">Cancel</button>
            <button type="submit" class="action-btn add">Save All BLs</button>
          </div>
        </form>
      </div>

      <!-- UPLOAD BL FORM -->
      <div id="uploadBlForm" style="display:none;margin-top:10px;padding:12px;border-radius:10px;background:rgba(255,255,255,0.98);box-shadow:0 4px 12px rgba(0,0,0,0.04);">
        <form method="post" enctype="multipart/form-data">
          <input type="hidden" name="action" value="add_doc">
          <input name="doc_desc" placeholder="Document Description">
          <input type="file" name="client_document" accept=".pdf,.jpg,.png,.docx" required>
          <div style="margin-top:8px;text-align:right">
            <button type="button" class="action-btn upload" onclick="toggleUpload()">Cancel</button>
            <button type="submit" class="action-btn upload">Upload</button>
          </div>
        </form>
      </div>

      {% if info %}
        <div style="background:rgba(59,130,246,0.12);border-left:3px solid #2563eb;color:#1e3a8a;padding:10px 12px;border-radius:8px;margin-top:12px">
          {{ info }}
        </div>
      {% endif %}

      <!-- BL LIST + EXPORT -->
      <form id="exportForm" method="post" style="margin-top:12px">
        <input type="hidden" name="action" value="export_selected_bl">

        <div class="bl-list" id="blList">
          {% for bl in client.bls %}
            <div class="bl-row {% if bl.amount_unpaid > 0 %}unpaid{% endif %}">
              <div style="display:flex;align-items:center;gap:10px">
                <input type="checkbox" name="bl_ids" value="{{ bl.id }}">
              </div>

              <div class="bl-text">
                <strong>{{ bl.bl_number }}</strong>
                <div class="bl-meta">Outstanding: ₵{{ '%.2f'|format(bl.amount_unpaid) }}</div>
              </div>

              <div style="display:flex;gap:8px;align-items:center">
                {% if bl.document %}
                  <a href="{{ url_for('uploaded_file', filename=bl.document) }}" target="_blank" class="icon-btn blue" title="View Document">
                    <img src="{{ url_for('static', filename='icon_view.png') }}" alt="view" style="width:16px;height:16px">
                  </a>
                {% endif %}
                <a href="{{ url_for('delete_bl', bl_id=bl.id) }}" class="icon-btn red" title="Delete BL" onclick="return confirm('Delete this BL?')">
                  <img src="{{ url_for('static', filename='icon_delete.png') }}" alt="del" style="width:14px;height:14px">
                </a>
              </div>
            </div>
          {% else %}
            <p class="muted">No BLs yet.</p>
          {% endfor %}
        </div>

        <div style="margin-top:12px;display:flex;gap:8px;justify-content:flex-end">
          <button type="submit" class="action-btn export">Export Selected BLs</button>
        </div>
      </form>

      <!-- single Export All (bottom) -->
      <div style="display:flex;justify-content:space-between;align-items:center;margin-top:14px">
        <a href="{{ url_for('export_client_pdf', client_id=client.id) }}" class="action-btn export">Export All</a>
        <p style="color:#4b5563;margin:0">Notes: {{ client.notes or '—' }}</p>
      </div>
    </div>

    <footer style="margin-top:18px;text-align:center;color:#93c5fd">©️ 2026 CargoBloc Logistics — Vision to Reality</footer>
  </div>

<script>
/* Toggle forms */
function toggleForm(){
  const f = document.getElementById('addBlForm');
  f.style.display = (f.style.display === 'none' || f.style.display === '') ? 'block' : 'none';
  document.getElementById('uploadBlForm').style.display = 'none';
  document.getElementById('addMultiBlForm').style.display = 'none';
}
function toggleMultiBl(){
  const f = document.getElementById('addMultiBlForm');
  f.style.display = (f.style.display === 'none' || f.style.display === '') ? 'block' : 'none';
  document.getElementById('addBlForm').style.display = 'none';
  document.getElementById('uploadBlForm').style.display = 'none';
}
function toggleUpload(){
  const f = document.getElementById('uploadBlForm');
  f.style.display = (f.style.display === 'none' || f.style.display === '') ? 'block' : 'none';
  document.getElementById('addBlForm').style.display = 'none';
  document.getElementById('addMultiBlForm').style.display = 'none';
}
function toggleEditClient(){
  const f = document.getElementById('editClientForm');
  f.style.display = (f.style.display === 'none' || f.style.display === '') ? 'block' : 'none';
  window.scrollTo({ top: 0, behavior: 'smooth' });
}

/* Add row for multiple BLs */
function addBlRow(){
  const container = document.getElementById('multiBlRows');
  const row = document.createElement('div');
  row.className = 'multi-bl-row';
  row.style.display = 'flex';
  row.style.gap = '8px';
  row.style.marginBottom = '8px';
  row.innerHTML = `
    <input name="bl_number[]" placeholder="BL Number" required style="flex:2">
    <input name="amount_total[]" placeholder="Total ₵" type="number" step="0.01" style="flex:1">
  `;
  container.appendChild(row);
}

/* Search and unpaid filter */
let unpaidOnly = false;
function doSearch(){
  const q = (document.getElementById('blSearchInput').value || '').toLowerCase().trim();
  document.querySelectorAll('.bl-list .bl-row').forEach(row => {
    const text = row.innerText.toLowerCase();
    const matches = q === '' ? true : text.includes(q);
    row.style.display = matches ? 'flex' : 'none';
  });
}
function toggleUnpaidFilter(){
  unpaidOnly = !unpaidOnly;
  const btn = document.getElementById('unpaidBtn');
  btn.style.background = unpaidOnly ? 'linear-gradient(135deg,rgba(37,99,235,0.9),rgba(0,174,239,0.9))' : '';
  document.querySelectorAll('.bl-list .bl-row').forEach(row => {
    if(unpaidOnly){
      row.style.display = row.classList.contains('unpaid') ? 'flex' : 'none';
    } else {
      row.style.display = 'flex';
      doSearch(); // reapply search filter if any
    }
  });
}

/* live search as user types */
document.getElementById('blSearchInput').addEventListener('input', function(){
  const q = this.value.toLowerCase().trim();
  document.querySelectorAll('.bl-list .bl-row').forEach(row => {
    const text = row.innerText.toLowerCase();
    const visible = text.includes(q);
    // apply unpaidOnly filter too
    if(unpaidOnly){
      row.style.display = (visible && row.classList.contains('unpaid')) ? 'flex' : 'none';
    } else {
      row.style.display = visible ? 'flex' : 'none';
    }
  });
});
</script>
</body>
</html>"""

CLIENTS_PAGE_HTML = """<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<title>Clients — CargoBloc</title>
<link href="https://fonts.googleapis.com/css2?family=Poppins:wght@400;600&display=swap" rel="stylesheet">
<style>
  body{
    font-family:'Poppins',sans-serif;
    background:url('{{ url_for('static', filename='homepage_bg.png') }}') no-repeat center center fixed;
    background-size:cover;
    margin:0; padding:0;
  }
  .wrap{
    max-width:1000px;
    margin:40px auto;
    background:rgba(255,255,255,0.95);
    border-radius:16px;
    padding:24px;
    box-shadow:0 10px 30px rgba(0,0,0,0.08);
  }
  h1{color:#2563eb;margin:0 0 14px;}
  .top{
  display:flex;
  gap:10px;
  margin-bottom:14px;
  flex-wrap:wrap;
}

.top input{
  flex:1 1 220px;
  padding:10px;
  border-radius:8px;
  border:1px solid #d1d5db;
}

.top button{
  flex:0 0 auto;
  padding:10px 16px;
  border:none;
  border-radius:8px;
  background:#2563eb;
  color:#fff;
  font-weight:600;
  cursor:pointer;
}
  .list{margin-top:16px;}
  .item{
    display:flex; justify-content:space-between; align-items:center;
    background:#fff; padding:12px; border-radius:10px;
    box-shadow:0 4px 12px rgba(0,0,0,0.05);
    margin-bottom:10px;
  }
  .meta small{color:#6b7280;}
  a.open{
    background:#00AEEF; color:#fff; padding:6px 12px;
    border-radius:8px; text-decoration:none; font-weight:600;
  }
  a.back{color:#2563eb;text-decoration:none;font-weight:600;}

  /* ===== CLIENTS LIST SCROLL (CargoBloc glass look) ===== */
.clients-scroll {
  max-height: 420px;            /* adjust up/down to taste */
  overflow-y: auto;
  padding-right: 8px;           /* room for scrollbar */
  border-radius: 10px;
  background: rgba(255,255,255,0.02);
}

/* subtle CargoBloc glass scrollbar */
.clients-scroll::-webkit-scrollbar { width: 8px; }
.clients-scroll::-webkit-scrollbar-thumb {
  background: linear-gradient(180deg, rgba(37,99,235,0.25), rgba(0,174,239,0.25));
  border-radius: 8px;
  backdrop-filter: blur(6px);
}
.clients-scroll::-webkit-scrollbar-track { background: transparent; }

/* optional: keep each client item full width inside the scroller */
.clients-scroll .client-item { width:100%; box-sizing:border-box; }

</style>
</head>
<body>
  <div class="wrap">
    <a href="{{ url_for('home') }}" class="back">← Back to Dashboard</a>
    <h1>Clients</h1>
    <p style="color:#6b7280; margin:0 0 10px;">
      Showing {{ clients|length }} client{{ '' if clients|length == 1 else 's' }}
    </p>
    



    <form class="top" method="get" style="display:flex; gap:10px; margin-bottom:14px; flex-wrap:wrap;">

  <input name="q"
         placeholder="Search client name or BL number..."
         value="{{ q or '' }}"
         style="flex:2;">

  <input type="date"
         name="date"
         value="{{ selected_date or '' }}"
         style="flex:1;">

  <button type="submit">Search</button>

  {% if q or selected_date %}
    <a href="{{ url_for('clients_page') }}"
       style="align-self:center; text-decoration:none; color:#2563eb; font-weight:600;">
       Clear
    </a>
  {% endif %}

</form>
    <div class="client-scroll"
    <div class="list">
      {% for row in clients %}
      {% set c = row.client %}
      <div class="item">
        <div class="meta">
          <strong>{{ c.name }}</strong><br>
        <small>
         {{ c.email or '-' }} • {{ c.phone or '-' }}<br>
          BLs: {{ c.bls|length }} |
          Owed: ₵{{ '%.2f'|format(row.unpaid) }}
       </small>
        </div>
  <div style="display:flex; gap:8px;">
    <a class="open" href="{{ url_for('client_detail', client_id=c.id) }}">Open</a>
    <a href="{{ url_for('delete_client', client_id=c.id) }}"
       style="background:#dc2626;color:#fff;padding:6px 12px;border-radius:8px;text-decoration:none;font-weight:600;"
       onclick="return confirm('Delete this client?')">Delete</a>
  </div>
      </div>
      {% else %}
        <p>No clients found.</p>
      {% endfor %}
    </div>
  </div>
</body>
</html>"""

HOUSE_BL_HTML = """<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<title>House BLs — CargoBloc</title>
<link href="https://fonts.googleapis.com/css2?family=Poppins:wght@300;400;600&display=swap" rel="stylesheet">
<style>
  body {
    font-family:'Poppins',sans-serif;
    background: url('{{ url_for('static', filename='homepage_bg.png') }}') no-repeat center center fixed;
    background-size: cover;
    margin:0; padding:0;
    color:#0b1220;
  }
  .container {
    max-width:1000px;
    margin:40px auto;
    background:rgba(255,255,255,0.92);
    border-radius:16px;
    padding:24px 28px;
    box-shadow:0 10px 30px rgba(0,0,0,0.08);
  }
  h1 {
    text-align:center;
    color:#2563eb;
    margin:0 0 18px;
  }
  form {
    display:grid;
    grid-template-columns:repeat(auto-fit,minmax(280px,1fr));
    gap:14px;
  }
  input, textarea {
    width:100%;
    padding:10px;
    border:1px solid #d1d5db;
    border-radius:8px;
    box-sizing:border-box;
  }
  textarea { grid-column:1 / -1; resize:vertical; }
  button {
    grid-column:1 / -1;
    background:linear-gradient(135deg,#007BFF,#00AEEF);
    color:white; font-weight:600;
    border:none; border-radius:8px;
    padding:12px; cursor:pointer;
    transition:all .2s ease;
  }
  button:hover { transform:translateY(-2px); }
  .list {
    margin-top:30px;
    background:#fff;
    border-radius:12px;
    box-shadow:0 6px 20px rgba(0,0,0,0.05);
    padding:18px;
  }
  .list-item {
    display:flex; justify-content:space-between;
    align-items:center; border-bottom:1px solid #e5e7eb;
    padding:10px 0;
  }
  .list-item:last-child{border:none;}
  .list-item small{color:#6b7280;}
  a.export {
    background:#2563eb; color:#fff;
    padding:8px 12px; border-radius:6px;
    text-decoration:none; font-weight:600;
  }
  a.back {
    display:inline-block; margin-bottom:10px;
    color:#2563eb; text-decoration:none; font-weight:600;
  }
</style>
</head>
<body>
  <div class="container">
    <a href="{{ url_for('home') }}" class="back">← Back to Dashboard</a>
    <h1>Create House BL</h1>

    <form method="post">
      <input name="exporter" placeholder="Exporter" required>
      <input name="bl_number" placeholder="Bill of Lading Number" required>
      <input name="forwarding_agent" placeholder="Forwarding Agent">
      <input name="consignee" placeholder="Consignee">
      <input name="notify_party" placeholder="Notify Party / Intermediate Consignee">
      <input name="vessel" placeholder="Vessel">
      <input name="voyage" placeholder="Voyage">
      <input name="port_loading" placeholder="Port of Loading">
      <input name="port_discharge" placeholder="Port of Discharge">
      <input name="place_delivery" placeholder="Place of Delivery">
      <textarea name="marks_numbers" placeholder="Marks and Numbers"></textarea>
      <input name="pkgs" placeholder="Packages (Pkgs)">
      <textarea name="description_goods" placeholder="Description of Goods"></textarea>
      <input name="gross_weight" placeholder="Gross Weight (kg)">
      <button type="submit">Save House BL</button>
    </form>

    <div class="list">
      <h3>Saved House BLs</h3>
      {% for h in hbls %}
      <div class="list-item">
  <div>
    <strong>{{ h.bl_number }}</strong><br>
    <small>{{ h.exporter or '-' }} — {{ h.consignee or '-' }}</small>
  </div>
  <div style="display:flex; gap:10px;">
    <a href="{{ url_for('edit_house_bl', hbl_id=h.id) }}" class="export" style="background:#00AEEF;">Edit</a>
    <a href="{{ url_for('export_house_bl', hbl_id=h.id) }}" class="export">Export</a>
  </div>
</div>
      {% else %}
        <p>No House BLs yet.</p>
      {% endfor %}
    </div>
  </div>
</body>
</html>"""
EDIT_HOUSE_BL_HTML = """<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<title>Edit House BL — CargoBloc</title>
<link href="https://fonts.googleapis.com/css2?family=Poppins:wght@300;400;600&display=swap" rel="stylesheet">
<style>
  body {
    font-family:'Poppins',sans-serif;
    background: url('{{ url_for('static', filename='homepage_bg.png') }}') no-repeat center center fixed;
    background-size: cover;
    margin:0; padding:0;
    color:#0b1220;
  }
  .container {
    max-width:900px;
    margin:40px auto;
    background:rgba(255,255,255,0.94);
    border-radius:16px;
    padding:28px;
    box-shadow:0 10px 30px rgba(0,0,0,0.08);
  }
  h1 {
    text-align:center;
    color:#2563eb;
    margin:0 0 18px;
  }
  form {
    display:grid;
    grid-template-columns:repeat(auto-fit,minmax(280px,1fr));
    gap:14px;
  }
  input, textarea {
    width:100%;
    padding:10px;
    border:1px solid #d1d5db;
    border-radius:8px;
    box-sizing:border-box;
  }
  textarea { grid-column:1 / -1; resize:vertical; }
  button {
    grid-column:1 / -1;
    background:linear-gradient(135deg,#007BFF,#00AEEF);
    color:white; font-weight:600;
    border:none; border-radius:8px;
    padding:12px; cursor:pointer;
    transition:all .2s ease;
  }
  button:hover { transform:translateY(-2px); }
  a.back {
    display:inline-block; margin-bottom:12px;
    color:#2563eb; text-decoration:none; font-weight:600;
  }
</style>
</head>
<body>
  <div class="container">
    <a href="{{ url_for('house_bl') }}" class="back">← Back to House BL List</a>
    <h1>Edit House Bill of Lading</h1>

    <form method="post">
      <input name="exporter" placeholder="Exporter" value="{{ hbl.exporter }}">
      <input name="bl_number" placeholder="Bill of Lading Number" value="{{ hbl.bl_number }}">
      <input name="forwarding_agent" placeholder="Forwarding Agent" value="{{ hbl.forwarding_agent }}">
      <input name="consignee" placeholder="Consignee" value="{{ hbl.consignee }}">
      <input name="notify_party" placeholder="Notify Party" value="{{ hbl.notify_party }}">
      <input name="vessel" placeholder="Vessel" value="{{ hbl.vessel }}">
      <input name="voyage" placeholder="Voyage" value="{{ hbl.voyage }}">
      <input name="port_loading" placeholder="Port of Loading" value="{{ hbl.port_loading }}">
      <input name="port_discharge" placeholder="Port of Discharge" value="{{ hbl.port_discharge }}">
      <input name="place_delivery" placeholder="Place of Delivery" value="{{ hbl.place_delivery }}">
      <textarea name="marks_numbers" placeholder="Marks and Numbers">{{ hbl.marks_numbers }}</textarea>
      <input name="pkgs" placeholder="Packages" value="{{ hbl.pkgs }}">
      <textarea name="description_goods" placeholder="Description of Goods">{{ hbl.description_goods }}</textarea>
      <input name="gross_weight" placeholder="Gross Weight" value="{{ hbl.gross_weight }}">
      <button type="submit"> Save Changes</button>
    </form>
  </div>
</body>
</html>"""
FORGOT_HTML = """<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<title>Forgot Password — CargoBloc</title>
<link href="https://fonts.googleapis.com/css2?family=Poppins:wght@400;600&display=swap" rel="stylesheet">
<style>
  body {
    margin: 0; height: 100vh; display: flex; justify-content: center; align-items: center;
    font-family: 'Poppins', sans-serif;
    background: url('{{ url_for('static', filename='login_bg.png') }}') no-repeat center center fixed;
    background-size: cover;
  }
  .overlay { position: fixed; inset: 0; backdrop-filter: blur(8px); background: rgba(255,255,255,0.25); }
  .card {
    position: relative; z-index: 1;
    background: rgba(255,255,255,0.20); backdrop-filter: blur(15px);
    border-radius: 16px; box-shadow: 0 8px 30px rgba(0,0,0,0.2);
    padding: 32px 36px; width: 380px; color: #fff; text-align: center;
  }
  h2 { margin: 0 0 10px; }
  p  { margin: 6px 0 16px; color: #e5e7eb; font-size: 14px; }
  input[type="email"] {
    width: 100%; padding: 12px; border: none; border-radius: 8px;
    background: rgba(255,255,255,0.88); color: #222; outline: none;
  }
  button {
    width: 100%; padding: 12px; margin-top: 12px;
    background: linear-gradient(135deg, #007BFF, #00AEEF);
    border: none; border-radius: 8px; color: white; font-weight: 600; cursor: pointer;
  }
  a { color: #93c5fd; text-decoration: none; font-size: 13px; }
  a:hover { text-decoration: underline; }
  .info {
    background: rgba(59,130,246,0.16); border-left: 3px solid #60a5fa;
    color: #e0f2fe; padding: 10px 12px; border-radius: 8px; font-size: 13px; text-align: left; margin-bottom: 10px;
  }
  footer { position: fixed; bottom: 14px; left: 0; right: 0; text-align: center; color: #93c5fd; font-size: 12px; }
</style>
</head>
<body>
<div class="overlay"></div>

<div class="card">
  <h2>Reset your password</h2>
  <p>Enter your email and we’ll send you a reset link (feature coming soon).</p>

  {% if info %}<div class="info">{{ info }}</div>{% endif %}

  <form method="post" autocomplete="off">
    <input type="email" name="email" placeholder="you@company.com" required>
    <button type="submit">Send Reset Link</button>
  </form>

  <p style="margin-top:12px;"><a href="{{ url_for('login') }}">← Back to Login</a></p>
</div>

<footer>© 2025 CargoBloc Logistics</footer>
</body>
</html>"""
RECEIPT_UI_HTML = """
<!doctype html>
<html>
<head>
<meta charset="utf-8">
<title>Generate Receipt</title>

<link href="https://fonts.googleapis.com/css2?family=Poppins:wght@400;600&display=swap" rel="stylesheet">

<style>
/* CargoBloc glass toggle button */
.btn.glass{
  background: rgba(255,255,255,0.25);
  color: #2563eb;
  border: 1.5px solid rgba(37,99,235,0.35);
  backdrop-filter: blur(10px);
  box-shadow: 0 6px 18px rgba(37,99,235,0.15);
}

/* Active glass state (NO RED) */
.btn.glass.active{
  background: rgba(255,255,255,0.4);
  box-shadow: inset 0 0 0 2px rgba(37,99,235,0.35),
              0 6px 20px rgba(37,99,235,0.25);
  color:#2563eb;
}

/* Unpaid highlight — CargoBloc glass feel */
.bl-item.unpaid-active{
  background: rgba(220, 38, 38, 0.08);
  box-shadow: inset 0 0 0 1px rgba(220, 38, 38, 0.25);
  backdrop-filter: blur(6px);
}
/* UNPAID TOGGLE BUTTON */
.unpaid-btn{
  padding:10px 14px;
  border-radius:999px;
  border:1px solid rgba(37,99,235,0.35);
  background:rgba(255,255,255,0.55);
  color:#2563eb;
  font-weight:600;
  cursor:pointer;
  backdrop-filter: blur(6px);
  transition:all 0.2s ease;
  white-space:nowrap;
}

.unpaid-btn.active{
  background:linear-gradient(135deg, rgba(37,99,235,0.85), rgba(0,174,239,0.85));
  color:#fff;
  border-color:transparent;
  box-shadow:0 4px 14px rgba(37,99,235,0.35);
}

/* UNPAID BL HIGHLIGHT */
.bl-item.unpaid-highlight{
  background:rgba(254,226,226,0.65);
  border-left:4px solid #dc2626;
}
body{
  font-family:'Poppins',sans-serif;
  background:url('{{ url_for('static', filename='homepage_bg.png') }}') no-repeat center center fixed;
  background-size:cover;
  margin:0;
}

.container{
  max-width:1200px;
  margin:40px auto;
  background:rgba(255,255,255,0.96);
  border-radius:18px;
  padding:30px;
}

.header{
  display:flex;
  justify-content:space-between;
  align-items:center;
}

h1{ color:#2563eb; margin:0; }

.btn{
  background:#2563eb;
  color:#fff;
  border:none;
  padding:10px 16px;
  border-radius:10px;
  font-weight:600;
  cursor:pointer;
  text-decoration:none;
}

.layout{
  display:grid;
  grid-template-columns: 2fr 1fr;
  gap:30px;
  margin-top:25px;
}

.card{
  background:#fff;
  border-radius:14px;
  padding:20px;
  box-shadow:0 10px 30px rgba(0,0,0,0.06);
}

.card h3{
  margin-top:0;
  color:#2563eb;
}

label{
  font-size:13px;
  font-weight:600;
  display:block;
  margin-top:14px;
}

input, select{
  width:100%;
  padding:10px;
  border-radius:8px;
  border:1px solid #d1d5db;
  margin-top:6px;
}

.bl-search{
  margin-bottom:14px;
}


/* ===== BL CHECKLIST STYLE ===== */

.bl-list{
  border:1px solid #e5e7eb;
  border-radius:12px;
  max-height:340px;
  overflow-y:auto;
  background:#fff;
}

.bl-item{
  display:flex;
  align-items:center;
  justify-content:space-between;
  padding:12px 16px;
  cursor:pointer;
  transition:background 0.15s ease;
}

.bl-item:hover{
  background:#f1f5f9;
}

.bl-left{
  display:flex;
  align-items:center;
  gap:12px;
}

.bl-item input[type="checkbox"]{
  width:18px;
  height:18px;
}

.bl-text{
  display:flex;
  flex-direction:column;
}

.bl-number{
  font-weight:500;
  color:#111827;
}

.bl-sub{
  font-size:12px;
  color:#6b7280;
}

.bl-status{
  font-size:12px;
  padding:4px 10px;
  border-radius:999px;
  background:#fef3c7;
  color:#92400e;
  white-space:nowrap;
}


.bl-meta{
  flex:1;
}

.badge{
  font-size:11px;
  background:#fef3c7;
  color:#92400e;
  padding:4px 6px;
  border-radius:6px;
  display:inline-block;
  margin-top:6px;
}

.summary{
  background:#f8fafc;
  border-radius:10px;
  padding:14px;
  margin-top:16px;
  font-size:14px;
}

.summary strong{
  color:#111827;
}
</style>
</head>

<body>
<div class="container">

<!-- HEADER -->
<div class="header">
  <h1>Generate Receipt</h1>
  <a href="{{ url_for('receipt_history') }}" class="btn">← Back</a>
</div>

<!-- CLIENT SELECT -->
<form method="get" style="margin-top:20px;">
  <label>Select Client</label>
  <select name="client_id" onchange="this.form.submit()">
    <option value="">-- choose client --</option>
    {% for c in clients %}
      <option value="{{ c.id }}" {% if selected_client_id == c.id %}selected{% endif %}>
        {{ c.name }}
      </option>
    {% endfor %}
  </select>
</form>

{% if bls %}
<form method="post" class="layout">

<input type="hidden" name="client_id" value="{{ selected_client_id }}">

<!-- ================= LEFT: BL LIST ================= -->
<div class="card">
  <h3>Client BLs</h3>

  <div style="display:flex; gap:8px; margin-bottom:12px; align-items:center;">
  <input class="bl-search"
         placeholder="Search BL..."
         id="blSearchInput"
         style="flex:1;">

  <button type="button"
          class="btn"
          onclick="filterBLs(document.getElementById('blSearchInput').value)">
    Search
  </button>

  <button type="button"
          id="unpaidToggle"
          class="unpaid-btn"
          onclick="toggleUnpaid()">
    Unpaid
  </button>
</div>
<!-- SELECT ALL -->
<div style="display:flex; align-items:center; gap:10px; margin-bottom:10px;">
  <label style="
    display:flex;
    align-items:center;
    gap:8px;
    padding:8px 14px;
    border-radius:12px;
    background:rgba(255,255,255,0.55);
    backdrop-filter:blur(10px);
    border:1px solid rgba(255,255,255,0.6);
    font-size:13px;
    font-weight:600;
    cursor:pointer;
  ">
    <input type="checkbox"
           id="selectAllBLs"
           style="width:16px;height:16px;"
           onchange="toggleSelectAll(this)">
    Select All
  </label>
</div>

  <div class="bl-list" id="blList">

{% for bl, last_date in bls %}
<label class="bl-item">

  <div class="bl-left">
    <input type="checkbox"
       name="bl_ids"
       value="{{ bl.id }}"
       data-unpaid="{{ bl.amount_unpaid }}"
       onchange="updateSummary()">

    <div class="bl-text">
      <div class="bl-number">
        BL {{ bl.bl_number }}
      </div>
      <div class="bl-sub">
        Outstanding: ₵{{ "%.2f"|format(bl.amount_unpaid) }}
      </div>
    </div>
  </div>

  {% if last_date %}
    <div class="bl-status">
      Receipted • {{ last_date.strftime("%d %b %Y") }}
    </div>
  {% endif %}

</label>
{% endfor %}

  </div>
</div>

<!-- ================= RIGHT: RECEIPT INFO ================= -->
<div class="card">
  <h3>Receipt Info</h3>

  <label>Total Amount Received</label>
  <input type="number"
       step="0.01"
       name="amount"
       id="totalAmount"
       readonly
       required>

  <!-- PAYMENT TYPE -->
<label style="margin-top:14px;">Payment Type</label>
<select name="payment_type" id="paymentType" onchange="toggleTransactionId(this.value)" required>
  <option value="MoMo">Mobile Money</option>
  <option value="Bank">Bank Transfer</option>
  <option value="Cash">Cash</option>
</select>

<!-- TRANSACTION ID -->
<div id="transactionIdWrap">
  <label style="margin-top:12px;">Transaction ID</label>
  <input name="transaction_id"
         id="transactionId"
         placeholder="Enter transaction reference">
</div>     

   <label style="margin-top:14px;">Description</label>
  <select name="description_type" onchange="toggleCustomDesc(this.value)">
    <option value="Amendment">Amendment</option>
    <option value="Manifest">Manifest</option>
    <option value="custom">Custom</option>
  </select>

  <input name="custom_description"
         id="customDesc"
         placeholder="Enter custom description"
         style="margin-top:8px; display:none;">

  <label style="margin-top:12px;">Issued / Revised By</label>
  <input name="issued_by" required>

  <!-- ===== SUMMARY ===== -->
  <div id="summary" style="
  background:#f8fafc;
  border-radius:10px;
  padding:12px;
  margin-bottom:14px;
  font-size:13px;
">
  <div><strong>Selected BLs:</strong> <span id="blCount">0</span></div>
  <div><strong>Total Outstanding:</strong> ₵<span id="blTotal">0.00</span></div>
</div>

  <button class="btn"
          style="margin-top:20px;width:100%;">
    Generate Receipt
  </button>
</div>

</form>
{% endif %}

</div>


<script>
/* ================= SEARCH BL ================= */
let searchQuery = '';

function filterBLs(q){
  searchQuery = q.toLowerCase().trim();
  applyFilters();
}

/* ================= CUSTOM DESCRIPTION ================= */
function toggleCustomDesc(val){
  document.getElementById('customDesc').style.display =
    val === 'custom' ? 'block' : 'none';
}

/* ================= SUMMARY ================= */
function updateSummary(){
  let total = 0;
  let count = 0;

  document.querySelectorAll('input[name="bl_ids"]:checked').forEach(cb => {
    total += parseFloat(cb.dataset.unpaid || 0);
    count++;
  });

  document.getElementById('blCount').innerText = count;
  document.getElementById('blTotal').innerText = total.toFixed(2);
  document.getElementById('totalAmount').value = total.toFixed(2);

  /* ✅ keep Select All in sync */
  const visibleUnchecked =
    document.querySelectorAll(
      '#blList .bl-item:not([style*="display: none"]) input[type="checkbox"]:not(:checked)'
    ).length;

  const selectAll = document.getElementById('selectAllBLs');
  if (selectAll){
    selectAll.checked = visibleUnchecked === 0;
  }
}

/* ================= UNPAID TOGGLE ================= */
let unpaidActive = false;

function toggleUnpaid(){
  unpaidActive = !unpaidActive;
  const btn = document.getElementById('unpaidToggle');

  btn.classList.toggle('active', unpaidActive);
  btn.innerText = unpaidActive ? 'Show All' : 'Unpaid';

  applyFilters();
}

/* ================= SELECT ALL ================= */
function toggleSelectAll(master){
  const visibleBLs = document.querySelectorAll(
    '#blList .bl-item:not([style*="display: none"]) input[type="checkbox"]'
  );

  visibleBLs.forEach(cb => {
    cb.checked = master.checked;
  });

  updateSummary();
}

/* ================= APPLY BOTH FILTERS ================= */
function applyFilters(){
  document.querySelectorAll('#blList .bl-item').forEach(item => {
    const text = item.innerText.toLowerCase();
    const checkbox = item.querySelector('input[type="checkbox"]');
    const unpaid = parseFloat(checkbox?.dataset.unpaid || 0);

    const matchesSearch = text.includes(searchQuery);
    const matchesUnpaid = !unpaidActive || unpaid > 0;

    if (matchesSearch && matchesUnpaid){
      item.style.display = 'flex';

      if (unpaidActive && unpaid > 0){
        item.classList.add('unpaid-highlight');
      } else {
        item.classList.remove('unpaid-highlight');
      }
    } else {
      item.style.display = 'none';
      item.classList.remove('unpaid-highlight');
    }
  });

  updateSummary();
}
function toggleTransactionId(type){
  const wrap = document.getElementById('transactionIdWrap');
  const input = document.getElementById('transactionId');

  if (type === 'Cash'){
    wrap.style.display = 'none';
    input.value = '';
  } else {
    wrap.style.display = 'block';
  }
}
</script>
</body>
</html>
"""
RECEIPT_HISTORY_HTML = """
<!doctype html>
<html>
<head>
<meta charset="utf-8">
<title>Receipts</title>

<link href="https://fonts.googleapis.com/css2?family=Poppins:wght@400;600&display=swap" rel="stylesheet">

<style>
body{
  font-family:'Poppins',sans-serif;
  background:url('{{ url_for('static', filename='homepage_bg.png') }}') no-repeat center center fixed;
  background-size:cover;
  margin:0;
}

.container{
  max-width:1150px;
  margin:40px auto;
  background:rgba(255,255,255,0.96);
  border-radius:18px;
  padding:28px 30px 34px;
}

.back-btn{
  background:#2563eb;
  color:#fff;
  padding:10px 14px;
  border-radius:10px;
  font-weight:600;
  text-decoration:none;
}

.tabs{
  display:flex;
  gap:20px;
  margin:20px 0 10px;
  border-bottom:1px solid #e5e7eb;
}

.tab{
  padding-bottom:10px;
  font-weight:600;
  color:#6b7280;
  text-decoration:none;
  cursor:pointer;
}

.tab.active{
  color:#2563eb;
  border-bottom:3px solid #2563eb;
}
/* ===== HEADER ===== */

.back-btn{
  background:#2563eb;
  color:#ffffff;
}

.back-btn:hover{
  background:#1d4ed8;
}


.header{
  display:flex;
  justify-content:space-between;
  align-items:center;
  margin-bottom:6px;
}

.header h1{
  margin:0;
  color:#2563eb;
  font-size:26px;
}

.subtitle{
  font-size:13px;
  color:#6b7280;
  margin-bottom:18px;
}

/* ===== SUB MENU ===== */
.subnav{
  display:flex;
  align-items:center;
  gap:22px;
  border-bottom:1px solid #e5e7eb;
  margin-bottom:22px;
  padding-bottom:10px;
}

.subnav a{
  text-decoration:none;
  font-weight:600;
  font-size:14px;
  color:#6b7280;
  padding-bottom:8px;
}

.subnav a.active{
  color:#2563eb;
  border-bottom:3px solid #2563eb;
}

.generate-btn{
  margin-left:auto;
  background:#2563eb;
  color:#fff;
  padding:10px 16px;
  border-radius:10px;
  font-weight:600;
  font-size:14px;
  text-decoration:none;
}

/* ===== SEARCH ===== */
.search-wrap{
  display:flex;
  gap:12px;
  background:#ffffff;
  padding:14px;
  border-radius:14px;
  box-shadow:0 8px 24px rgba(0,0,0,0.06);
  margin:20px 0;
}

.search-input{
  flex:1;
  padding:14px 16px;
  border-radius:10px;
  border:1px solid #e5e7eb;
  font-size:14px;
}

.search-input:focus{
  outline:none;
  border-color:#2563eb;
  box-shadow:0 0 0 3px rgba(37,99,235,0.15);
}
.search-row{
  display:flex;
  gap:12px;
  margin:15px 0 25px;
}

.search-row input{
  flex:1;
  padding:12px;
  border-radius:10px;
  border:1px solid #d1d5db;
  font-size:14px;
}

.search-row button{
  padding:12px 18px;
}
.date-input{
  background:#f9fafb;
  cursor:pointer;
}
.date-input{
  padding:14px 16px;
  border-radius:10px;
  border:1px solid #e5e7eb;
  font-size:14px;
}

.search-btn{
  padding:14px 20px;
}
/* ===== TABLE ===== */
table{
  width:100%;
  border-collapse:collapse;
}

th{
  text-align:left;
  font-size:13px;
  color:#2563eb;
  padding-bottom:10px;
  border-bottom:1px solid #e5e7eb;
}

td{
  padding:14px 6px;
  border-bottom:1px solid #f1f5f9;
  font-size:14px;
}

td strong{
  font-weight:600;
  color:#111827;
}

.amount{
  font-weight:600;
  color:#111827;
}

.issued{
  color:#6b7280;
  font-size:13px;
}

.actions a{
  margin-right:10px;
  font-size:13px;
  font-weight:600;
  color:#2563eb;
  text-decoration:none;
}
</style>
</head>

<body>
<div class="container">

  <!-- HEADER -->
  <div class="header">
  <div>
    <h1>Receipts</h1>
    <p style="margin:4px 0 0;color:#6b7280;font-size:14px;">
      Manage, search and download all generated receipts
    </p>
  </div>

  <a href="{{ url_for('home') }}" class="btn back-btn">← Back to Dashboard</a>
</div>


  <!-- SUB MENU -->
  <div class="subnav">
    <a href="{{ url_for('receipt_history') }}" class="active">All Receipts</a>
    <a href="{{ url_for('generate_receipt') }}" class="tab">Generate Receipt</a>

    
  </div>

  <!-- SEARCH -->
  <form method="get" class="search-row">
  <input
    name="q"
    value="{{ q }}"
    placeholder="Search receipt #, client or BL number"
  >

  <input
    type="date"
    name="date"
    value="{{ request.args.get('date','') }}"
  >

  <button class="btn">Search</button>
</form>

  <!-- TABLE -->
  <table>
    <tr>
      <th>Receipt #</th>
      <th>Client</th>
      <th>Date</th>
      <th>Amount</th>
      <th>Issued By</th>
      <th>Actions</th>
    </tr>

    {% for r in receipts %}
    <tr>
  <td><strong>{{ "%06d"|format(r.id) }}</strong></td>
  <td>{{ r.client.name }}</td>
  <td>{{ r.created_at.strftime("%d %b %Y") }}</td>
  <td class="amount">₵{{ "%.2f"|format(r.amount) }}</td>

  <td class="issued">
    {% if r.description and 'Issued by:' in r.description %}
      {{ r.description.split('Issued by:')[-1].strip() }}
    {% else %}
      —
    {% endif %}
  </td>

  <td class="actions">
    <a href="{{ url_for('receipt_preview', receipt_id=r.id) }}">Preview</a>
    <a href="{{ url_for('download_receipt_pdf', receipt_id=r.id) }}">PDF</a>
  </td>
</tr>
    {% endfor %}
  </table>

</div>
</body>
</html>
"""
RECEIPT_PREVIEW_HTML = """
<!doctype html>
<html>
<head>
<meta charset="utf-8">
<title>Receipt Preview</title>

<link href="https://fonts.googleapis.com/css2?family=Poppins:wght@400;600&display=swap" rel="stylesheet">

<style>
body{
  font-family:'Poppins',sans-serif;
  background:#f3f4f6;
  margin:0;
}

.wrap{
  max-width:900px;
  margin:30px auto;
}

.preview{
  position:relative;
  background:url('{{ url_for('static', filename='new_receipt.png') }}') no-repeat;
  background-size:100% auto;
  height:1120px;
  border-radius:6px;
  overflow:hidden;
}

.rno{ position:absolute; left:90px; top:260px; font-weight:600; }
.rdate{ position:absolute; right:90px; top:260px; }

.section{
  position:absolute;
  font-size:13px;
}

.table{
  position:absolute;
  left:90px;
  right:90px;
  top:420px;
}

table{
  width:100%;
  border-collapse:collapse;
}

th, td{
  padding:8px;
  border-bottom:1px solid #ddd;
}

.footer{
  position:absolute;
  left:90px;
  right:90px;
  bottom:120px;
  font-size:11px;
  color:#444;
}
</style>
</head>

<body>
<div class="wrap">
<div class="preview">

<div class="rno">Receipt #: {{ "%06d"|format(receipt.id) }}</div>
<div class="rdate">{{ today }}</div>

<div class="section" style="left:90px; top:300px;">
  <strong>Received From</strong><br>
  {{ client.name }}<br>
  {% if client.phone %}Phone: {{ client.phone }}<br>{% endif %}
  {% if client.email %}Email: {{ client.email }}{% endif %}
</div>

<div class="section" style="right:90px; top:300px; text-align:right;">
  <strong>Receipt Summary</strong><br>
  Transaction ID: {{ receipt.reference or 'CASH PAYMENT' }}<br>
  Currency: GHS<br>
  Payment Type: {{ receipt.method or '-' }}
</div>

<div class="table">
<table>
<tr>
  <th>#</th>
  <th>BL Number</th>
  <th>Description</th>
  <th>Amount</th>
</tr>

{% for rbl, bl in bl_rows %}
<tr>
  <td>{{ loop.index }}</td>
  <td>{{ bl.bl_number }}</td>
  <td>{{ receipt.description }}</td>
  <td>₵{{ "%.2f"|format(rbl.amount_applied) }}</td>
</tr>
{% endfor %}
</table>
</div>

<div class="footer">
  Generated by:
  {% if receipt.description and 'Issued by:' in receipt.description %}
    {{ receipt.description.split('Issued by:')[1].strip() }}
  {% else %}
    —
  {% endif %}
  | CargoBloc System | {{ today }}
</div>

</div>

<div style="margin-top:20px; display:flex; gap:12px;">
  <a href="{{ url_for('download_receipt_pdf', receipt_id=receipt.id) }}" class="btn">⬇ PDF</a>
  <a href="{{ url_for('receipt_history') }}" class="btn">← Back</a>
</div>

</div>
</body>
</html>
"""
RECEIPTS_HOME_HTML = """
<!doctype html>
<html>
<head>
<meta charset="utf-8">
<title>Receipts</title>
<link href="https://fonts.googleapis.com/css2?family=Poppins:wght@400;600&display=swap" rel="stylesheet">

<style>
body{
  font-family:'Poppins',sans-serif;
  background:url('{{ url_for('static', filename='homepage_bg.png') }}') no-repeat center center fixed;
  background-size:cover;
  margin:0;
}

.container{
  max-width:900px;
  margin:60px auto;
  background:rgba(255,255,255,0.96);
  border-radius:18px;
  padding:40px;
  text-align:center;
}

h1{ color:#2563eb; margin-bottom:30px; }

.grid{
  display:grid;
  grid-template-columns:1fr 1fr;
  gap:30px;
}

.card{
  background:#fff;
  padding:30px;
  border-radius:16px;
  box-shadow:0 10px 30px rgba(0,0,0,0.08);
  cursor:pointer;
  transition:.2s;
}

.card:hover{
  transform:translateY(-3px);
}

.card h3{
  color:#2563eb;
  margin-bottom:10px;
}

.card p{
  font-size:14px;
  color:#374151;
}

a{ text-decoration:none; color:inherit; }
</style>
</head>

<body>
<div class="container">
  <div class="header" style="display:flex; justify-content:space-between; align-items:center;">
  <h1>Receipts</h1>

  <a href="{{ url_for('home') }}"
     class="btn"
     style="
       background:rgba(255,255,255,0.75);
       color:#2563eb;
       border:1px solid rgba(37,99,235,0.25);
       backdrop-filter:blur(8px);
       font-weight:600;
     ">
    ← Back to Dashboard
  </a>
</div>

  <div class="grid">

    <a href="{{ url_for('generate_receipt') }}">
      <div class="card">
        <h3>➕ Generate Receipt</h3>
        <p>Create a new receipt for a client</p>
      </div>
    </a>

    <a href="{{ url_for('receipt_history') }}">
      <div class="card">
        <h3>📂 All Receipts</h3>
        <p>Search, preview and download past receipts</p>
      </div>
    </a>

  </div>
</div>
</body>
</html>
"""
BREAKDOWN_HTML = """<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<title>Cargo Breakdown</title>
<link href="https://fonts.googleapis.com/css2?family=Poppins:wght@400;600&display=swap" rel="stylesheet">
<style>
:root{
  --blue:#2563eb;
  --accent:#00AEEF;
  --green:#16a34a;
  --red:#dc2626;
  --muted:#6b7280;
}
body{
  margin:0;
  font-family:'Poppins',sans-serif;
  background:url('{{ url_for('static', filename='homepage_bg.png') }}') no-repeat center center fixed;
  background-size:cover;
}
.container{
  position:relative;
  max-width:980px;
  margin:40px auto;
  background:rgba(255,255,255,0.92);
  border-radius:14px;
  padding:22px;
  box-shadow:0 10px 30px rgba(0,0,0,0.12);
  overflow:hidden;
}
.watermark{
  position:absolute;
  top:50%;
  left:50%;
  transform:translate(-50%,-50%);
  opacity:0.06;
  pointer-events:none;
  z-index:0;
}
.watermark img{width:420px;height:auto}
h1{color:var(--blue);text-align:center;margin:6px 0 20px;font-size:24px;z-index:2;position:relative}
.card{
  margin-top:18px;
  padding:18px;
  background:white;
  border-radius:12px;
  box-shadow:0 6px 20px rgba(0,0,0,0.05);
  position:relative;
  z-index:2;
}
.title{
  font-size:20px;
  font-weight:600;
  margin-bottom:12px;
}
.grid{
  display:grid;
  grid-template-columns:1fr 1fr 1fr;
  gap:12px;
}
input{
  padding:10px;
  border-radius:8px;
  border:1px solid #d1d5db;
  width:100%;
  box-sizing:border-box;
}
button{
  display:inline-flex;
  align-items:center;
  justify-content:center;
  padding:10px 16px;
  border:none;
  border-radius:10px;
  font-weight:600;
  cursor:pointer;
  color:#fff;
  background:linear-gradient(135deg,var(--blue),var(--accent));
  transition:all 0.18s;
}
button:hover{opacity:0.9}
table{
  width:100%;
  border-collapse:separate;
  border-spacing:0 6px;
  margin-top:12px;
}
th{
  background:var(--blue);
  color:white;
  padding:10px;
  text-align:left;
  border-radius:8px;
}
td{
  background:white;
  padding:10px;
  border-radius:8px;
  box-shadow:0 4px 10px rgba(0,0,0,0.04);
}
.delete-btn{
  background:var(--red);
  color:white;
  border:none;
  padding:5px 10px;
  border-radius:6px;
  cursor:pointer;
}
.locked{
  background:#f3f4f6;
}
footer{
  text-align:center;
  color:var(--muted);
  font-size:13px;
  margin-top:18px;
}
textarea{
  padding:10px;
  border-radius:8px;
  border:1px solid #d1d5db;
  width:100%;
  box-sizing:border-box;
  resize:vertical;
  font-family:'Poppins',sans-serif;
}
td{
  white-space: pre-line;
  word-break: break-word;
}
button{
  margin-right:5px;
}
.action-cell button{
  padding:5px 10px;
  font-size:12px;
}
.back-btn{
  background:#6b7280;
  margin-bottom:10px;
}
.back-btn:hover{
  opacity:0.85;
}

</style>
</head>
<body>
<div class="container">
  <div class="watermark"><img src="{{ url_for('static', filename='logo.png') }}"></div>
  <button onclick="goBack()" class="back-btn">← Back</button>
  <h1>Cargo Breakdown</h1>

  <div class="card">
    <div class="title">Start Breakdown Session</div>
    <div class="grid">
      <input id="company" placeholder="Company Name">
      <input id="container" placeholder="Container No">
      <input id="vessel" placeholder="Vessel">
      <input id="voyage" placeholder="Voyage No">
    </div>
    <br>
    <button id="startBtn" onclick="startSession()">Start Session</button>
  </div>

  <div class="card">
    <div class="title">Add BL Entry</div>
    <div class="grid">
      <textarea id="bl" placeholder="BL Number" rows="2"></textarea>
      <textarea id="consignee" placeholder="Consignee" rows="2"></textarea>
      <textarea id="description" placeholder="Description" rows="3"></textarea>
      <input id="package" placeholder="Package">
      <textarea id="weight" placeholder="Weight" rows="2"></textarea>
    </div>
    <br>
    <button onclick="addBL()">Add BL</button>
  </div>

  <div class="card">
    <div class="title">Breakdown List</div>
    <table>
      <thead>
        <tr>
          <th>BL No</th>
          <th>Consignee</th>
          <th>Description</th>
          <th>Package</th>
          <th>Weight</th>
          <th>Action</th>
        </tr>
      </thead>
      <tbody id="tableBody"></tbody>
    </table>
    <br>
    <button onclick="generatePDF()">Generate PDF</button>
  </div>

  <footer>©️ 2026 CargoBloc Logistics</footer>
</div>

<script>
let sessionStarted = false;
let blList = [];

function startSession(){
  if(sessionStarted){alert("Session already started"); return;}
  const c=document.getElementById("container");
  const v=document.getElementById("vessel");
  const vg=document.getElementById("voyage");
  const btn=document.getElementById("startBtn");
  if(!c.value || !v.value || !vg.value){alert("Fill all session fields"); return;}
  sessionStarted=true;
  c.disabled=v.disabled=vg.disabled=true;
  c.classList.add("locked"); v.classList.add("locked"); vg.classList.add("locked");
  btn.disabled=true; btn.style.opacity=0.6; btn.style.cursor="not-allowed";
}

function addBL(){
  if(!sessionStarted){alert("Start session first"); return;}

  const data={
    bl:document.getElementById("bl").value,
    consignee:document.getElementById("consignee").value,
    description:document.getElementById("description").value,
    pkg:document.getElementById("package").value,
    weight:document.getElementById("weight").value
  };

  if(!data.bl || !data.consignee){
    alert("BL & Consignee required");
    return;
  }

  if(editIndex !== null){
    // UPDATE MODE
    blList[editIndex] = data;
    editIndex = null;
    document.querySelector("button[onclick='addBL()']").innerText = "Add BL";
  } else {
    // NORMAL ADD
    blList.push(data);
  }

  renderTable();
  clearInputs();
}

function renderTable(){
  const table=document.getElementById("tableBody");
  table.innerHTML="";
  blList.forEach((item,i)=>{
    table.innerHTML+=`<tr>
      <td>${item.bl}</td>
      <td>${item.consignee}</td>
      <td>${item.description}</td>
      <td>${item.pkg}</td>
      <td>${item.weight}</td>
      <td class="action-cell">
         <button onclick="editBL(${i})">Edit</button>
         <button class="delete-btn" onclick="del(${i})">X</button>
     </td>
    </tr>`;
  });
}

function del(i){blList.splice(i,1); renderTable();}
function clearInputs(){["bl","consignee","description","package","weight"].forEach(id=>document.getElementById(id).value="");}
function generatePDF(){
  if(!sessionStarted){ alert("Start session first"); return; }

  const payload = {
    company: document.getElementById("company").value,
    container: document.getElementById("container").value,
    vessel: document.getElementById("vessel").value,
    voyage: document.getElementById("voyage").value,
    blList: blList
  };

  fetch('/breakdown', {
    method: 'POST',
    headers: {'Content-Type': 'application/json'},
    body: JSON.stringify(payload)
  })
  .then(res => {
      if(!res.ok) throw new Error("Network response was not ok");
      return res.blob();
  })
  .then(blob => {
      const url = window.URL.createObjectURL(blob);
      const a = document.createElement('a');
      a.href = url;
      a.download = `Breakdown.pdf`;
      document.body.appendChild(a);
      a.click();
      a.remove();
      window.URL.revokeObjectURL(url);
  })
  .catch(err => alert("Failed to generate PDF: " + err));
}
let editIndex = null;

function editBL(i){
  const item = blList[i];

  document.getElementById("bl").value = item.bl;
  document.getElementById("consignee").value = item.consignee;
  document.getElementById("description").value = item.description;
  document.getElementById("package").value = item.pkg;
  document.getElementById("weight").value = item.weight;

  editIndex = i;

  // Change button text to Update
  document.querySelector("button[onclick='addBL()']").innerText = "Update BL";
}

function goBack(){
  window.history.back();
}

</script>
</body>
</html>
"""

# =========================================================
# HOME PAGE HTML
# =========================================================
INVOICES_HOME_HTML = """
<!doctype html>
<html>
<head>
<meta charset="utf-8">
<title>Invoices</title>
<link href="https://fonts.googleapis.com/css2?family=Poppins:wght@400;600&display=swap" rel="stylesheet">

<style>
body{
  font-family:'Poppins',sans-serif;
  background:url('{{ url_for('static', filename='homepage_bg.png') }}') no-repeat center center fixed;
  background-size:cover;
  margin:0;
}

.container{
  max-width:950px;
  margin:60px auto;
  background:rgba(255,255,255,0.96);
  border-radius:20px;
  padding:40px;
  box-shadow:0 20px 50px rgba(0,0,0,0.08);
}

.header{
  display:flex;
  justify-content:space-between;
  align-items:center;
  margin-bottom:30px;
  gap:16px;
}

.header h1{
  color:#2563eb;
  margin:0;
  font-size:30px;
}

.subtitle{
  color:#6b7280;
  margin-top:6px;
  font-size:14px;
}

.btn{
  background:#2563eb;
  color:#fff;
  padding:10px 16px;
  border-radius:12px;
  text-decoration:none;
  font-weight:600;
  white-space:nowrap;
}

.grid{
  display:grid;
  grid-template-columns:1fr 1fr;
  gap:30px;
}

.card{
  display:block;
  background:rgba(255,255,255,0.72);
  border:1px solid rgba(255,255,255,0.6);
  backdrop-filter:blur(12px);
  border-radius:20px;
  padding:30px;
  box-shadow:0 10px 30px rgba(0,0,0,0.08);
  transition:all 0.2s ease;
  text-decoration:none;
  color:inherit;
}

.card:hover{
  transform:translateY(-4px);
  box-shadow:0 18px 40px rgba(37,99,235,0.15);
}

.card h3{
  color:#2563eb;
  margin-top:0;
  margin-bottom:12px;
}

.card p{
  color:#4b5563;
  font-size:14px;
  line-height:1.6;
}

.card.coming-soon{
  opacity:0.75;
}

.card small{
  display:inline-block;
  margin-top:14px;
  padding:6px 10px;
  border-radius:999px;
  background:#fef3c7;
  color:#92400e;
  font-size:11px;
  font-weight:600;
}

@media (max-width: 768px){
  .container{
    margin:20px;
    padding:24px;
  }
  .grid{
    grid-template-columns:1fr;
  }
  .header{
    flex-direction:column;
    align-items:flex-start;
  }
}
</style>
</head>
<body>

<div class="container">
  <div class="header">
    <div>
      <h1>Invoice Center</h1>
      <div class="subtitle">Create, manage and export commercial and custom invoices</div>
    </div>

    <a href="{{ url_for('home') }}" class="btn">← Back to Dashboard</a>
  </div>

  <div class="grid">
    <a href="{{ url_for('generate_commercial_invoice') }}" class="card">
      <h3>📄 Create Commercial Invoice</h3>
      <p>
        Generate a professional commercial invoice with shipper, consignee,
        quantity, weight, FOB value and item details.
      </p>
    </a>

    <a href="#" class="card coming-soon">
      <h3>✨ Create Customized Invoice</h3>
      <p>
        Build advanced invoice templates with your own layout, logo,
        table style and custom sections.
      </p>
      <small>Coming Soon</small>
    </a>
  </div>
</div>

</body>
</html>
"""


# =========================================================
# INVOICE ENTRY HTML
# =========================================================
COMMERCIAL_INVOICE_UI_HTML = """
<!doctype html>
<html>
<head>
<meta charset="utf-8">
<title>Smart Commercial Invoice</title>
<link href="https://fonts.googleapis.com/css2?family=Poppins:wght@400;600&display=swap" rel="stylesheet">

<style>
body{
  font-family:'Poppins',sans-serif;
  background:url('{{ url_for('static', filename='homepage_bg.png') }}') no-repeat center center fixed;
  background-size:cover;
  margin:0;
}

.container{
  max-width:1100px;
  margin:40px auto;
  background:rgba(255,255,255,0.97);
  border-radius:20px;
  padding:30px;
}

.header{
  display:flex;
  justify-content:space-between;
  align-items:center;
  margin-bottom:25px;
}

.header h1{
  margin:0;
  color:#2563eb;
}

.btn{
  background:#2563eb;
  color:#fff;
  border:none;
  padding:10px 16px;
  border-radius:10px;
  font-weight:600;
  cursor:pointer;
  text-decoration:none;
}

.layout{
  display:grid;
  grid-template-columns:1fr 1fr;
  gap:20px;
}

.card{
  background:#fff;
  padding:20px;
  border-radius:16px;
  box-shadow:0 10px 30px rgba(0,0,0,0.06);
}

label{
  display:block;
  margin-top:10px;
  font-size:13px;
  font-weight:600;
}

input, textarea{
  width:100%;
  padding:10px;
  margin-top:5px;
  border-radius:8px;
  border:1px solid #ddd;
  font-family:'Poppins',sans-serif;
  box-sizing:border-box;
}

textarea{
  min-height:90px;
  resize:vertical;
}

.smart-box{
  min-height:180px;
  background:#f9fafb;
  border:2px dashed #cbd5e1;
}

button{
  margin-top:15px;
  width:100%;
  padding:12px;
  background:#2563eb;
  color:white;
  border:none;
  border-radius:10px;
  font-size:14px;
  cursor:pointer;
}

@media (max-width: 900px){
  .layout{
    grid-template-columns:1fr;
  }
  .container{
    margin:20px;
  }
  .header{
    flex-direction:column;
    align-items:flex-start;
  }
}
select{
  width:100%;
  padding:10px;
  margin-top:5px;
  border-radius:8px;
  border:1px solid #ddd;
  font-family:'Poppins',sans-serif;
  box-sizing:border-box;
}

input[type="color"]{
  height:50px;
  padding:4px;
  cursor:pointer;
}

.form-row{
  display:flex;
  gap:15px;
  margin-top:10px;
}

.form-group{
  flex:1;
}

</style>
</head>

<body>

<div class="container">

  <div class="header">
    <h1>Smart Freight Invoice AI</h1>
    <a href="{{ url_for('invoices_home') }}" class="btn">← Back</a>
  </div>

  <form method="post">

    <div class="layout">

      <div class="card">
        <h3>Shipper & Consignee</h3>

        <label>Shipper Name</label>
        <input name="shipper_name" required>

        <label>Shipper Address</label>
        <textarea name="shipper_address" required></textarea>

        <label>Consignee Name</label>
        <input name="consignee_name" required>

        <label>Consignee Address</label>
        <textarea name="consignee_address" required></textarea>

        <div class="form-row">

  <div class="form-group">
    <label>Container Number</label>
    <input name="container_no" placeholder="e.g. TRHU6850736" required>
  </div>

  <div class="form-group">
    <label>Container Size</label>
    <select name="container_size" required>
      <option value="20FT">20FT</option>
      <option value="40FT">40FT</option>
      <option value="40HQ">40HQ</option>
      <option value="45HQ">45HQ</option>
    </select>
  </div>

</div>

<label>Theme Color</label>
<input type="color" name="theme_color" value="#cfe8ff">

<label>Total Weight (KG)</label>
<input name="total_weight" required>
      </div>

      <div class="card">
        <h3>AI Freight Input (SMART MODE)</h3>

        <label>Type your cargo here:</label>
        <textarea class="smart-box" name="freight_input" placeholder="
USED CLOTHING GRADE C - 150KG
WOODEN BED - 2PCS
EMULSION PAINT - 150PCS X 4PCS X 5L
MATTRESS - 15PCS
" required></textarea>

        <button type="submit">Generate Smart Invoice</button>

        <p style="font-size:12px;color:gray;margin-top:10px;">
          AI will automatically assign FOB + distribute weight + calculate totals
        </p>
      </div>

    </div>

  </form>

</div>

</body>
</html>
"""


# =========================================================
# PREVIEW HTML
# =========================================================
PREVIEW_HTML = """
<!doctype html>
<html>
<head>
<meta charset="utf-8">
<title>Invoice Preview</title>
<style>
body{
  font-family:'Poppins',sans-serif;
  background:url('{{ url_for('static', filename='homepage_bg.png') }}') no-repeat center center fixed;
  background-size:cover;
  margin:0;
  padding:30px;
}

.box{
  max-width:1000px;
  margin:40px auto;
  background:rgba(255,255,255,0.96);
  padding:30px;
  border-radius:20px;
  box-shadow:0 20px 50px rgba(0,0,0,0.10);
  backdrop-filter:blur(10px);
}

.loading{
  text-align:center;
  padding:40px;
  font-size:18px;
}

table{
  width:100%;
  border-collapse:collapse;
  margin-top:20px;
}

td, th{
  border:1px solid #ddd;
  padding:8px;
  text-align:left;
}

th{
  background:{{ theme_color }};
}}

button{
  margin-top:25px;
  padding:14px 20px;
  background:linear-gradient(135deg, #2563eb, #1d4ed8);
  color:white;
  border:none;
  border-radius:12px;
  cursor:pointer;
  font-size:14px;
  font-weight:600;
  letter-spacing:0.3px;
  box-shadow:0 10px 25px rgba(37,99,235,0.25);
  transition:all 0.2s ease;
}

button:hover{
  transform:translateY(-2px);
  box-shadow:0 14px 30px rgba(37,99,235,0.35);
}

.meta{
  margin-top:10px;
  line-height:1.7;
}

.meta{
  margin-top:10px;
  line-height:1.7;
  padding:16px;
  border-radius:10px;
  background:{{ theme_color }};
}
.top-bar{
  display:flex;
  justify-content:space-between;
  align-items:center;
  margin-bottom:20px;
}

.back-btn{
  display:inline-block;
  padding:10px 16px;
  background:#ffffff;
  color:#2563eb;
  border:1px solid #dbeafe;
  border-radius:10px;
  text-decoration:none;
  font-weight:600;
  box-shadow:0 4px 12px rgba(0,0,0,0.05);
  transition:all 0.2s ease;
}

.back-btn:hover{
  background:#eff6ff;
  transform:translateY(-1px);
}
.qty-input,
.fob-input,
.weight-input{
  width:90px;
  padding:6px;
  border:1px solid #d1d5db;
  border-radius:6px;
}

.totals-box{
  margin-top:25px;
  padding:18px;
  background:#f8fafc;
  border-radius:12px;
  border:1px solid #e5e7eb;
  line-height:2;
  font-size:14px;
}

</style>
</head>
<body>
<div class="box">

  <div id="loading" class="loading">
    🤖 Manuel-AI is processing invoice... please wait
  </div>

  <div id="content" style="display:none;">
    
  <div class="top-bar">
  <a href="{{ url_for('generate_commercial_invoice') }}" class="back-btn">← Back</a>
  <h2 style="margin:0;">Commercial Invoice Preview</h2>
</div>
    <h2>Commercial Invoice Preview</h2>

    <div class="meta">
      <strong>SHIPPER:</strong> {{ shipper_name }}<br>
      <strong>SHIPPER ADDRESS:</strong> {{ shipper_address }}<br><br>

      <strong>CONSIGNEE:</strong> {{ consignee_name }}<br>
      <strong>CONSIGNEE ADDRESS:</strong> {{ consignee_address }}<br><br>

      <strong>CONTAINER:</strong> {{ container_no }}<br>
      <strong>CONTAINER SIZE:</strong> {{ container_size }}<br>
      <strong>TOTAL WEIGHT:</strong> {{ total_weight }} KG<br>
    </div>

    <table>
      <tr>
        <th>Item</th>
        <th>Description</th>
        <th>Qty</th>
        <th>FOB</th>
        <th>Amount</th>
        <th>Weight</th>
      </tr>

      {% for i in items %}
      <tr>
        <td>{{ loop.index }}</td>
        <td>{{ i.desc }}</td>
        <td>
  <input type="number" class="qty-input" value="{{ i.qty }}" step="0.01">
</td>
<td>
  <input type="number" class="fob-input" value="{{ '%.2f'|format(i.unit) }}" step="0.01">
</td>
<td class="amount-cell">{{ '%.2f'|format(i.value) }}</td>
<td>
  <input type="number" class="weight-input" value="{{ i.weight }}" step="0.01">
</td>
      </tr>
      {% endfor %}
    </table>
    <div class="totals-box">
  <div><strong>FOB VALUE:</strong> USD <span id="fobTotal">0.00</span></div>
  <div><strong>FREIGHT:</strong> USD <span id="freightTotal">300.00</span></div>
  <div><strong>C & F VALUE:</strong> USD <span id="cfTotal">0.00</span></div>
  <div><strong>TOTAL WEIGHT:</strong> <span id="weightTotal">0</span> KG</div>
</div>

    <form action="/export_invoice_pdf" method="post">
      <input type="hidden" name="shipper_name" value="{{ shipper_name|e }}">
      <input type="hidden" name="shipper_address" value="{{ shipper_address|e }}">
      <input type="hidden" name="consignee_name" value="{{ consignee_name|e }}">
      <input type="hidden" name="consignee_address" value="{{ consignee_address|e }}">
      <input type="hidden" name="container_no" value="{{ container_no|e }}">
      <input type="hidden" name="total_weight" value="{{ total_weight }}">
      <input type="hidden" name="container_size" value="{{ container_size|e }}">
      <input type="hidden" name="theme_color" value="{{ theme_color|e }}">
      <textarea name="freight_input" hidden>{{ freight_input }}</textarea>

      <button type="submit">Print / Download PDF</button>
    </form>

  </div>
</div>

<script>
setTimeout(() => {
  document.getElementById("loading").style.display = "none";
  document.getElementById("content").style.display = "block";
}, 20000);
</script>
<script>
function recalculateTotals(){
  let rows = document.querySelectorAll("table tr");
  let totalFOB = 0;
  let totalWeight = 0;
  let freight = 300;

  rows.forEach((row, index) => {
    if(index === 0) return;

    let qtyInput = row.querySelector('.qty-input');
    let fobInput = row.querySelector('.fob-input');
    let weightInput = row.querySelector('.weight-input');
    let amountCell = row.querySelector('.amount-cell');

    if(qtyInput && fobInput && weightInput){
      let qty = parseFloat(qtyInput.value) || 0;
      let fob = parseFloat(fobInput.value) || 0;
      let weight = parseFloat(weightInput.value) || 0;

      let amount = qty * fob;
      amountCell.innerText = amount.toFixed(2);

      totalFOB += amount;
      totalWeight += weight;
    }
  });

  document.getElementById('fobTotal').innerText = totalFOB.toFixed(2);
  document.getElementById('cfTotal').innerText = (totalFOB + freight).toFixed(2);
  document.getElementById('weightTotal').innerText = totalWeight.toFixed(2);
}

window.addEventListener('load', () => {
  document.querySelectorAll('.qty-input, .fob-input, .weight-input').forEach(input => {
    input.addEventListener('input', recalculateTotals);
  });

  recalculateTotals();
});
</script>

</body>
</html>
"""

if __name__ == '__main__':
    os.makedirs(app.config['UPLOAD_FOLDER'], exist_ok=True)
    with app.app_context():
        db.create_all()
    print("✅ Default login → admin / Cargo@conso123")
    # debug=False in production; leave True for local troubleshooting
    app.run()
