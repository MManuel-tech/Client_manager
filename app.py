# app.py — CLEAN IMPORTS
import os
import shutil
import logging
from io import BytesIO
from datetime import datetime, timedelta

from flask import (
    Flask, request, redirect, url_for,
    send_from_directory, send_file,
    render_template_string, abort
)

from flask_sqlalchemy import SQLAlchemy
from flask_login import (
    LoginManager, UserMixin,
    login_user, logout_user,
    login_required, current_user
)

from flask_wtf.csrf import CSRFProtect
from flask_limiter import Limiter
from flask_limiter.util import get_remote_address

from werkzeug.utils import secure_filename
from werkzeug.security import generate_password_hash, check_password_hash

from reportlab.pdfgen import canvas
from reportlab.lib.pagesizes import A4
from reportlab.lib.utils import ImageReader
from reportlab.lib.units import mm


app = Flask(__name__)
app.secret_key = 'cargobloc_secret_key'
app.config['SQLALCHEMY_DATABASE_URI'] = 'sqlite:///clients.db'
app.config['UPLOAD_FOLDER'] = 'uploads'
app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False


csrf = CSRFProtect(app)
app.secret_key = os.environ.get("SECRET_KEY", "CHANGE_THIS_IN_PRODUCTION")
limiter = Limiter(
    key_func=get_remote_address,
    app=app,
    default_limits=["200 per day", "50 per hour"]
)
logging.basicConfig(
    filename="security.log",
    level=logging.WARNING,
    format="%(asctime)s %(levelname)s %(message)s"
)
app.config.update(
    SESSION_COOKIE_HTTPONLY=True,   # JS can't read cookies
    SESSION_COOKIE_SAMESITE="Lax",   # prevents CSRF
    SESSION_COOKIE_SECURE=not app.debug     
)

def get_static_file(filename):
    """Return absolute path to a file in the `static/` folder."""
    return os.path.join(current_app.root_path, 'static', filename)

def link_callback(uri, rel):
    if uri.startswith('/static/'):
        path = os.path.join(current_app.root_path, uri.lstrip('/'))
        return path
    return uri
db = SQLAlchemy(app)
login_manager = LoginManager(app)
login_manager.login_view = 'login'

ALLOWED_EXTENSIONS = {"pdf", "jpg", "jpeg", "png", "docx"}

def allowed_file(filename):
    return (
        "." in filename and
        filename.rsplit(".", 1)[1].lower() in ALLOWED_EXTENSIONS
    )

# -----------------------
# MODELS
# -----------------------
class User(UserMixin, db.Model):
    id = db.Column(db.Integer, primary_key=True)
    username = db.Column(db.String(50), unique=True, nullable=False)
    password = db.Column(db.String(255), nullable=False)

class Client(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(120))
    email = db.Column(db.String(120))
    phone = db.Column(db.String(50))
    notes = db.Column(db.Text)
    bls = db.relationship('BL', backref='client', cascade="all, delete-orphan")
    documents = db.relationship('ClientDocument', backref='client', cascade="all, delete-orphan")

class BL(db.Model):
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
# LOGIN MANAGEMENT
# -----------------------
@login_manager.user_loader
def load_user(user_id):
    return User.query.get(int(user_id))

@app.before_first_request
def create_default_user():
    # create admin if DB empty; password requested earlier: Cargo@conso123
    if not User.query.first():
        db.session.add(
          User(
            username='admin',
            password=generate_password_hash('Cargo@conso123')
          )
        )
        db.session.commit()
        print("✅ Default login → username: admin | password: Cargo@conso123")
#----------------------
#BACKUP       
#-----------------------

def backup_database():
    if not os.path.exists("backups"):
        os.makedirs("backups")

    timestamp = datetime.now().strftime("%Y-%m-%d_%H-%M-%S")
    shutil.copy("clients.db", f"backups/clients_{timestamp}.db")

    print("✅ Database backup created")
