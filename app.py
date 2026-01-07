# app.py
import os
from datetime import datetime
from flask import Flask, render_template_string, request, redirect, url_for, send_from_directory
from flask_sqlalchemy import SQLAlchemy
from flask_login import LoginManager, UserMixin, login_user, logout_user, login_required
from werkzeug.utils import secure_filename
from reportlab.lib.pagesizes import A4
from reportlab.pdfgen import canvas
from reportlab.lib.utils import ImageReader

app = Flask(__name__)
app.secret_key = 'cargobloc_secret_key'
app.config['SQLALCHEMY_DATABASE_URI'] = 'sqlite:///clients.db'
app.config['UPLOAD_FOLDER'] = 'uploads'
app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False

db = SQLAlchemy(app)
login_manager = LoginManager(app)
login_manager.login_view = 'login'

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
    id = db.Column(db.Integer, primary_key=True)
    bl_number = db.Column(db.String(100))
    amount_total = db.Column(db.Float, default=0)
    amount_paid = db.Column(db.Float, default=0)
    document = db.Column(db.String(200))
    client_id = db.Column(db.Integer, db.ForeignKey('client.id'))
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

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
# -----------------------
# LOGIN MANAGEMENT
# -----------------------
@login_manager.user_loader
def load_user(user_id):
    return User.query.get(int(user_id))

@app.before_request
def create_default_user():
    # create admin if DB empty; password requested earlier: Cargo@conso123
    if not User.query.first():
        db.session.add(User(username='admin', password='Cargo@conso123'))
        db.session.commit()
        print("✅ Default login → username: admin | password: Cargo@conso123")

