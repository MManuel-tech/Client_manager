import os
from datetime import datetime
from flask import Flask, render_template_string, request, redirect, url_for, send_from_directory
from flask_sqlalchemy import SQLAlchemy
from flask_login import LoginManager, UserMixin, login_user, logout_user, current_user, login_required
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

    @property
    def amount_unpaid(self):
        return max(self.amount_total - self.amount_paid, 0)

class ClientDocument(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    filename = db.Column(db.String(200))
    description = db.Column(db.String(200))
    client_id = db.Column(db.Integer, db.ForeignKey('client.id'))

# -----------------------
# LOGIN MANAGEMENT
# -----------------------
@login_manager.user_loader
def load_user(user_id):
    return User.query.get(int(user_id))

@app.before_request
def create_default_user():
    if not User.query.first():
        db.session.add(User(username='admin', password='1234'))
        db.session.commit()
        print("✅ Default login → username: admin | password: 1234")

# -----------------------
# ROUTES
# -----------------------
@app.route('/login', methods=['GET', 'POST'])
def login():
    if request.method == 'POST':
        user = User.query.filter_by(username=request.form['username'], password=request.form['password']).first()
        if user:
            login_user(user)
            return redirect(url_for('home'))
    return render_template_string(LOGIN_HTML)

@app.route('/logout')
@login_required
def logout():
    logout_user()
    return redirect(url_for('login'))

@app.route('/')
@login_required
def home():
    q = request.args.get('q', '').strip()
    if q:
        clients = Client.query.filter(
            (Client.name.ilike(f'%{q}%')) |
            (Client.bls.any(BL.bl_number.ilike(f'%{q}%')))
        ).all()
    else:
        clients = Client.query.order_by(Client.name).all()

    total_billed = sum(sum(bl.amount_total for bl in c.bls) for c in clients)
    total_paid = sum(sum(bl.amount_paid for bl in c.bls) for c in clients)
    total_unpaid = total_billed - total_paid

    return render_template_string(HOME_HTML, clients=clients, total_billed=total_billed,
                                  total_paid=total_paid, total_unpaid=total_unpaid, q=q)

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

    if request.method == 'POST':
        action = request.form.get('action')

        if action == 'add_bl':
            bl_number = request.form.get('bl_number', '').strip()
            total = float(request.form.get('amount_total') or 0)
            paid = float(request.form.get('amount_paid') or 0)
            file = request.files.get('bl_document')
            filename = None
            if file and file.filename:
                filename = secure_filename(file.filename)
                file.save(os.path.join(app.config['UPLOAD_FOLDER'], filename))
            db.session.add(BL(bl_number=bl_number, amount_total=total, amount_paid=paid,
                              document=filename, client=client))
            db.session.commit()
            return redirect(url_for('client_detail', client_id=client.id))

        elif action == 'record_payment':
            bl_id = int(request.form.get('bl_id'))
            extra_payment = float(request.form.get('extra_payment') or 0)
            bl = BL.query.get(bl_id)
            if bl:
                bl.amount_paid += extra_payment
                db.session.commit()
            return redirect(url_for('client_detail', client_id=client.id))

        elif action == 'add_doc':
            file = request.files.get('client_document')
            if file and file.filename:
                filename = secure_filename(file.filename)
                file.save(os.path.join(app.config['UPLOAD_FOLDER'], filename))
                desc = request.form.get('doc_desc', '')
                db.session.add(ClientDocument(filename=filename, description=desc, client=client))
                db.session.commit()
            return redirect(url_for('client_detail', client_id=client.id))

        elif action == 'export_selected_bl':
            bl_ids = request.form.getlist('bl_ids')
            bls = BL.query.filter(BL.id.in_(bl_ids)).all() if bl_ids else []
            if bls:
                pdf_path = os.path.join(app.config['UPLOAD_FOLDER'], f"{client.name}_selected_bls.pdf")
                create_bl_pdf(client, bls, pdf_path)
                return send_from_directory(app.config['UPLOAD_FOLDER'], os.path.basename(pdf_path), as_attachment=True)
            return redirect(url_for('client_detail', client_id=client.id))

    return render_template_string(CLIENT_HTML, client=client)

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
    db.session.delete(bl)
    db.session.commit()
    return redirect(url_for('client_detail', client_id=cid))

@app.route('/uploads/<filename>')
def uploaded_file(filename):
    return send_from_directory(app.config['UPLOAD_FOLDER'], filename)

@app.route('/client/<int:client_id>/export')
@login_required
def export_client_pdf(client_id):
    client = Client.query.get_or_404(client_id)
    pdf_path = os.path.join(app.config['UPLOAD_FOLDER'], f"{client.name}_summary.pdf")
    create_bl_pdf(client, client.bls, pdf_path)
    return send_from_directory(app.config['UPLOAD_FOLDER'], os.path.basename(pdf_path), as_attachment=True)

def create_bl_pdf(client, bls, pdf_path):
    c = canvas.Canvas(pdf_path, pagesize=A4)
    width, height = A4

    logo_path = os.path.join('static', 'logo.png')
    if os.path.exists(logo_path):
        logo = ImageReader(logo_path)
        c.drawImage(logo, 40, height - 100, width=60, height=60)

    c.setFont("Helvetica-Bold", 16)
    c.drawString(110, height - 60, "CARGOBLOC LOGISTICS — Client Summary")
    c.setFont("Helvetica", 10)
    c.drawRightString(width - 50, height - 50, f"Generated on: {datetime.now().strftime('%d %b %Y')}")

    c.setFont("Helvetica", 12)
    c.drawString(50, height - 120, f"Client: {client.name}")
    c.drawString(50, height - 140, f"Email: {client.email or '-'}")
    c.drawString(50, height - 160, f"Phone: {client.phone or '-'}")

    y = height - 200
    total_billed = total_paid = 0
    for bl in bls:
        c.drawString(50, y, f"BL: {bl.bl_number}")
        c.drawString(200, y, f"Total: ₵{bl.amount_total:.2f}")
        c.drawString(350, y, f"Paid: ₵{bl.amount_paid:.2f}")
        c.drawString(470, y, f"Unpaid: ₵{bl.amount_unpaid:.2f}")
        total_billed += bl.amount_total
        total_paid += bl.amount_paid
        y -= 20
        if y < 80:
            add_footer(c, width)
            c.showPage()
            y = height - 50

    total_unpaid = total_billed - total_paid
    c.setFont("Helvetica-Bold", 12)
    c.drawString(50, y - 20, f"Total Billed: ₵{total_billed:.2f}")
    c.drawString(250, y - 20, f"Total Paid: ₵{total_paid:.2f}")
    c.drawString(450, y - 20, f"Unpaid: ₵{total_unpaid:.2f}")

    add_footer(c, width)
    c.save()

def add_footer(c, width):
    c.setFont("Helvetica", 9)
    c.setStrokeColorRGB(0.6, 0.6, 0.6)
    c.line(40, 50, width - 40, 50)
    c.drawCentredString(width / 2, 35, "CARGOBLOC LOGISTICS • +233 55 123 4567 • info@cargobloc.com")
    c.drawCentredString(width / 2, 23, "“Excellence in Motion”")

# -----------------------
# HTML TEMPLATES
# -----------------------
LOGIN_HTML = """<!doctype html><html><head><meta charset="utf-8"><title>Login - CARGOBLOC</title>
<style>
body {font-family:Poppins, sans-serif; background:#f8fafc; display:flex; justify-content:center; align-items:center; height:100vh;}
form {background:white; padding:30px; border-radius:12px; box-shadow:0 6px 18px rgba(0,0,0,0.1);}
input {width:100%; padding:10px; margin:8px 0; border-radius:6px; border:1px solid #ddd;}
button {width:100%; background:#2563eb; color:white; border:none; padding:10px; border-radius:8px;}
</style></head><body>
<form method="post">
<h2 style="color:#2563eb;">CARGOBLOC LOGIN</h2>
<input name="username" placeholder="Username" required>
<input name="password" placeholder="Password" type="password" required>
<button type="submit">Login</button>
</form></body></html>"""

HOME_HTML = """<!doctype html><html><head><meta charset="utf-8"><title>CARGOBLOC — Clients</title>
<link href="https://fonts.googleapis.com/css2?family=Poppins:wght@400;600&display=swap" rel="stylesheet">
<style>
body{font-family:Poppins,sans-serif;background:#f8fafc;color:#0b1220;margin:20px;}
header{background:#2563eb;color:white;padding:15px;border-radius:10px;display:flex;align-items:center;gap:12px;}
header img{height:50px;}
button{background:#2563eb;color:white;border:none;padding:8px 12px;border-radius:8px;cursor:pointer;}
a.btn-blue{background:#2563eb;color:white;padding:5px 8px;border-radius:6px;text-decoration:none;}
a.btn-red{background:#dc2626;color:white;padding:5px 8px;border-radius:6px;text-decoration:none;}
.client{background:white;padding:10px;margin:8px 0;border-radius:8px;box-shadow:0 2px 8px rgba(0,0,0,0.05);display:flex;justify-content:space-between;}
</style></head><body>
<header>
<img src="{{ url_for('static', filename='logo.png') }}" alt="logo">
<h2>CARGOBLOC LOGISTICS</h2>
<div style="margin-left:auto;"><a href="{{ url_for('logout') }}" style="color:white;">Logout</a></div>
</header>
<div style="max-width:900px;margin:auto;">
<h3>💼 Financial Summary</h3>
<p>Total Billed: ₵{{ '%.2f'|format(total_billed) }} | Paid: ₵{{ '%.2f'|format(total_paid) }} | Unpaid: ₵{{ '%.2f'|format(total_unpaid) }}</p>
<form method="post" action="{{ url_for('add_client') }}">
<input name="name" placeholder="Client Name" required>
<input name="email" placeholder="Email">
<input name="phone" placeholder="Phone">
<textarea name="notes" placeholder="Notes"></textarea>
<button>Add Client</button>
</form>
<h3>Clients</h3>
<form method="get"><input name="q" value="{{ q }}" placeholder="Search name or BL"> <button>Search</button></form>
{% for c in clients %}
<div class="client"><div><b>{{ c.name }}</b><br>{{ c.email or '-' }} | {{ c.phone or '-' }}</div>
<div><a href="{{ url_for('client_detail', client_id=c.id) }}" class="btn-blue">Open</a>
<a href="{{ url_for('delete_client', client_id=c.id) }}" class="btn-red" onclick="return confirm('Delete client?')">Delete</a></div></div>
{% endfor %}
</div></body></html>"""

CLIENT_HTML = """<!doctype html><html><head><meta charset="utf-8"><title>{{ client.name }} — CARGOBLOC</title>
<style>
body{font-family:Poppins,sans-serif;background:#f8fafc;color:#0b1220;margin:20px;}
button{background:#2563eb;color:white;border:none;padding:8px 12px;border-radius:8px;}
a.btn-blue{background:#2563eb;color:white;padding:5px 8px;border-radius:6px;text-decoration:none;}
a.btn-red{background:#dc2626;color:white;padding:5px 8px;border-radius:6px;text-decoration:none;}
.card{background:white;padding:16px;border-radius:10px;box-shadow:0 2px 8px rgba(0,0,0,0.05);margin-bottom:10px;}
</style></head><body>
<a href="{{ url_for('home') }}" class="btn-blue">← Back</a>
<h2>{{ client.name }}</h2>
<p>{{ client.email or '-' }} | {{ client.phone or '-' }}</p>
<p>{{ client.notes }}</p>
<a href="{{ url_for('export_client_pdf', client_id=client.id) }}" class="btn-blue">Export Full PDF 📄</a>

<div class="card">
<h3>Add BL</h3>
<form method="post" enctype="multipart/form-data">
<input type="hidden" name="action" value="add_bl">
<input name="bl_number" placeholder="BL number" required>
<input name="amount_total" placeholder="Total">
<input name="amount_paid" placeholder="Paid">
<input type="file" name="bl_document">
<button>Add BL</button>
</form>
</div>

<div class="card">
<h3>BL List</h3>
<form method="post">
<input type="hidden" name="action" value="export_selected_bl">
{% for bl in client.bls %}
<div style="margin-bottom:10px;">
<input type="checkbox" name="bl_ids" value="{{ bl.id }}">
BL: {{ bl.bl_number }} — ₵{{ bl.amount_total }} | Paid: ₵{{ bl.amount_paid }} | Unpaid: ₵{{ bl.amount_unpaid }}
{% if bl.document %}<a href="{{ url_for('uploaded_file', filename=bl.document) }}" target="_blank">📥</a>{% endif %}
<a href="{{ url_for('delete_bl', bl_id=bl.id) }}" class="btn-red" onclick="return confirm('Delete BL?')">Delete</a>

<form method="post" style="display:inline-block; margin-left:10px;">
<input type="hidden" name="action" value="record_payment">
<input type="hidden" name="bl_id" value="{{ bl.id }}">
<input name="extra_payment" placeholder="Enter payment amount" style="width:120px;">
<button>💰 Record Payment</button>
</form>
</div>
{% endfor %}
<button type="submit">📄 Export Selected BLs as PDF</button>
</form>
</div>
</body></html>"""

# -----------------------
# RUN APP
# -----------------------
if __name__ == '__main__':
    os.makedirs(app.config['UPLOAD_FOLDER'], exist_ok=True)
    with app.app_context():
        db.create_all()
    print("✅ Default login → admin / 1234")
    app.run(debug=True)