# -----------------------
# ROUTES
# -----------------------
@limiter.limit("5 per minute")
@app.route('/login', methods=['GET', 'POST'])
def login():
    error = None
    if request.method == 'POST':
        username = request.form.get('username', '').strip()
        password = request.form.get('password', '').strip()
        remember = request.form.get('remember') == 'on'

        user = User.query.filter_by(username=username).first()

        if user and check_password_hash(user.password, password):
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

    clients = query.order_by(Client.name).all()

    total_billed = sum(sum((bl.amount_total or 0) for bl in c.bls) for c in clients)
    total_paid = sum(sum((bl.amount_paid or 0) for bl in c.bls) for c in clients)
    total_unpaid = total_billed - total_paid

    activities = []

    # recent clients (no created_at → fake datetime)
    for c in Client.query.order_by(Client.id.desc()).limit(3):
        activities.append({
            "text": f"Client added: {c.name}",
            "time": datetime.utcnow() - timedelta(days=365 + c.id)
        })

    # recent receipts
    for r in Receipt.query.order_by(Receipt.created_at.desc()).limit(3):
        activities.append({
            "text": f"Receipt generated for {r.client.name} (₵{r.amount:,.2f})",
            "time": r.created_at
        })

    # recent house BLs
    for h in HouseBL.query.order_by(HouseBL.created_at.desc()).limit(2):
        activities.append({
            "text": f"House BL created: {h.bl_number}",
            "time": h.created_at
        })

    # safe sort (ALL are datetimes now)
    activities.sort(key=lambda x: x["time"], reverse=True)

    return render_template_string(
        HOME_HTML,
        clients=clients,
        total_billed=total_billed,
        total_paid=total_paid,
        total_unpaid=total_unpaid,
        activities=activities,
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

    # ===== FINANCE SUMMARY (ALWAYS RUNS) =====
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

    
    if request.method == 'POST':
        action = request.form.get('action')

        # ===== ADD SINGLE BL =====
        if action == 'add_bl':
            bl_number = request.form.get('bl_number', '').strip()

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
            return redirect(url_for('client_detail', client_id=client.id))
      


        # ===== RECORD PAYMENT =====
        elif action == 'record_payment':
            try:
                bl_id = int(request.form.get('bl_id'))
                extra_payment = float(request.form.get('extra_payment') or 0)
            except:
                bl_id = None
                extra_payment = 0

            bl = BL.query.get(bl_id) if bl_id else None
            if bl:
                bl.amount_paid = (bl.amount_paid or 0) + extra_payment
                db.session.commit()

            return redirect(url_for('client_detail', client_id=client.id))


        # ===== ADD MULTIPLE BLs AT ONCE =====
        elif action == 'add_multi_bl':
            bl_numbers = request.form.getlist('bl_number[]')
            totals = request.form.getlist('amount_total[]')
            paids = request.form.getlist('amount_paid[]')

            for i in range(len(bl_numbers)):
                bl_number = bl_numbers[i].strip()
                if not bl_number:
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

            db.session.commit()
            return redirect(url_for('client_detail', client_id=client.id))


        # ===== UPLOAD CLIENT DOCUMENT =====
        elif action == 'add_doc':
            file = request.files.get('client_document')
            if file and file.filename:
                filename = secure_filename(file.filename)
                os.makedirs(app.config['UPLOAD_FOLDER'], exist_ok=True)
                file.save(os.path.join(app.config['UPLOAD_FOLDER'], filename))
                desc = request.form.get('doc_desc', '')
                db.session.add(ClientDocument(filename=filename, description=desc, client=client))
                db.session.commit()

            return redirect(url_for('client_detail', client_id=client.id))


        # ===== EXPORT SELECTED BLs =====
        elif action == 'export_selected_bl':
            bl_ids = request.form.getlist('bl_ids')

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
    if current_user.username != "admin":
        abort(403)

    client = Client.query.get_or_404(client_id)
    db.session.delete(client)
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
        from flask import flash
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
    from reportlab.pdfgen import canvas
    from reportlab.lib.pagesizes import A4
    from PyPDF2 import PdfReader, PdfWriter
    import io

    hbl = HouseBL.query.get_or_404(hbl_id)

    # === Setup paths ===
    base_template = os.path.join(app.config['UPLOAD_FOLDER'], 'CARGOBLOC_HOUSE_BL_TEMPLETE[1].pdf')
    export_filename = f"HouseBL_{hbl.bl_number}_{datetime.now().strftime('%Y%m%d%H%M%S')}.pdf"
    export_path = os.path.join(app.config['UPLOAD_FOLDER'], export_filename)
    letterhead_path = os.path.join(app.config['UPLOAD_FOLDER'], 'letterhead_receipt.pdf')
    stamp_path = os.path.join(app.config['UPLOAD_FOLDER'], 'paid_stamp.png')
    
    # Ensure template exists
    if not os.path.exists(base_template):
        return f"❌ Template not found: {base_template}", 404

    # === Step 1: Create overlay with ReportLab ===
    packet = io.BytesIO()
    c = canvas.Canvas(packet, pagesize=A4)
    c.setFont("Helvetica", 7)

    # === Step 1: Create overlay with ReportLab ===
    packet = io.BytesIO()
    c = canvas.Canvas(packet, pagesize=A4)
    c.setFont("Helvetica", 7)

    # 🔧 Helper function for wrapping text inside a rectangle
    from textwrap import wrap
    def draw_wrapped_text(x, y, text, width_chars=50, line_height=9):
        """Draw multi-line text starting at (x, y) that wraps after width_chars."""
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

from io import BytesIO
from flask import send_file
from reportlab.pdfgen import canvas
from reportlab.lib.units import mm





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

        desc_type = request.form.get('description_type')
        custom_desc = request.form.get('custom_description', '').strip()

        if desc_type == 'custom' and custom_desc:
            final_description = custom_desc
        else:
            final_description = desc_type

        client_id = int(request.form.get('client_id'))
        total_amount = float(request.form.get('amount'))
        issued_by = request.form.get('issued_by')
        payment_type = request.form.get('payment_type')
        transaction_id = request.form.get('transaction_id')

        receipt = Receipt(
          client_id=client_id,
          amount=total_amount,
          method=payment_type,
          reference=transaction_id,
          description=f"{final_description} | Issued by: {issued_by}"
        )
        db.session.add(receipt)
        db.session.flush()

        remaining = total_amount
        bl_ids = request.form.getlist('bl_ids')

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
    backup_database()

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

    return render_template_string(RECEIPTS_HOME_HTML)
from reportlab.lib.pagesizes import A4
from reportlab.lib.utils import ImageReader
from io import BytesIO
from datetime import datetime
from textwrap import wrap



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

@app.route('/receipts')
@login_required
def receipts_home():
    return render_template_string(RECEIPTS_HOME_HTML)

@app.route('/receipt/pdf/<int:receipt_id>')
@login_required
def download_receipt_pdf(receipt_id):

    from reportlab.pdfgen import canvas
    from reportlab.lib.pagesizes import A4
    from reportlab.lib.utils import ImageReader
    from reportlab.lib import colors
    from io import BytesIO
    from datetime import datetime
    import os
    from reportlab.lib import colors
    CARGOBLOC_TEAL = colors.HexColor("#9edfe0")


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
        bg = ImageReader(bg_path)
        c.drawImage(bg, 0, 0, width=width, height=height)

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

    # ----- RECEIVED FROM -----
    # Header bar
    c.setFillColor(teal)
    c.rect(left_x, box_y + box_h - 26, box_w, 26, fill=1, stroke=0)

    # Heading text (WHITE)
    c.setFillColor(colors.white)
    c.setFont("Helvetica-Bold", 11)
    c.drawString(left_x + 10, box_y + box_h - 18, "RECEIVED FROM")
    

    #line 
    c.setStrokeColor(CARGOBLOC_TEAL)
    c.setLineWidth(1)
    c.rect(left_x, box_y, box_w, box_h, stroke=1, fill=0)
    c.setFillColor(colors.black)

    # Body text
    c.setFont("Helvetica-Bold", 11)
    c.drawString(left_x + 10, box_y + box_h - 45, client.name)

    c.setFont("Helvetica", 10)
    if client.phone:
        c.drawString(left_x + 10, box_y + box_h - 65, f"Phone: {client.phone}")

    if client.email:
        c.drawString(left_x + 10, box_y + box_h - 82, f"Email: {client.email}")

    # ----- RECEIPT SUMMARY -----
    # Header bar
    c.setFillColor(teal)
    c.rect(right_x, box_y + box_h - 26, box_w, 26, fill=1, stroke=0)

    # Heading text (WHITE)
    c.setFillColor(colors.white)
    c.setFont("Helvetica-Bold", 11)
    c.drawString(right_x + 10, box_y + box_h - 18, "RECEIPT SUMMARY")

    # Box outline (CARGOBLOC BLUE)
    c.setStrokeColor(CARGOBLOC_TEAL)
    c.setLineWidth(1)
    c.rect(right_x, box_y, box_w, box_h, stroke=1, fill=0)
    c.setFillColor(colors.black)

    # Body text
    c.setFont("Helvetica", 10)
    c.drawString(right_x + 10, box_y + box_h - 45, "Transaction ID:")
    c.drawRightString(
        right_x + box_w - 10,
        box_y + box_h - 45,
        receipt.reference or "—"
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
    
# AttributeError: 'Receipt' object has no attribute 'client_name'
# -----------------------
# PDF helpers
# -----------------------
def draw_multiline(c, text, x, y, width, line_height=10):
    """
    Draws multi-line text in a limited width box.
    Automatically wraps long text based on word width.
    """
    if not text:
        return
    c.setFont("Helvetica", 7)
    words = text.split()
    line = ""
    offset = 0
    for word in words:
        test_line = f"{line} {word}".strip()
        if c.stringWidth(test_line, "Helvetica", 7) <= width:
            line = test_line
        else:
            c.drawString(x, y - offset, line)
            offset += line_height
            line = word
    if line:
        c.drawString(x, y - offset, line)
def create_bl_pdf(client, bls, pdf_path):
    from reportlab.lib.pagesizes import A4
    from reportlab.pdfgen import canvas
    from reportlab.lib.utils import ImageReader
    import os

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
    c.drawString(LEFT_MARGIN, height - TOP_MARGIN + 30, "Client Summary")
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
        dates = [bl.created_at for bl in bls if bl.created_at]
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
    --deep-blue: #2563eb;
    --light-blue: #cfeaff;
    --accent: #00AEEF;
    --text: #0b1220;
  }
  html,body{height:100%; margin:0; font-family:'Poppins',sans-serif; color:var(--text);}

  /* page background (wide port image) */
  body{
    background: url('{{ url_for('static', filename='homepage_bg.png') }}') no-repeat center center fixed;
    background-size: cover;
    -webkit-font-smoothing:antialiased;
    -moz-osx-font-smoothing:grayscale;
  }

  /* centered overlay container */
  .page-wrap{
    max-width:1200px;
    margin:36px auto;
    display:grid;
    grid-template-columns: 220px 1fr;
    gap:20px;
    align-items:start;
    padding:18px;
  }

  /* SIDEBAR */
  .sidebar{
    background: var(--light-blue);
    border-radius:12px;
    padding:18px;
    box-shadow:0 10px 30px rgba(13,27,56,0.08);
    height: calc(100vh - 100px);
    box-sizing:border-box;
    display:flex;
    flex-direction:column;
    gap:18px;
  }
  .brand{
    display:flex; align-items:center; gap:12px;
  }
  .brand img{ height:44px; width:auto; border-radius:6px; }
  .brand h3{ margin:0; font-size:16px; font-weight:700; color:var(--text); }
  .nav{ margin-top:6px; display:flex; flex-direction:column; gap:8px; }
  .nav a{
    display:block; padding:10px 12px; border-radius:8px; color:var(--text); text-decoration:none; font-weight:600;
  }
  .nav a.active{ background: rgba(255,255,255,0.25); }

  .sidebar .small{ font-size:13px; color:rgba(11,17,32,0.7); margin-top:auto; }

  /* MAIN PANEL */
  .main{
    background: rgba(255,255,255,0.92);
    border-radius:12px;
    padding:22px;
    box-shadow:0 10px 30px rgba(3,7,18,0.08);
    position:relative;
    overflow:hidden;
  }

  /* watermark (centered) */
  .watermark{
    position:absolute;
    left:50%;
    top:42%;
    transform:translate(-50%,-50%);
    opacity:0.06;
    pointer-events:none;
    width:560px;
    max-width:70%;
    filter: blur(0.4px);
  }

  header.top{
    display:flex;
    gap:12px;
    align-items:center;
    margin-bottom:14px;
  }
  header.top h1{ font-size:18px; margin:0; font-weight:700; color:var(--deep-blue); }
  .header-actions{ margin-left:auto; display:flex; gap:8px; align-items:center; }

  /* cards row */
  .cards{ display:flex; gap:12px; margin-bottom:18px; flex-wrap:wrap; }
  .card{
    background:#fff; border-radius:10px; padding:16px; min-width:180px;
    box-shadow:0 6px 18px rgba(3,7,18,0.04); flex:1;
  }
  .card h4{ margin:0 0 8px 0; font-size:13px; color:rgba(11,17,32,0.7); }
  .card .value{ font-size:20px; font-weight:700; color:var(--text); }

  /* add-client panel */
  .add-client{ background:#fff; border-radius:10px; padding:12px; margin-bottom:16px; box-shadow:0 6px 18px rgba(3,7,18,0.04); }
  .add-client input, .add-client textarea{
    width:100%; padding:10px; border-radius:8px; border:1px solid #e6eefb; margin:8px 0; box-sizing:border-box;
  }

  /* search & clients list */
  .search-row{ display:flex; gap:8px; align-items:center; margin-bottom:12px; }
  .search-row input{ padding:10px 12px; border-radius:8px; border:1px solid #e6eefb; width:320px; }
  .client-list{ display:flex; flex-direction:column; gap:10px; max-height:360px; overflow:auto; padding-right:6px; }

  .client-item{
    display:flex; justify-content:space-between; align-items:center;
    background:#fafafa; padding:12px; border-radius:8px; border:1px solid #eef6ff;
  }
  .client-item .meta{ font-weight:600; }
  .client-item .meta small{ display:block; font-weight:400; color:#6b7280; margin-top:4px; font-size:13px; }

  /* === CARGOBLOC GLASS BUTTONS — Refined Look === */
button,
.btn,
.client-actions a {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  gap: 6px;
  padding: 10px 16px;
  font-size: 14px;
  font-weight: 600;
  border-radius: 12px;
  border: 1.5px solid rgba(255,255,255,0.25);
  background: rgba(255,255,255,0.15);
  color: #0b1220;
  backdrop-filter: blur(10px);
  text-decoration: none;
  box-shadow: 0 6px 20px rgba(0,0,0,0.08);
  cursor: pointer;
  transition: all 0.25s ease;
}

/* 🔵 Core Blue Buttons — Add Client, Open, Refresh */
button[type="submit"],
.client-actions .open,
.add-client button {
  color: #fff;
  background: linear-gradient(135deg, rgba(37,99,235,0.85), rgba(0,174,239,0.85));
  border: none;
  box-shadow: 0 6px 18px rgba(37,99,235,0.25);
}
button[type="submit"]:hover,
/* === Make Open and Delete buttons slightly smaller === */
.client-actions .open,
.client-actions .delete {
  padding: 5px 10px;      /* smaller button size */
  font-size: 12.5px;      /* reduce text size slightly */
  border-radius: 8px;     /* slightly less rounded */
  transform: scale(0.95); /* overall compact feel */
}

.client-actions .open:hover,
.client-actions .delete:hover {
  transform: scale(0.98) translateY(-1px); /* smooth hover lift */
}
/* 🟦 Export / New Report / Secondary Buttons */
.btn {
  color: #00AEEF;
  background: rgba(255,255,255,0.25);
  border: 1.5px solid rgba(0,174,239,0.35);
  backdrop-filter: blur(10px);
  box-shadow: 0 4px 12px rgba(0,174,239,0.25);
}
.btn:hover {
  background: linear-gradient(135deg, rgba(0,174,239,0.15), rgba(37,99,235,0.15));
  transform: translateY(-2px);
}

/* 🔴 Delete Button */
.client-actions .delete {
  background: rgba(220,38,38,0.2);
  color: #b91c1c;
  border: 1.5px solid rgba(220,38,38,0.3);
  box-shadow: 0 4px 10px rgba(220,38,38,0.15);
}
.client-actions .delete:hover {
  background: rgba(220,38,38,0.35);
  transform: translateY(-2px);
  box-shadow: 0 6px 16px rgba(220,38,38,0.25);
}

/* 🔍 Search Fields with Glass Blur */
.search-row input {
  background: rgba(255,255,255,0.3);
  border: 1px solid rgba(255,255,255,0.5);
  backdrop-filter: blur(8px);
  color: #0b1220;
  border-radius: 10px;
  padding: 10px 14px;
  transition: all 0.25s ease;
}
.search-row input:focus {
  outline: none;
  border-color: #00AEEF;
  box-shadow: 0 0 10px rgba(0,174,239,0.35);
}
  footer{ margin-top:18px; text-align:center; color:#6b7280; font-size:13px; }

  /* small responsive tweak */
  @media(max-width:980px){
    .page-wrap{ grid-template-columns: 1fr; padding:14px; }
    .sidebar{ height:auto; order:2; display:flex; flex-direction:row; gap:10px; padding:10px; align-items:center; }
    .main{ order:1; margin-bottom:20px; }
    .watermark{ width:380px; opacity:0.04; top:46%; }
  }
</style>
</head>
<body>
  <div class="page-wrap">

    <!-- SIDEBAR -->
    <aside class="sidebar">
      <div class="brand">
        <img src="{{ url_for('static', filename='logo.png') }}" alt="logo">
        <div>
          <h3>CargoBloc</h3>
          <div style="font-size:12px;color:rgba(11,17,32,0.75);">Logistics Suite</div>
        </div>
      </div>

      <nav class="nav">
        <a href="#" class="active">Dashboard</a>
        <a href="{{ url_for('clients_page') }}">Clients</a>
        <a href="{{ url_for('receipts_home') }}">Receipts</a>
        <a href="{{ url_for('house_bl') }}">House BLs</a>
        <a href="{{ url_for('logout') }}">Logout</a>
      </nav>

      <div class="small">Contact: +233 53 055 8275 • info@cargobloc.world</div>
    </aside>

    <!-- MAIN -->
    <main class="main">
      <!-- centered watermark using the same logo (transparent) -->
      <img src="{{ url_for('static', filename='logo.png') }}" class="watermark" alt="watermark">

      <header class="top">
        <h1>Dashboard</h1>
        <div class="header-actions">
          
        </div>
      </header>

      <section class="cards">
        <div class="card">
          <h4>Total Billed</h4>
          <div class="value">₵{{ '%.2f'|format(total_billed) }}</div>
        </div>
        <div class="card">
          <h4>Total Paid</h4>
          <div class="value">₵{{ '%.2f'|format(total_paid) }}</div>
        </div>
        <div class="card">
          <h4>Unpaid</h4>
          <div class="value">₵{{ '%.2f'|format(total_unpaid) }}</div>
        </div>
      </section>

      <section style="display:flex; gap:18px; flex-wrap:wrap;">

  <!-- LEFT COLUMN -->
  <div style="flex:0.45; min-width:320px;">

    <div class="add-client">
      <h4 style="margin:0 0 8px 0;">➕ Add New Client</h4>
      <form method="post" action="{{ url_for('add_client') }}">
        <input name="name" placeholder="Client Name" required>
        <input name="email" placeholder="Email">
        <input name="phone" placeholder="Phone">
        <textarea name="notes" placeholder="Notes" rows="3"></textarea>
        <button type="submit">Add Client</button>
      </form>
    </div>

    <div style="margin-top:12px;">
      <h4 style="margin:4px 0 8px 0;">Clients</h4>

      <!-- Short summary + link to full clients page -->
      <div style="background:#fff; padding:12px; border-radius:10px; box-shadow:0 6px 18px rgba(3,7,18,0.04);">
        <p style="margin:0 0 8px 0; color:#374151;">
          Manage all clients on the dedicated Clients page.
          <br><small style="color:#6b7280;">(Open, add BLs, export and more)</small>
        </p>

        <div style="margin-top:10px; display:flex; gap:8px; align-items:center;">
          <a href="{{ url_for('clients_page') }}"
             class="btn"
             style="background:var(--accent); color:#fff; padding:8px 12px; border-radius:8px; text-decoration:none;">
             Open Clients Page
          </a>

          <a href="{{ url_for('client_detail', client_id=clients[0].id) if clients else url_for('generate_receipt') }}"
             style="color:#2563eb; text-decoration:none; font-weight:600;">
            Quick: Open first client
          </a>
        </div>
      </div>
    </div>

  </div> <!-- ✅ END LEFT COLUMN -->


  <!-- RIGHT COLUMN -->
  <div style="flex:0.5; min-width:320px;">

    <div class="card" style="margin-bottom:12px;">
      <h4>Quick Actions</h4>
      <div style="display:flex; gap:8px; flex-wrap:wrap; margin-top:8px;">
        <a href="{{ url_for('home') }}" class="btn"
           style="background:var(--deep-blue); color:#fff; padding:8px 12px; border-radius:8px; text-decoration:none;">
           Refresh
        </a>

        <a href="{{ url_for('export_all_filtered', date=request.args.get('date', '')) }}"
           class="btn"
           style="background:#fff; border:1px solid #e6eefb; padding:8px 12px; border-radius:8px; text-decoration:none; color:var(--text);">
          Export All
        </a>

        <a href="#" class="btn"
           style="background:var(--accent); color:#fff; padding:8px 12px; border-radius:8px; text-decoration:none;">
          New Report
        </a>
      </div>
    </div>

    <!-- ✅ RECENT ACTIVITY (NOW STAYS ON RIGHT) -->
    <div class="card">
  <h4>Recent Activity</h4>

  <div style="
    font-size:13px;
    color:#374151;
    max-height:280px;
    overflow-y:auto;
    padding-right:6px;
  ">

    {% for a in activities %}
      <div style="
        padding:10px 0;
        border-bottom:1px dashed #eef6ff;
        display:flex;
        flex-direction:column;
        gap:2px;
      ">
        <span>{{ a.text }}</span>
        {% if a.time %}
  <small style="color:#9ca3af;">
    {{ a.time.strftime("%d %b %Y • %H:%M") }}
  </small>
{% endif %}
      </div>
    {% else %}
      <div style="color:#9ca3af;">No recent activity</div>
    {% endfor %}

  </div>
</div>

  </div> <!-- ✅ END RIGHT COLUMN -->

</section>
{% if selected_date %}
<p style="margin:0 0 10px 0; color:#4b5563; font-size:13px;">
  Showing BLs added on <strong>{{ selected_date }}</strong>
</p>
{% endif %}

      <footer>© 2026 CargoBloc Logistics — Vision to reality</footer>
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
  .container {
  animation: fadeIn 0.35s ease-in-out;
}

@keyframes fadeIn {
  from {
    opacity: 0;
    transform: translateY(6px);
  }
  to {
    opacity: 1;
    transform: translateY(0);
  }
}
  :root {
    --blue: #2563eb;
    --accent: #00AEEF;
    --green: #16a34a;
  }
  html, body {
    height:100%;
    margin:0;
    font-family:'Poppins',sans-serif;
    background: url('{{ url_for('static', filename='port_bg.png') }}') no-repeat center center fixed;
    background-size: cover;
    color:#0b1220;
  }

  .container {
    position: relative;
    max-width: 950px;
    margin: 40px auto;
    background: rgba(255,255,255,0.9);
    border-radius: 16px;
    padding: 25px;
    box-shadow: 0 8px 25px rgba(0,0,0,0.12);
    overflow: hidden;
  }

  /* ✅ WATERMARK */
  .watermark {
    position: absolute;
    top: 50%;
    left: 50%;
    transform: translate(-50%, -50%);
    opacity: 0.07;
    z-index: 0;
    pointer-events: none;
  }
  .watermark img {
    width: 450px;
    height: auto;
    object-fit: contain;
  }

  h1, p, .card, footer, form, a, button {
    position: relative;
    z-index: 2;
  }

  h1 {
    color: var(--blue);
    text-align:center;
    margin:0 0 8px;
    font-size:24px;
  }
  p.meta {
    text-align:center;
    color:#374151;
    margin:5px 0 20px;
  }

  /* BUTTON STYLES */
  .action-btn {
    display: inline-flex;
    align-items: center;
    justify-content: center;
    gap: 6px;
    padding: 10px 18px;
    font-size: 14px;
    font-weight: 600;
    border-radius: 10px;
    border: 2px solid transparent;
    cursor: pointer;
    transition: all 0.25s ease;
    font-family: 'Poppins', sans-serif;
    letter-spacing: 0.2px;
  }

  /* Add BL */
  .action-btn.add {
    color: #fff;
    border-color: #2563eb;
    background: linear-gradient(135deg, rgba(37,99,235,0.85), rgba(0,174,239,0.85));
    backdrop-filter: blur(6px);
    box-shadow: 0 4px 16px rgba(37,99,235,0.25);
  }
  .action-btn.add:hover {
    background: linear-gradient(135deg, rgba(37,99,235,0.95), rgba(0,174,239,0.95));
    transform: translateY(-2px);
  }

  /* Upload BL */
  .action-btn.upload {
    background: rgba(255,255,255,0.6);
    border: 2px solid #2563eb;
    color: #2563eb;
    backdrop-filter: blur(6px);
  }
  .action-btn.upload:hover {
    background: rgba(255,255,255,0.85);
    transform: translateY(-2px);
  }

  /* Export (used for both Export All & Selected) */
  .action-btn.export {
    background: rgba(255, 255, 255, 0.6);
    border: 2px solid #00AEEF;
    color: #00AEEF;
    font-weight: 600;
    backdrop-filter: blur(6px);
    border-radius: 10px;
    padding: 10px 18px;
    font-size: 14px;
    transition: all 0.25s ease;
    box-shadow: 0 2px 8px rgba(0,174,239,0.2);
  }
  .action-btn.export:hover {
    background: linear-gradient(135deg, rgba(0,174,239,0.15), rgba(37,99,235,0.15));
    transform: translateY(-2px);
  }

  /* Back Button */
  .action-btn.back {
    background: rgba(255,255,255,0.7);
    color: #2563eb;
    border: 2px solid #2563eb;
    border-radius: 10px;
    font-weight: 600;
    padding: 9px 16px;
    text-decoration: none;
    backdrop-filter: blur(6px);
    transition: all 0.25s ease;
  }
  .action-btn.back:hover {
    background: rgba(37,99,235,0.1);
    transform: translateY(-2px);
  }

  /* Icon buttons (View, Pay, Delete) */
  .icon-btn {
    position: relative;
    width: 34px;
    height: 34px;
    border-radius: 50%;
    border: 2px solid transparent;
    background: rgba(255, 255, 255, 0.6);
    backdrop-filter: blur(6px);
    display: inline-flex;
    align-items: center;
    justify-content: center;
    cursor: pointer;
    transition: all 0.25s ease;
    font-size: 16px;
    box-shadow: 0 2px 6px rgba(0,0,0,0.1);
  }
  .icon-btn.blue { border-color: #2563eb; color: #2563eb; }
  .icon-btn.green { border-color: #16a34a; color: #16a34a; }
  .icon-btn.red { border-color: #dc2626; color: #dc2626; }
  .icon-btn:hover {
    box-shadow: 0 0 10px currentColor, 0 3px 10px rgba(0,0,0,0.2);
    transform: translateY(-2px);
    background: rgba(255,255,255,0.8);
  }

  .card {
    margin-top:20px;
    padding:16px;
    background:white;
    border-radius:12px;
    box-shadow: 0 4px 20px rgba(0,0,0,0.05);
  }

  .bl-row {
    display:flex;
    justify-content:space-between;
    align-items:center;
    padding:10px;
    border-bottom:1px solid #e5e7eb;
  }
  .bl-row {
  width: 100%;
  box-sizing: border-box;
  }
  .bl-row.unpaid {
  background: rgba(220, 38, 38, 0.06);
  border-left: 4px solid #dc2626;
  }

  .bl-row.unpaid strong {
  color: #991b1b;
  }

  footer {
    text-align:center;
    color:#93c5fd;
    font-size:13px;
    margin-top:20px;
  }

  input[type="text"], input[type="number"] {
    border:1px solid #d1d5db;
    border-radius:6px;
    padding:6px 8px;
  }
  .bl-scroll {
  max-height: 420px;
  overflow-y: auto;
  padding-right: 6px;
}

/* smooth scrollbar */
.bl-scroll::-webkit-scrollbar {
  width: 6px;
}
.bl-scroll::-webkit-scrollbar-thumb {
  background: rgba(37,99,235,0.35);
  border-radius: 10px;
}
.bl-scroll {
  display: flex;
  flex-direction: column;
  gap: 6px;
}
</style>
</head>
<body>
  <div class="container">
    <div class="watermark">
      <img src="{{ url_for('static', filename='logo.png') }}" alt="CargoBloc Watermark">
    </div>

    <a href="{{ url_for('clients_page') }}" class="action-btn back">← Back</a>
    <h1 style="display:flex; justify-content:center; align-items:center; gap:10px;">
  Client Overview
  <button type="button" onclick="toggleEditClient()" class="action-btn upload" style="padding:6px 12px; font-size:13px;">✏ Edit</button>
</h1>
    <p class="meta">{{ client.name }} • {{ client.email or '-' }} • {{ client.phone or '-' }}</p>
    <div class="card" style="display:flex; gap:16px; flex-wrap:wrap; text-align:center;">

  <div style="flex:1; min-width:140px;">
    <small>Total Billed</small>
    <h3 style="margin:4px 0;">₵{{ '%.2f'|format(total_billed) }}</h3>
  </div>

  <div style="flex:1; min-width:140px;">
    <small>Total Paid</small>
    <h3 style="margin:4px 0; color:#16a34a;">₵{{ '%.2f'|format(total_paid) }}</h3>
  </div>

  <div style="flex:1; min-width:140px;">
    <small>Outstanding</small>
    <h3 style="margin:4px 0; color:#dc2626;">₵{{ '%.2f'|format(total_unpaid) }}</h3>
  </div>

  <div style="flex:1; min-width:140px;">
    <small>Status</small><br>
    <span style="
      display:inline-block;
      margin-top:6px;
      padding:4px 12px;
      border-radius:999px;
      font-size:13px;
      font-weight:600;
      background:
        {% if finance_status == 'Cleared' %}#dcfce7
        {% elif finance_status == 'Part Paid' %}#fef9c3
        {% elif finance_status == 'Owing' %}#fee2e2
        {% else %}#e5e7eb{% endif %};
      color:
        {% if finance_status == 'Cleared' %}#166534
        {% elif finance_status == 'Part Paid' %}#854d0e
        {% elif finance_status == 'Owing' %}#991b1b
        {% else %}#374151{% endif %};
    ">
      {{ finance_status }}
    </span>
  </div>

</div>
<div id="editClientForm" style="display:none; margin:15px auto 10px; max-width:400px; background:rgba(255,255,255,0.95); border-radius:10px; padding:14px; box-shadow:0 2px 8px rgba(0,0,0,0.08);">
  <form method="post">
    <input type="hidden" name="action" value="edit_client">
    <input name="name" value="{{ client.name }}" placeholder="Client Name" required style="width:100%; margin:5px 0; padding:8px; border:1px solid #e5e7eb; border-radius:8px;">
    <input name="email" value="{{ client.email }}" placeholder="Email" style="width:100%; margin:5px 0; padding:8px; border:1px solid #e5e7eb; border-radius:8px;">
    <input name="phone" value="{{ client.phone }}" placeholder="Phone" style="width:100%; margin:5px 0; padding:8px; border:1px solid #e5e7eb; border-radius:8px;">
    <textarea name="notes" placeholder="Notes" rows="3" style="width:100%; margin:5px 0; padding:8px; border:1px solid #e5e7eb; border-radius:8px;">{{ client.notes or '' }}</textarea>
    <button type="submit" class="action-btn add" style="margin-top:6px;"> Save Changes</button>
  </form>
</div>

<script>
function toggleForm() {
  const f = document.getElementById('addBlForm');
  f.style.display = (f.style.display === 'none' || f.style.display === '') ? 'block' : 'none';
  document.getElementById('uploadBlForm').style.display = 'none';
  document.getElementById('addMultiBlForm').style.display = 'none';
}

function toggleUpload() {
  const f = document.getElementById('uploadBlForm');
  f.style.display = (f.style.display === 'none' || f.style.display === '') ? 'block' : 'none';
  document.getElementById('addBlForm').style.display = 'none';
  document.getElementById('addMultiBlForm').style.display = 'none';
}

function toggleMultiBl() {
  const f = document.getElementById('addMultiBlForm');
  f.style.display = (f.style.display === 'none' || f.style.display === '') ? 'block' : 'none';
  document.getElementById('addBlForm').style.display = 'none';
  document.getElementById('uploadBlForm').style.display = 'none';
}
</script>

  

      <!-- Add BL Form -->
      <div id="addBlForm" style="display:none; margin-top:10px; background:rgba(255,255,255,0.95); border-radius:8px; padding:12px; box-shadow:0 2px 8px rgba(0,0,0,0.08);">
        <form method="post" enctype="multipart/form-data">
          <input type="hidden" name="action" value="add_bl">
          <input name="bl_number" placeholder="BL Number" required>
          <input name="amount_total" placeholder="Total Amount" type="number" step="0.01" required>
          <input type="file" name="bl_document" accept=".pdf,.jpg,.png,.docx">
          <button type="submit" class="action-btn add" style="margin-top:6px;">Save</button>
        </form>
      </div>
    
     <!-- ADD MULTIPLE BLs (ROW BASED) -->
<div id="addMultiBlForm" style="display:none; margin-top:10px; background:#fff; border-radius:10px; padding:14px; box-shadow:0 2px 8px rgba(0,0,0,0.08);">

  <form method="post">
    <input type="hidden" name="action" value="add_multi_bl">

    <div id="multiBlRows">
      <div class="multi-bl-row" style="display:flex; gap:8px; margin-bottom:8px;">
        <input name="bl_number[]" placeholder="BL Number" required style="flex:2;">
        <input name="amount_total[]" placeholder="Total ₵" type="number" step="0.01" style="flex:1;">
      </div>
    </div>

    <button type="button" onclick="addBlRow()" class="action-btn upload" style="margin-top:6px;">
      ➕ Add another BL
    </button>

    <button type="submit" class="action-btn add" style="margin-top:10px;">
      Save All BLs
    </button>
  </form>
</div>

<script>
function addBlRow() {
  const container = document.getElementById('multiBlRows');
  const row = document.createElement('div');
  row.style.display = 'flex';
  row.style.gap = '8px';
  row.style.marginBottom = '8px';

  row.innerHTML = `
    <input name="bl_number[]" placeholder="BL Number" required style="flex:2;">
    <input name="amount_total[]" placeholder="Total ₵" type="number" step="0.01" style="flex:1;">
  `;
  container.appendChild(row);
}
</script>
    

      <!-- Upload BL Form -->
      <div id="uploadBlForm" style="display:none; margin-top:10px; background:rgba(255,255,255,0.95); border-radius:8px; padding:12px; box-shadow:0 2px 8px rgba(0,0,0,0.08);">
        <form method="post" enctype="multipart/form-data">
          <input type="hidden" name="action" value="add_doc">
          <input name="doc_desc" placeholder="Document Description">
          <input type="file" name="client_document" accept=".pdf,.jpg,.png,.docx" required>
          <button type="submit" class="action-btn upload" style="margin-top:6px;">Upload</button>
        </form>
      </div>

      <script>
      function toggleForm() {
        const f = document.getElementById('addBlForm');
        f.style.display = (f.style.display === 'none' || f.style.display === '') ? 'block' : 'none';
        document.getElementById('uploadBlForm').style.display = 'none';
      }
      function toggleUpload() {
        const f = document.getElementById('uploadBlForm');
        f.style.display = (f.style.display === 'none' || f.style.display === '') ? 'block' : 'none';
        document.getElementById('addBlForm').style.display = 'none';
      }
      </script>

      {% if info %}
        <div style="background:rgba(59,130,246,0.12); border-left:3px solid #2563eb; color:#1e3a8a; padding:10px 12px; border-radius:8px; margin-bottom:10px;">
          {{ info }}
        </div>
      {% endif %}

      <!-- ===== START: BL list + export (no nested forms) ===== -->
<div class="card">
  <h3 style="display:flex; justify-content:space-between; align-items:center;">
    <span>BL List</span>
    <span style="display:flex; gap:8px;">
      <button type="button" onclick="toggleForm()" class="action-btn add">➕ Add BL</button>
      <button type="button" onclick="toggleMultiBl()" class="action-btn add">➕➕ Add Multiple BLs</button>
      <button type="button" onclick="toggleUpload()" class="action-btn upload">Upload BL</button>
    </span>
  </h3>
  <div style="display:flex; gap:10px; align-items:center; margin:10px 0 14px;">
  <input
    type="text"
    id="blSearch"
    placeholder="Search BL…"
    style="
      width:260px;
      padding:8px 10px;
      border-radius:8px;
      border:1px solid #d1d5db;
      font-size:14px;
    "
    oninput="filterClientBLs()">

  <button
    type="button"
    onclick="toggleUnpaidOnly()"
    class="action-btn upload"
    style="padding:6px 14px; font-size:13px;">
    Unpaid
  </button>
</div>

  <!-- 🔽 SCROLL CONTAINER START -->
  <div class="bl-scroll">

    <form id="exportForm" method="post">
      <input type="hidden" name="action" value="export_selected_bl">

      {% for bl in client.bls %}
      <div class="bl-row {% if bl.amount_unpaid > 0 %}unpaid{% endif %}">
        <input type="checkbox" name="bl_ids" value="{{ bl.id }}">

        <div style="flex:1;">
          <strong>{{ bl.bl_number }}</strong><br>
          Outstanding: ₵{{ "%.2f"|format(bl.amount_unpaid) }}
        </div>

        <div style="display:flex; gap:8px;">
          {% if bl.document %}
          <a href="{{ url_for('uploaded_file', filename=bl.document) }}"
             target="_blank"
             class="icon-btn blue">
            <img src="{{ url_for('static', filename='icon_view.png') }}" width="18">
          </a>
          {% endif %}

          <a href="{{ url_for('delete_bl', bl_id=bl.id) }}"
             class="icon-btn red"
             onclick="return confirm('Delete this BL?')">
            <img src="{{ url_for('static', filename='icon_delete.png') }}" width="18">
          </a>
        </div>
      </div>
      {% else %}
      <p>No BLs yet.</p>
      {% endfor %}

      <div style="margin-top:12px;">
        <button type="submit" class="action-btn export">Export Selected BLs</button>
      </div>
    </form>

  </div>
  <!-- 🔼 SCROLL CONTAINER END -->

  <div style="margin-top:10px;">
    <a href="{{ url_for('export_client_pdf', client_id=client.id) }}" class="action-btn export">Export All</a>
  </div>
</div>

<!-- ===== END: BL list + export ===== -->

    </div>

    <div style="margin-top:20px; display:flex; justify-content:space-between; align-items:center;">
      <p style="color:#4b5563;">Notes: {{ client.notes or '—' }}</p>
    </div>

    <footer>© 2026 CargoBloc Logistics — Vision to Reality </footer>
  </div>
  <script>
function filterClientBLs() {
  const q = document.getElementById('blSearch').value.toLowerCase();

  document.querySelectorAll('.bl-row').forEach(row => {
    const text = row.innerText.toLowerCase();
    row.style.display = text.includes(q) ? 'flex' : 'none';
  });
}
let unpaidOnly = false;

function toggleUnpaidOnly(){
  unpaidOnly = !unpaidOnly;

  document.querySelectorAll('.bl-row').forEach(row => {
    if(!unpaidOnly){
      row.style.display = 'flex';
      return;
    }
    row.style.display = row.classList.contains('unpaid') ? 'flex' : 'none';
  });
}
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

  <!-- Search + Filters (CARGOBLOC) -->
  <div style="
    display:flex;
    align-items:center;
    gap:10px;
    margin-bottom:14px;
  ">

    <input class="bl-search"
           placeholder="Search BL number…"
           id="blSearchInput"
           style="flex:1;">

    <button type="button"
            class="btn"
            onclick="filterBLs(document.getElementById('blSearchInput').value)">
      Search
    </button>

    <!-- Unpaid filter pill -->
    <div id="unpaidFilter"
         onclick="toggleUnpaidFilter()"
         style="
           padding:6px 12px;
           border-radius:999px;
           font-size:12px;
           font-weight:500;
           cursor:pointer;
           border:1px solid #cbd5e1;
           color:#334155;
           background:#f8fafc;
           white-space:nowrap;
         ">
      Unpaid only
    </div>

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

  <label style="margin-top:12px;">Payment Type</label>
<select name="payment_type"
        id="paymentType"
        onchange="handlePaymentType()"
        required>
  <option value="">Select payment type</option>
  <option value="Mobile Money">Mobile Money</option>
  <option value="Bank">Bank</option>
  <option value="Cash">Cash</option>
</select>

<label style="margin-top:12px; display:none;" id="txLabel">
  Transaction ID
</label>
<input name="transaction_id"
       id="transactionId"
       placeholder="e.g. MTN-99288321 or Bank Ref"
       style="margin-bottom:6px; display:none;">

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
function filterBLs(q){
  q = q.toLowerCase();

  document.querySelectorAll('#blList .bl-item').forEach(item => {
    const text = item.innerText.toLowerCase();
    item.style.display = text.includes(q) ? 'flex' : 'none';
  });
}

function toggleCustomDesc(val){
  document.getElementById('customDesc').style.display =
    val === 'custom' ? 'block' : 'none';
}

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
}

let unpaidOnly = false;

function toggleUnpaidFilter(){
  unpaidOnly = !unpaidOnly;

  const pill = document.getElementById('unpaidFilter');

  if(unpaidOnly){
    pill.style.background = '#e6f4f5';
    pill.style.borderColor = '#9edfe0';
    pill.style.color = '#0f766e';
  }else{
    pill.style.background = '#f8fafc';
    pill.style.borderColor = '#cbd5e1';
    pill.style.color = '#334155';
  }

  filterUnpaidBLs();
}

function filterUnpaidBLs(){
  document.querySelectorAll('.bl-item').forEach(item => {
    const unpaid = parseFloat(
      item.querySelector('input[name="bl_ids"]').dataset.unpaid || 0
    );

    if(unpaidOnly){
      item.style.display = unpaid > 0 ? 'flex' : 'none';
    }else{
      item.style.display = 'flex';
    }
  });
}
function handlePaymentType(){
  const type = document.getElementById('paymentType').value;
  const tx = document.getElementById('transactionId');
  const label = document.getElementById('txLabel');

  if(type === 'Mobile Money' || type === 'Bank'){
    tx.style.display = 'block';
    label.style.display = 'block';
    tx.required = true;
  } else {
    tx.style.display = 'none';
    label.style.display = 'none';
    tx.required = false;
    tx.value = '';
  }
}
</script>
</body>
</html>"""

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
  Total Paid: ₵{{ "%.2f"|format(receipt.amount) }}<br>
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
  <h1>Receipts</h1>

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
# -----------------------
# RUN APP
# -----------------------

@app.after_request
def security_headers(response):
    response.headers["X-Frame-Options"] = "DENY"
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["Referrer-Policy"] = "strict-origin"
    response.headers["Content-Security-Policy"] = (
        "default-src 'self'; "
        "img-src 'self' data:; "
        "style-src 'self' 'unsafe-inline'; "
        "script-src 'self'"
    )
    return response

if __name__ == '__main__':
    os.makedirs(app.config['UPLOAD_FOLDER'], exist_ok=True)
    with app.app_context():
        db.create_all()
    print("✅ Default login → admin / Cargo@conso123")
    app.run(debug=False)