# -----------------------
# ROUTES
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

    clients = query.order_by(Client.name).all()

    total_billed = sum(sum((bl.amount_total or 0) for bl in c.bls) for c in clients)
    total_paid = sum(sum((bl.amount_paid or 0) for bl in c.bls) for c in clients)
    total_unpaid = total_billed - total_paid

    return render_template_string(
        HOME_HTML,
        clients=clients,
        total_billed=total_billed,
        total_paid=total_paid,
        total_unpaid=total_unpaid,
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
    return redirect(url_for('home'))

@app.route('/client/<int:client_id>', methods=['GET', 'POST'])
@login_required
def client_detail(client_id):
    client = Client.query.get_or_404(client_id)
    info = None

    if request.method == 'POST':
        action = request.form.get('action')

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
            db.session.add(BL(bl_number=bl_number, amount_total=total, amount_paid=paid,
                              document=filename, client=client))
            db.session.commit()
            return redirect(url_for('client_detail', client_id=client.id))

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

        elif action == 'export_selected_bl':
            # get selected checkboxes (multiple)
            bl_ids = request.form.getlist('bl_ids')
            # filter ensures BLs belong to this client
            bls = BL.query.filter(BL.client_id == client.id, BL.id.in_(bl_ids)).all() if bl_ids else []

            if not bls:
                # show friendly message in same page
                info = "⚠ Please select at least one BL to export."
                return render_template_string(CLIENT_HTML, client=client, info=info)

            # create pdf for selected BLs
            pdf_path = os.path.join(app.config['UPLOAD_FOLDER'], f"{client.name}selected{datetime.now().strftime('%Y%m%d%H%M%S')}.pdf")
            os.makedirs(app.config['UPLOAD_FOLDER'], exist_ok=True)
            create_bl_pdf(client, bls, pdf_path)
            return send_from_directory(app.config['UPLOAD_FOLDER'], os.path.basename(pdf_path), as_attachment=True)

        elif action == 'edit_client':
            client.name = request.form.get('name', client.name)
            client.email = request.form.get('email', client.email)
            client.phone = request.form.get('phone', client.phone)
            client.notes = request.form.get('notes', client.notes)
            db.session.commit()
            return redirect(url_for('client_detail', client_id=client.id))

    return render_template_string(CLIENT_HTML, client=client, info=info)

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
@app.route('/generate_receipt', methods=['GET', 'POST'])
@login_required
def generate_receipt():
    clients = Client.query.order_by(Client.name).all()

    if request.method == 'POST':
        client_id = request.form.get('client_id')
        amount = float(request.form.get('amount') or 0)
        method = request.form.get('method')
        reference = request.form.get('reference')
        description = request.form.get('description')

        receipt = Receipt(
            client_id=client_id,
            amount=amount,
            method=method,
            reference=reference,
            description=description
        )
        db.session.add(receipt)
        db.session.commit()

        return redirect(url_for('export_receipt', receipt_id=receipt.id))

    return render_template_string(RECEIPT_HTML, clients=clients)


@app.route('/export_receipt/<int:receipt_id>')
@login_required
def export_receipt(receipt_id):
    receipt = Receipt.query.get_or_404(receipt_id)
    client = Client.query.get(receipt.client_id)

    buffer = BytesIO()
    c = canvas.Canvas(buffer, pagesize=(200 * mm, 100 * mm))
    c.setFont("Helvetica-Bold", 12)
    c.drawString(20, 80, f"Receipt for {client.name}")
    c.drawString(20, 65, f"Amount: ₵{receipt.amount}")
    c.drawString(20, 50, f"Method: {receipt.method}")
    c.drawString(20, 35, f"Reference: {receipt.reference}")
    c.drawString(20, 20, f"Description: {receipt.description}")
    c.showPage()
    c.save()
    buffer.seek(0)

    return send_file(
        buffer,
        as_attachment=True,
        download_name=f"receipt_{receipt.id}.pdf",
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
        <a href="{{ url_for('home') }}">Clients</a>
        <a href="{{ url_for('generate_receipt') }}">Receipts</a>
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
          <form method="get" action="{{ url_for('home') }}" style="display:flex; gap:8px; align-items:center;">
    <input name="q" placeholder="Search name or BL" value="{{ q or '' }}" style="padding:9px 12px; border-radius:8px; border:1px solid #e6eefb;">
    <input type="date" name="date" value="{{ request.args.get('date', '') }}" style="padding:9px 12px; border-radius:8px; border:1px solid #e6eefb;">
    <button type="submit" style="background:var(--accent); color:#fff; border:none; padding:9px 12px; border-radius:8px; font-weight:600;">Search</button>
</form>
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
            <div class="client-list">
              {% for c in clients %}
              <div class="client-item">
                <div class="meta">
                  {{ c.name }} <small>{{ c.email or '-' }} • {{ c.phone or '-' }}</small>
                </div>
                <div class="client-actions">
                  <a href="{{ url_for('client_detail', client_id=c.id) }}" class="open">Open</a>
                  <a href="{{ url_for('delete_client', client_id=c.id) }}" class="delete" onclick="return confirm('Delete client?')">Delete</a>
                </div>
              </div>
              {% endfor %}
            </div>
          </div>
        </div>

        <div style="flex:0.5; min-width:320px;">
          <div class="card" style="margin-bottom:12px;">
            <h4>Quick Actions</h4>
            <div style="display:flex; gap:8px; flex-wrap:wrap; margin-top:8px;">
              <a href="{{ url_for('home') }}" class="btn" style="background:var(--deep-blue); color:#fff; padding:8px 12px; border-radius:8px; text-decoration:none;">Refresh</a>
              <a href="{{ url_for('export_all_filtered', date=request.args.get('date', '')) }}"
   class="btn"
   style="background:#fff; border:1px solid #e6eefb; padding:8px 12px; border-radius:8px; text-decoration:none; color:var(--text);">
   Export All
</a>
              <a href="#" class="btn" style="background:var(--accent); color:#fff; padding:8px 12px; border-radius:8px; text-decoration:none;">New Report</a>
            </div>
          </div>

          <div class="card">
            <h4>Recent Activity</h4>
            <div style="font-size:13px; color:#6b7280;">
              {% for c in clients[:6] %}
                <div style="padding:8px 0; border-bottom:1px dashed #eef6ff;">Added client: <strong>{{ c.name }}</strong></div>
              {% else %}
                <div>No recent activity</div>
              {% endfor %}
            </div>
          </div>
        </div>
      </section>
{% if selected_date %}
<p style="margin:0 0 10px 0; color:#4b5563; font-size:13px;">
  Showing BLs added on <strong>{{ selected_date }}</strong>
</p>
{% endif %}

      <footer>© 2025 CargoBloc Logistics — Vision to reality</footer>
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
</style>
</head>
<body>
  <div class="container">
    <div class="watermark">
      <img src="{{ url_for('static', filename='logo.png') }}" alt="CargoBloc Watermark">
    </div>

    <a href="{{ url_for('home') }}" class="action-btn back">← Back</a>
    <h1 style="display:flex; justify-content:center; align-items:center; gap:10px;">
  Client Overview
  <button type="button" onclick="toggleEditClient()" class="action-btn upload" style="padding:6px 12px; font-size:13px;">✏ Edit</button>
</h1>
    <p class="meta">{{ client.name }} • {{ client.email or '-' }} • {{ client.phone or '-' }}</p>
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
function toggleEditClient() {
  const f = document.getElementById('editClientForm');
  f.style.display = (f.style.display === 'none' || f.style.display === '') ? 'block' : 'none';
}
</script>

    <div class="card">
      <h3 style="display:flex; justify-content:space-between; align-items:center;">
        <span>BL List</span>
        <span style="display:flex; gap:8px;">
          <button type="button" onclick="toggleForm()" class="action-btn add">➕ Add BL</button>
          <button type="button" onclick="toggleUpload()" class="action-btn upload"> Upload BL</button>
        </span>
      </h3>

      <!-- Add BL Form -->
      <div id="addBlForm" style="display:none; margin-top:10px; background:rgba(255,255,255,0.95); border-radius:8px; padding:12px; box-shadow:0 2px 8px rgba(0,0,0,0.08);">
        <form method="post" enctype="multipart/form-data">
          <input type="hidden" name="action" value="add_bl">
          <input name="bl_number" placeholder="BL Number" required>
          <input name="amount_total" placeholder="Total Amount" type="number" step="0.01" required>
          <input name="amount_paid" placeholder="Amount Paid" type="number" step="0.01">
          <input type="file" name="bl_document" accept=".pdf,.jpg,.png,.docx">
          <button type="submit" class="action-btn add" style="margin-top:6px;">Save</button>
        </form>
      </div>

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

      <form method="post">
        <input type="hidden" name="action" value="export_selected_bl">
        {% for bl in client.bls %}
        <div class="bl-row">
          <div class="bl-meta">
            <input type="checkbox" name="bl_ids" value="{{ bl.id }}">
            <strong>BL: {{ bl.bl_number }}</strong><br>
            <small>Total ₵{{ bl.amount_total }} | Paid ₵{{ bl.amount_paid }} | Unpaid ₵{{ bl.amount_unpaid }}</small>
          </div>
          <div>
            {% if bl.document %}
              <a href="{{ url_for('uploaded_file', filename=bl.document) }}" target="_blank" class="icon-btn blue" title="View Document">
                <img src="{{ url_for('static', filename='icon_view.png') }}" alt="View" style="width:18px; height:18px;">
              </a>
            {% endif %}
            <form method="post" style="display:inline;">
              <input type="hidden" name="action" value="record_payment">
              <input type="hidden" name="bl_id" value="{{ bl.id }}">
              <input name="extra_payment" placeholder="₵ amount" style="width:90px;">
              <button class="icon-btn green" title="Record Payment">
                <img src="{{ url_for('static', filename='icon_pay.png') }}" alt="Pay" style="width:18px; height:18px;">
              </button>
            </form>
            <a href="{{ url_for('delete_bl', bl_id=bl.id) }}" class="icon-btn red" title="Delete BL" onclick="return confirm('Delete this BL?')">
              <img src="{{ url_for('static', filename='icon_delete.png') }}" alt="Delete" style="width:18px; height:18px;">
            </a>
          </div>
        </div>
        {% else %}
          <p>No BLs yet.</p>
        {% endfor %}
        <button type="submit" class="action-btn export" style="margin-top:10px;"> Export Selected BLs</button>
      </form>
    </div>

    <div style="margin-top:20px; display:flex; justify-content:space-between; align-items:center;">
      <a href="{{ url_for('export_client_pdf', client_id=client.id) }}" class="action-btn export"> Export All</a>
      <p style="color:#4b5563;">Notes: {{ client.notes or '—' }}</p>
    </div>

    <footer>© 2025 CargoBloc Logistics — Vision to Reality </footer>
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
RECEIPT_HTML = """<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<title>Generate Receipt - CargoBloc</title>
<link href="https://fonts.googleapis.com/css2?family=Poppins:wght@300;400;600&display=swap" rel="stylesheet">
<style>
  body {
    font-family: 'Poppins', sans-serif;
    background: url('{{ url_for('static', filename='homepage_bg.png') }}') no-repeat center center fixed;
    background-size: cover;
    margin: 0; padding: 0;
    color: #0b1220;
  }

  .container {
    max-width: 800px;
    margin: 40px auto;
    background: rgba(255,255,255,0.95);
    border-radius: 16px;
    padding: 28px;
    box-shadow: 0 10px 30px rgba(0,0,0,0.08);
  }

  /* Letterhead */
  .letterhead {
    text-align: center;
    margin-bottom: 20px;
  }
  .letterhead img {
    height: 80px;
  }
  .letterhead h2 {
    margin: 5px 0;
    color: #2563eb;
    font-size: 22px;
  }
  .letterhead p {
    margin: 0;
    font-size: 13px;
    color: #374151;
  }
  .letterhead hr {
    margin: 15px 0;
    border: 1px solid #2563eb;
  }

  h1 {
    text-align: center;
    color: #2563eb;
    margin-bottom: 20px;
  }

  form {
    display: grid;
    grid-template-columns: 1fr 1fr;
    gap: 14px;
  }
  input, select, textarea {
    width: 100%;
    padding: 10px;
    border: 1px solid #d1d5db;
    border-radius: 8px;
    box-sizing: border-box;
  }
  textarea { grid-column: 1 / -1; resize: vertical; }

  button {
    grid-column: 1 / -1;
    background: linear-gradient(135deg,#007BFF,#00AEEF);
    color: white; font-weight: 600;
    border: none; border-radius: 8px;
    padding: 12px; cursor: pointer;
    transition: all .2s ease;
  }
  button:hover { transform: translateY(-2px); }

  a.back {
    display: inline-block; margin-bottom: 12px;
    color: #2563eb; text-decoration: none; font-weight: 600;
  }

  /* Receipt card */
  .receipt {
    background: #fff;
    padding: 25px;
    border-radius: 12px;
    box-shadow: 0 4px 15px rgba(0,0,0,0.1);
    margin-top: 30px;
  }
  .receipt h3 {
    text-align: center;
    color: #2563eb;
    margin-bottom: 20px;
  }
  .receipt p {
    margin: 6px 0;
    font-size: 14px;
  }
  .receipt .stamp {
    text-align: right;
    margin-top: 50px;
  }
  .receipt .stamp img {
    height: 80px;
  }

  .print-btn {
    margin-bottom: 20px;
    background: #2563eb;
    color: #fff;
    padding: 10px 16px;
    border: none;
    border-radius: 8px;
    cursor: pointer;
  }

  /* Print styles */
  @media print {
  body { background: none; color: #000; }
  .container { box-shadow: none; margin:0; max-width:100%; }
  button, input, select, textarea { display: none; }

  /* Make sure letterhead and stamp images print */
  .letterhead, .receipt .stamp {
    -webkit-print-color-adjust: exact;
    print-color-adjust: exact;
  }
  img { -webkit-print-color-adjust: exact; print-color-adjust: exact; }
}
</style>
</head>
<body>
<div class="container">
  <a href="{{ url_for('home') }}" class="back">← Back to Dashboard</a>
  
  <div class="letterhead">
    <img src="{{ url_for('static', filename='logo.png') }}" alt="CargoBloc Logo">
    <h2>CargoBloc Logistics</h2>
    <p>Vision to Reality • 123 Port Street, Accra, Ghana</p>
    <hr>
  </div>

  <h1>Generate New Receipt</h1>

  <form method="post">
    <label>Client:</label>
    <select name="client_id" required>
      {% for c in clients %}
        <option value="{{ c.id }}">{{ c.name }}</option>
      {% endfor %}
    </select>

    <label>Amount (₵):</label>
    <input type="number" step="0.01" name="amount" placeholder="Enter amount" required>

    <label>Payment Method:</label>
    <input name="method" placeholder="Cash / Bank Transfer / MoMo" required>

    <label>Reference / Transaction ID:</label>
    <input name="reference" placeholder="e.g. TXN123456">

    <label>Description:</label>
    <textarea name="description" placeholder="Purpose of payment or BL reference"></textarea>

    <button type="submit">Generate Receipt</button>
  </form>

  {% if receipt %}
<button class="print-btn" onclick="window.print()">🖨 Print Receipt</button>

<div class="receipt" style="
     background: url('{{ url_for('static', filename='letterhead_receipt.png') }}') no-repeat center top;
     background-size: contain;
     min-height: 800px; /* adjust to your letterhead size */
     padding: 150px 40px 40px 40px;
     position: relative;
">
  <h3 style="text-align:center; color:#2563eb; margin-bottom:30px;">Payment Receipt</h3>

  <p><strong>Receipt No:</strong> {{ receipt.id or '---' }}</p>
  <p><strong>Date:</strong> {{ receipt.date or now }}</p>
  <p><strong>Client:</strong> {{ receipt.client.name }}</p>
  <p><strong>Amount Paid:</strong> ₵{{ receipt.amount }}</p>
  <p><strong>Payment Method:</strong> {{ receipt.method }}</p>
  <p><strong>Reference / Txn ID:</strong> {{ receipt.reference or '-' }}</p>
  <p><strong>Description:</strong> {{ receipt.description or '-' }}</p>

  <!-- Stamp (optional if it's already in the letterhead) -->
  <div class="stamp" style="position:absolute; bottom:50px; right:50px;">
    <img src="{{ url_for('static', filename='stamp.png') }}" alt="Stamp" style="height:80px;">
  </div>
</div>
{% endif %}
</div>
</body>
</html>"""

# -----------------------
# RUN APP
# -----------------------
if __name__ == '__main__':
    os.makedirs(app.config['UPLOAD_FOLDER'], exist_ok=True)
    with app.app_context():
        db.create_all()
    print("✅ Default login → admin / Cargo@conso123")
    app.run(debug=True)