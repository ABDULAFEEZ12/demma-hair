import os
import re
import sys
import ssl
import bcrypt
import jwt
import datetime
import requests
import certifi
import base64
import math
import smtplib
import hmac
import hashlib
from email.mime.text import MIMEText
from io import BytesIO
from PIL import Image
from functools import wraps
from flask import Flask, render_template, request, redirect, url_for, jsonify, flash, abort
from flask_cors import CORS
from pymongo import MongoClient, ReturnDocument, errors as mongo_errors
from dotenv import load_dotenv
from bson.objectid import ObjectId
from bson.errors import InvalidId

if sys.platform == 'win32':
    ssl._create_default_https_context = ssl._create_unverified_context

load_dotenv()

app = Flask(__name__)
CORS(app)
app.secret_key = os.getenv("JWT_SECRET")

MONGO_URI = os.getenv("MONGO_URI")
JWT_SECRET = os.getenv("JWT_SECRET")
IMGBB_API_KEY = os.getenv("IMGBB_API_KEY")
SQUADCO_SECRET_KEY = os.getenv("SQUADCO_SECRET_KEY")
SITE_URL = os.getenv("SITE_URL", "https://dimmahairshop.com")

MAIL_SERVER = os.getenv("MAIL_SERVER")
MAIL_PORT = int(os.getenv("MAIL_PORT", "587") or "587")
MAIL_USE_TLS = os.getenv("MAIL_USE_TLS", "true").lower() == "true"
MAIL_USERNAME = os.getenv("MAIL_USERNAME")
MAIL_PASSWORD = os.getenv("MAIL_PASSWORD")
ORDER_NOTIFY_EMAIL = os.getenv("ORDER_NOTIFY_EMAIL", MAIL_USERNAME or "")

# ==========================
# BRAND / BUSINESS CONSTANTS
# ==========================
BRAND_NAME = "DIMMAHAIRSHOP"
BRAND_SLOGAN = "Let your hair do the talking."
WHATSAPP_NUMBER_DISPLAY = "07060943228"
WHATSAPP_NUMBER_INTL = "2347060943228"
WHATSAPP_LINK = f"https://wa.me/{WHATSAPP_NUMBER_INTL}"
INSTAGRAM_HANDLE = "@Dimma_Hair_Shop"
INSTAGRAM_URL = "https://www.instagram.com/Dimma_Hair_Shop/"
TIKTOK_HANDLE = "@Dimmahairshop"
TIKTOK_URL = "https://www.tiktok.com/@Dimmahairshop"
BUSINESS_ADDRESS_LINES = ["6, Lawson Alley,", "Off Breadfruit Street,", "Balogun Market,", "Lagos Island, Nigeria."]
BUSINESS_ADDRESS = "6, Lawson Alley, Off Breadfruit Street, Balogun Market, Lagos Island, Nigeria."

def send_order_notification(order_data):
    """Best-effort email to the store owner when a payment is confirmed. Never blocks or fails the order itself."""
    if not (MAIL_SERVER and MAIL_USERNAME and MAIL_PASSWORD and ORDER_NOTIFY_EMAIL):
        return
    try:
        items_text = "\n".join(
            f"- {item.get('name', 'Item')}{' (' + item['length'] + '\")' if item.get('length') else ''}{' - ' + item['color'] if item.get('color') else ''} x{item.get('quantity', 1)} (₦{item.get('price', 0):,.0f})"
            for item in order_data.get("items", [])
        ) or "No items listed"
        body = (
            f"New order received!\n\n"
            f"Reference: {order_data.get('paymentReference')}\n"
            f"Customer: {order_data.get('customerName')}\n"
            f"Phone: {order_data.get('customerPhone')}\n"
            f"WhatsApp: {order_data.get('customerWhatsapp')}\n"
            f"Email: {order_data.get('customerEmail')}\n"
            f"Amount: ₦{order_data.get('amount', 0):,.0f}\n\n"
            f"Items:\n{items_text}\n\n"
            f"Delivery: {order_data.get('deliveryAddress')}, {order_data.get('state')}, {order_data.get('country')}"
        )
        msg = MIMEText(body)
        msg["Subject"] = f"New Order - {order_data.get('paymentReference')}"
        msg["From"] = MAIL_USERNAME
        msg["To"] = ORDER_NOTIFY_EMAIL
        with smtplib.SMTP(MAIL_SERVER, MAIL_PORT, timeout=10) as server:
            if MAIL_USE_TLS:
                server.starttls()
            server.login(MAIL_USERNAME, MAIL_PASSWORD)
            server.sendmail(MAIL_USERNAME, [ORDER_NOTIFY_EMAIL], msg.as_string())
    except Exception as e:
        print(f"Order notification email failed: {e}")

ALLOWED_EXTENSIONS = {'png', 'jpg', 'jpeg', 'gif', 'webp'}

def allowed_file(filename):
    return '.' in filename and filename.rsplit('.', 1)[1].lower() in ALLOWED_EXTENSIONS

MAX_IMAGE_DIMENSION = 1600
IMAGE_JPEG_QUALITY = 80

def compress_image(file_data):
    """Downscale and re-encode an uploaded image as JPEG to cut its file size before hosting it."""
    try:
        img = Image.open(BytesIO(file_data))
        img = img.convert("RGB")
        if max(img.size) > MAX_IMAGE_DIMENSION:
            img.thumbnail((MAX_IMAGE_DIMENSION, MAX_IMAGE_DIMENSION), Image.LANCZOS)
        buffer = BytesIO()
        img.save(buffer, format="JPEG", quality=IMAGE_JPEG_QUALITY, optimize=True)
        return buffer.getvalue()
    except Exception:
        return file_data

def upload_image(file):
    if not IMGBB_API_KEY:
        return "https://via.placeholder.com/400x500/F1E7D8/AD8A4D?text=DIMMAHAIRSHOP"
    try:
        file_data = compress_image(file.read())
        encoded_image = base64.b64encode(file_data).decode('utf-8')
        response = requests.post("https://api.imgbb.com/1/upload", data={"key": IMGBB_API_KEY, "image": encoded_image})
        result = response.json()
        if result.get("success"): return result["data"]["url"]
        return None
    except: return None

if not MONGO_URI:
    print("WARNING: MONGO_URI is not set - set it in your environment (.env locally, "
          "the host's dashboard in production). The app will start but every database "
          "operation will fail until it's configured.")

try:
    client = MongoClient(MONGO_URI, tls=True, tlsCAFile=certifi.where(), tlsAllowInvalidCertificates=True, serverSelectionTimeoutMS=5000)
    client.admin.command('ping')
except Exception as e:
    print(f"Primary MongoDB connection attempt failed, retrying with relaxed TLS options: {e}")
    # Keep a short, explicit timeout here too - the previous attempt already proved the
    # server isn't reachable, so a slow retry would just tie up a request thread until it
    # exceeds the process manager's worker timeout (e.g. gunicorn's default 30s) and gets
    # killed mid-request instead of failing fast with a clear error.
    client = MongoClient(MONGO_URI, tls=True, tlsAllowInvalidCertificates=True, serverSelectionTimeoutMS=5000)

db = client["dimmahairshop"]
products_collection = db["products"]
orders_collection = db["orders"]
admins_collection = db["admins"]
messages_collection = db["messages"]
wholesale_collection = db["wholesale_enquiries"]
reviews_collection = db["reviews"]

try:
    orders_collection.create_index("paymentReference", unique=True)
except Exception as e:
    print(f"Could not create unique index on paymentReference: {e}")

PRODUCTS_PER_PAGE = 12

def paginate_products(query, page, sort=None):
    """Fetch one page of products matching query. Returns (products, page, total_pages, total_count)."""
    total = products_collection.count_documents(query)
    total_pages = max(1, math.ceil(total / PRODUCTS_PER_PAGE))
    page = min(max(1, page), total_pages)
    cursor = products_collection.find(query)
    if sort:
        cursor = cursor.sort(*sort)
    cursor = cursor.skip((page - 1) * PRODUCTS_PER_PAGE).limit(PRODUCTS_PER_PAGE)
    return convert_cursor(cursor), page, total_pages, total

def safe_objectid(id_str):
    try: return ObjectId(id_str)
    except: return None

def convert_doc(doc):
    if not doc: return doc
    doc["_id"] = str(doc["_id"])
    return doc

def convert_cursor(cursor):
    return [convert_doc(doc) for doc in cursor]

# ==========================
# HAIR CATEGORY TAXONOMY
# ==========================
CATEGORIES = [
    {"slug": "raw-hair", "label": "Raw Hair", "nav_label": "Raw Hair", "blurb": "Single donor raw hair - unprocessed, cuticle-aligned and built to last."},
    {"slug": "virgin-donor-hair", "label": "Virgin Donor Hair", "nav_label": "Virgin Donor Hair", "blurb": "Never chemically processed donor hair, prized for its natural strength and shine."},
    {"slug": "bone-straight", "label": "Bone Straight", "nav_label": "Bone Straight", "blurb": "Sleek, silky bone straight textures that hold their finish wash after wash."},
    {"slug": "wavy-hair", "label": "Wavy Hair", "nav_label": "Wavy Hair", "blurb": "Effortless body wave and water wave textures for everyday luxury."},
    {"slug": "curly-hair", "label": "Curly Hair", "nav_label": "Curly Hair", "blurb": "Bouncy, defined curls from kinky curly to deep curl patterns."},
    {"slug": "bundles", "label": "Bundles", "nav_label": "Bundles", "blurb": "Premium bundles in every length and texture, sold individually or in sets."},
    {"slug": "closures", "label": "Closures", "nav_label": "Closures", "blurb": "4x4, 5x5 and 6x6 closures for a natural, seamless finish."},
    {"slug": "frontals", "label": "Frontals", "nav_label": "Frontals", "blurb": "13x4, 13x6 and 360 frontals for versatile styling and parting."},
    {"slug": "ready-to-wear-wigs", "label": "Ready To Wear Wigs", "nav_label": "Wigs", "blurb": "Pre-styled, glueless-ready wigs - install and go."},
    {"slug": "colored-hair", "label": "Colored Hair", "nav_label": "Colored Hair", "blurb": "Blondes, browns and balayage tones, professionally coloured for vendors and stylists."},
    {"slug": "combo-deals", "label": "Combo Deals", "nav_label": "Combo Deals", "blurb": "Bundle + closure/frontal sets curated to save you money."},
]
for _c in CATEGORIES:
    _c["endpoint"] = _c["slug"]
CATEGORY_BY_SLUG = {c["slug"]: c for c in CATEGORIES}
CATEGORY_LABELS = [c["label"] for c in CATEGORIES]

LENGTH_OPTIONS = ['8', '10', '12', '14', '16', '18', '20', '22', '24', '26', '28', '30', '32']
TEXTURE_OPTIONS = ['Straight', 'Bone Straight', 'Body Wave', 'Water Wave', 'Deep Wave', 'Curly', 'Kinky Curly']
HAIR_TYPE_OPTIONS = ['Raw Hair', 'Virgin Hair', 'Donor Hair', 'Processed Hair']
CLOSURE_FRONTAL_TYPE_OPTIONS = ['4x4 Closure', '5x5 Closure', '6x6 Closure', '13x4 Frontal', '13x6 Frontal', '360 Frontal']
CAP_LACE_TYPE_OPTIONS = ['HD Lace Closure Wig', 'Lace Front Wig', 'Full Lace Wig', 'Glueless Wig', 'U-Part Wig']
HAIR_COLOR_OPTIONS = ['Natural Black', 'Dark Brown', 'Chocolate Brown', 'Honey Blonde', '613 Blonde', 'Ombre', 'Ash Grey', 'Burgundy']

def parse_length_stock(form):
    """Read per-length quantity fields (length_qty_8, length_qty_10, ...) into a {length: qty} dict, dropping zero/blank entries."""
    length_stock = {}
    for l in LENGTH_OPTIONS:
        raw = form.get(f"length_qty_{l}", "").strip()
        if raw:
            try:
                qty = int(raw)
                if qty > 0:
                    length_stock[l] = qty
            except ValueError:
                pass
    return length_stock

def validate_product_data(name, price, stock, category):
    if not name or not name.strip(): return False, "Product name is required."
    try:
        if float(price) < 0: return False, "Price cannot be negative."
    except: return False, "Price must be a valid number."
    try:
        if int(stock) < 0: return False, "Stock cannot be negative."
    except: return False, "Stock must be a valid integer."
    if not category or not category.strip(): return False, "Category is required."
    return True, ""

def format_currency(amount):
    try: return "₦{:,.2f}".format(float(amount))
    except: return "₦0.00"

app.jinja_env.globals.update(format_currency=format_currency)

@app.context_processor
def inject_brand_context():
    return dict(
        BRAND_NAME=BRAND_NAME,
        BRAND_SLOGAN=BRAND_SLOGAN,
        WHATSAPP_NUMBER_DISPLAY=WHATSAPP_NUMBER_DISPLAY,
        WHATSAPP_LINK=WHATSAPP_LINK,
        INSTAGRAM_HANDLE=INSTAGRAM_HANDLE,
        INSTAGRAM_URL=INSTAGRAM_URL,
        TIKTOK_HANDLE=TIKTOK_HANDLE,
        TIKTOK_URL=TIKTOK_URL,
        BUSINESS_ADDRESS_LINES=BUSINESS_ADDRESS_LINES,
        BUSINESS_ADDRESS=BUSINESS_ADDRESS,
        ALL_CATEGORIES=CATEGORIES,
        CATEGORY_LABELS=CATEGORY_LABELS,
        LENGTH_OPTIONS=LENGTH_OPTIONS,
        TEXTURE_OPTIONS=TEXTURE_OPTIONS,
        HAIR_TYPE_OPTIONS=HAIR_TYPE_OPTIONS,
        CLOSURE_FRONTAL_TYPE_OPTIONS=CLOSURE_FRONTAL_TYPE_OPTIONS,
        CAP_LACE_TYPE_OPTIONS=CAP_LACE_TYPE_OPTIONS,
        HAIR_COLOR_OPTIONS=HAIR_COLOR_OPTIONS,
        current_year=datetime.datetime.utcnow().year,
    )

def token_required(f):
    @wraps(f)
    def decorated(*args, **kwargs):
        token = request.cookies.get("admin_token")
        if not token:
            flash("Please log in to access the admin area.", "error")
            return redirect(url_for("admin_login_page"))
        try:
            data = jwt.decode(token, JWT_SECRET, algorithms=["HS256"])
            admin_id = safe_objectid(data.get("id"))
            if not admin_id: raise InvalidId
            current_admin = admins_collection.find_one({"_id": admin_id})
            if not current_admin:
                flash("Admin account not found.", "error")
                return redirect(url_for("admin_login_page"))
        except:
            flash("Invalid or expired session.", "error")
            return redirect(url_for("admin_login_page"))
        return f(current_admin, *args, **kwargs)
    return decorated

# ==========================
# PUBLIC ROUTES
# ==========================
@app.route("/")
def home():
    try:
        featured = convert_cursor(products_collection.find({"featured": True}).limit(8))
        if not featured:
            featured = convert_cursor(products_collection.find().sort("created_at", -1).limit(8))
        new_arrivals = convert_cursor(products_collection.find({"newArrival": True}).limit(8))
        category_images = {}
        for cat in CATEGORIES:
            prod = products_collection.find_one({"category": cat["label"], "image": {"$exists": True}})
            if prod:
                category_images[cat["slug"]] = prod.get("image")
        reviews = convert_cursor(reviews_collection.find().sort("createdAt", -1).limit(6))
    except Exception as e:
        print(f"Home route error: {e}")
        featured, new_arrivals, category_images, reviews = [], [], {}, []
    return render_template("home.html", featured=featured, new_arrivals=new_arrivals,
                            category_images=category_images, reviews=reviews)

def render_product_page(template, query, category_name, sort=None, endpoint=None, **extra_context):
    page = request.args.get("page", 1, type=int)
    try:
        products, page, total_pages, total = paginate_products(query, page, sort=sort)
    except Exception as e:
        print(f"Product listing error: {e}")
        products, page, total_pages, total = [], 1, 1, 0
    pagination_args = request.args.to_dict(flat=False)
    pagination_args.pop("page", None)
    return render_template(template, products=products, category_name=category_name,
                            page=page, total_pages=total_pages, total_products=total,
                            endpoint=endpoint or request.endpoint, pagination_args=pagination_args,
                            **extra_context)

def make_category_view(cat):
    def view():
        return render_product_page("category.html", {"category": cat["label"]}, cat["label"], category=cat)
    return view

for _cat in CATEGORIES:
    app.add_url_rule(f"/{_cat['slug']}", _cat["endpoint"], make_category_view(_cat))

@app.route("/closures-frontals")
def closures_frontals():
    return render_product_page("category.html", {"category": {"$in": ["Closures", "Frontals"]}}, "Closures & Frontals",
                                category={"slug": "closures-frontals", "label": "Closures & Frontals",
                                          "blurb": "Everything you need for a flawless install - closures and frontals in every size."})

@app.route("/about")
def about(): return render_template("about.html")

@app.route("/wholesale", methods=["GET"])
def wholesale():
    return render_template("wholesale.html")

@app.route("/wholesale-enquiry", methods=["POST"])
def wholesale_enquiry():
    data = request.form if request.form else (request.get_json(silent=True) or {})
    name = (data.get("name") or "").strip()
    phone = (data.get("phone") or "").strip()
    if not name or not phone:
        if request.is_json:
            return jsonify({"message": "Name and phone are required."}), 400
        flash("Please provide your name and phone number.", "error")
        return redirect(url_for("wholesale"))
    enquiry = {
        "name": name,
        "businessName": (data.get("businessName") or "").strip(),
        "phone": phone,
        "whatsapp": (data.get("whatsapp") or "").strip(),
        "email": (data.get("email") or "").strip(),
        "businessType": (data.get("businessType") or "").strip(),
        "productsInterested": (data.get("productsInterested") or "").strip(),
        "quantity": (data.get("quantity") or "").strip(),
        "budget": (data.get("budget") or "").strip(),
        "message": (data.get("message") or "").strip(),
        "status": "New",
        "createdAt": datetime.datetime.utcnow(),
    }
    try:
        wholesale_collection.insert_one(enquiry)
        if request.is_json:
            return jsonify({"message": "Enquiry sent successfully!"})
        flash("Thank you! Your wholesale enquiry has been received - we'll reach out shortly.", "success")
    except Exception as e:
        if request.is_json:
            return jsonify({"message": "Failed to send enquiry", "error": str(e)}), 500
        flash("Could not send your enquiry right now. Please try WhatsApp instead.", "error")
    return redirect(url_for("wholesale"))

@app.route("/collection")
def collection():
    return redirect(url_for("shop"))

@app.route("/search")
def search():
    q = request.args.get("q", "").strip()
    if not q:
        return render_template("search.html", products=[], total_products=0, page=1, total_pages=1,
                                search_query="", endpoint="search", pagination_args={})
    pattern = {"$regex": re.escape(q), "$options": "i"}
    query = {"$or": [
        {"name": pattern}, {"category": pattern}, {"texture": pattern},
        {"hairType": pattern}, {"origin": pattern}, {"tags": pattern}, {"description": pattern},
    ]}
    return render_product_page("search.html", query, f'Search results for "{q}"', endpoint="search", search_query=q)

@app.route("/shop")
def shop():
    query = {}
    selected_cats = request.args.getlist("cat")
    if selected_cats:
        query["category"] = {"$in": selected_cats}
    selected_textures = request.args.getlist("texture")
    if selected_textures:
        query["texture"] = {"$in": selected_textures}
    selected_lengths = request.args.getlist("length")
    if selected_lengths:
        query["lengths"] = {"$in": selected_lengths}
    min_price = request.args.get("min_price", type=float)
    max_price = request.args.get("max_price", type=float)
    if min_price is not None or max_price is not None:
        price_q = {}
        if min_price is not None: price_q["$gte"] = min_price
        if max_price is not None: price_q["$lte"] = max_price
        query["price"] = price_q
    if request.args.get("in_stock") == "1":
        query["stock"] = {"$gt": 0}
    if request.args.get("wholesale") == "1":
        query["wholesaleAvailable"] = True
    return render_product_page("shop.html", query, "Shop All", selected_cats=selected_cats,
                                selected_textures=selected_textures, selected_lengths=selected_lengths)

@app.route("/cart")
def cart(): return render_template("cart.html")

@app.route("/contact")
def contact(): return render_template("contact.html")

@app.route("/track-order", methods=["GET", "POST"])
def track_order():
    orders = None
    searched = False
    if request.method == "POST":
        query_value = request.form.get("lookup", "").strip()
        searched = True
        if query_value:
            orders = convert_cursor(orders_collection.find({
                "$or": [
                    {"customerPhone": query_value},
                    {"customerWhatsapp": query_value},
                    {"customerEmail": {"$regex": f"^{re.escape(query_value)}$", "$options": "i"}}
                ]
            }).sort("createdAt", -1))
            for order in orders:
                order["orderItems"] = order.pop("items", [])
        else:
            orders = []
    return render_template("track-order.html", orders=orders, searched=searched)

@app.route("/product/<product_id>")
def product_detail(product_id):
    obj_id = safe_objectid(product_id)
    if not obj_id: abort(404)
    product = products_collection.find_one({"_id": obj_id})
    if not product: abort(404)
    product = convert_doc(product)
    product["id"] = product["_id"]
    related_products = []
    if product.get("category"):
        related_products = convert_cursor(products_collection.find({"_id": {"$ne": obj_id}, "category": product["category"]}).limit(4))
    return render_template("product.html", product=product, related_products=related_products)

@app.route("/checkout")
def checkout(): return render_template("checkout.html")

@app.route("/order-confirmed")
def order_confirmed(): return render_template("order-confirmed.html")

def verify_squad_transaction(reference):
    """Ask SquadCo whether a transaction actually succeeded. Returns the transaction data dict, or None if it can't be confirmed."""
    if not SQUADCO_SECRET_KEY or not reference:
        return None
    headers = {"Authorization": f"Bearer {SQUADCO_SECRET_KEY}"}
    try:
        resp = requests.get(f"https://api-d.squadco.com/transaction/verify/{reference}", headers=headers, timeout=15)
        if resp.status_code != 200:
            return None
        result = resp.json()
        return result.get("data")
    except Exception as e:
        print(f"SquadCo verify error: {e}")
        return None

def enrich_items_with_images(items):
    """Attach each item's current product image so admin can see what was bought at a glance."""
    enriched = []
    for item in items:
        product_id = safe_objectid(item.get("id", ""))
        image = None
        if product_id:
            product = products_collection.find_one({"_id": product_id}, {"image": 1})
            if product:
                image = product.get("image")
        enriched.append({**item, "image": image})
    return enriched

def decrement_stock_for_items(items):
    for item in items:
        product_id = safe_objectid(item.get("id", ""))
        qty = int(item.get("quantity") or 1)
        length = (item.get("length") or "").strip()
        if not product_id or qty <= 0:
            continue
        if length:
            # Length-tracked product: decrement that length's stock and the overall total together.
            length_field = f"lengthStock.{length}"
            result = products_collection.update_one(
                {"_id": product_id, length_field: {"$gte": qty}},
                {"$inc": {length_field: -qty, "stock": -qty}}
            )
            if result.matched_count == 0:
                products_collection.update_one({"_id": product_id}, {"$set": {length_field: 0}})
        else:
            result = products_collection.update_one(
                {"_id": product_id, "stock": {"$gte": qty}},
                {"$inc": {"stock": -qty}}
            )
            if result.matched_count == 0:
                products_collection.update_one({"_id": product_id}, {"$set": {"stock": 0}})

def finalize_paid_order(reference, extra_fields=None):
    """Atomically transition an order to Paid and run the paid-side effects (stock, email) exactly
    once - safe to call from both the browser callback and the SquadCo webhook without either one
    double-processing if they both arrive for the same payment."""
    update_fields = dict(extra_fields or {})
    update_fields["status"] = "Paid"

    claimed = orders_collection.find_one_and_update(
        {"paymentReference": reference, "status": {"$ne": "Paid"}},
        {"$set": update_fields},
        return_document=ReturnDocument.AFTER
    )
    if claimed is None:
        existing = orders_collection.find_one({"paymentReference": reference})
        if existing:
            return existing
        update_fields["paymentReference"] = reference
        update_fields.setdefault("createdAt", datetime.datetime.utcnow())
        try:
            orders_collection.insert_one(update_fields)
        except mongo_errors.DuplicateKeyError:
            return orders_collection.find_one({"paymentReference": reference})
        claimed = orders_collection.find_one({"paymentReference": reference})

    decrement_stock_for_items(claimed.get("items", []))
    send_order_notification(claimed)
    return claimed

@app.route("/create-payment-link", methods=["POST"])
def create_payment_link():
    data = request.get_json()
    if not data:
        return jsonify({"error": "Invalid request"}), 400

    amount = data.get("amount", 0)
    email = data.get("email", "customer@dimmahairshop.com")
    name = data.get("name", "Customer")
    reference = data.get("reference")

    if not reference:
        return jsonify({"error": "Missing order reference"}), 400
    if not SQUADCO_SECRET_KEY:
        return jsonify({"error": "Payment not configured"}), 500

    try:
        items = enrich_items_with_images(data.get("items", []))
        orders_collection.update_one(
            {"paymentReference": reference},
            {"$setOnInsert": {
                "paymentReference": reference,
                "customerName": data.get("customerName", name),
                "customerPhone": data.get("customerPhone", ""),
                "customerWhatsapp": data.get("customerWhatsapp", ""),
                "customerEmail": data.get("customerEmail", email),
                "deliveryAddress": data.get("deliveryAddress", ""),
                "state": data.get("state", ""),
                "country": data.get("country", "Nigeria"),
                "orderNotes": data.get("orderNotes", ""),
                "items": items,
                "amount": float(amount),
                "paymentMethod": "SquadCo",
                "status": "Pending",
                "createdAt": datetime.datetime.utcnow()
            }},
            upsert=True
        )
    except Exception as e:
        print(f"Could not pre-save pending order: {e}")

    try:
        response = requests.post(
            "https://api-d.squadco.com/transaction/initiate",
            headers={
                "Authorization": f"Bearer {SQUADCO_SECRET_KEY}",
                "Content-Type": "application/json"
            },
            json={
                "amount": float(amount) * 100,
                "email": email,
                "customer_name": name,
                "currency": "NGN",
                "initiate_type": "inline",
                "pass_charge": True,
                "transaction_ref": reference,
                "callback_url": f"{SITE_URL}/order-confirmed"
            }
        )

        result = response.json()
        print(f"SquadCo response: {result}")
        checkout_url = result.get("data", {}).get("checkout_url")

        if checkout_url:
            return jsonify({"payment_url": checkout_url})
        else:
            return jsonify({"error": "Could not create payment link", "detail": result}), 502

    except Exception as e:
        print(f"SquadCo error: {e}")
        return jsonify({"error": "Payment service unavailable"}), 502

@app.route("/squadco-webhook", methods=["POST"])
def squadco_webhook():
    """Server-to-server confirmation from SquadCo - the reliable path that doesn't depend on the
    customer's browser making it back to the site after paying."""
    raw_body = request.get_data()
    signature = request.headers.get("x-squad-encrypted-body", "")
    if not SQUADCO_SECRET_KEY or not signature:
        return jsonify({"message": "missing signature"}), 401
    expected = hmac.new(SQUADCO_SECRET_KEY.encode(), raw_body, hashlib.sha512).hexdigest().upper()
    if not hmac.compare_digest(expected, signature.upper()):
        return jsonify({"message": "invalid signature"}), 401

    payload = request.get_json(silent=True) or {}
    if payload.get("Event") != "charge_successful":
        return jsonify({"message": "ignored"}), 200

    body = payload.get("Body", {})
    reference = body.get("transaction_ref")
    status = str(body.get("transaction_status", "")).lower()
    if not reference or status != "success":
        return jsonify({"message": "ignored"}), 200

    extra_fields = {}
    if not orders_collection.find_one({"paymentReference": reference}):
        extra_fields = {
            "customerName": "Unknown - recovered from SquadCo webhook, check SquadCo dashboard",
            "customerEmail": body.get("email", ""),
            "amount": float(body.get("amount", 0)) / 100,
            "items": [],
            "paymentMethod": "SquadCo",
        }

    try:
        finalize_paid_order(reference, extra_fields)
        return jsonify({"message": "ok"}), 200
    except Exception as e:
        print(f"Webhook finalize error: {e}")
        return jsonify({"message": "error"}), 500

@app.route("/save-order", methods=["POST"])
def save_order():
    data = request.get_json()
    if not data: return jsonify({"message": "Invalid request"}), 400
    reference = data.get("reference")
    if not reference:
        return jsonify({"message": "Missing order reference"}), 400

    existing = orders_collection.find_one({"paymentReference": reference})
    if existing and existing.get("status") == "Paid":
        return jsonify({"message": "Order already recorded", "reference": reference})

    transaction = verify_squad_transaction(reference)
    if not transaction or str(transaction.get("transaction_status", "")).lower() != "success":
        return jsonify({"message": "Payment could not be verified. Order was not recorded.", "verified": False}), 402

    expected_kobo = float(data.get("totalAmount", 0)) * 100
    paid_kobo = float(transaction.get("transaction_amount") or 0)
    if expected_kobo > 0 and paid_kobo < expected_kobo * 0.99:
        return jsonify({"message": "Paid amount does not match order total. Order was not recorded.", "verified": False}), 402

    extra_fields = {
        "customerName": data.get("customerName", "Unknown"),
        "customerPhone": data.get("customerPhone", ""),
        "customerWhatsapp": data.get("customerWhatsapp", ""),
        "customerEmail": data.get("customerEmail", ""),
        "deliveryAddress": data.get("deliveryAddress", ""),
        "state": data.get("state", ""),
        "country": data.get("country", "Nigeria"),
        "orderNotes": data.get("orderNotes", ""),
        "amount": float(data.get("totalAmount", 0)),
        "paymentMethod": data.get("paymentMethod", "SquadCo"),
    }
    if not existing or not existing.get("items"):
        extra_fields["items"] = enrich_items_with_images(data.get("items", []))

    try:
        finalize_paid_order(reference, extra_fields)
        return jsonify({"message": "Order saved", "reference": reference})
    except Exception as e:
        return jsonify({"message": "Failed", "error": str(e)}), 500

@app.route("/send-message", methods=["POST"])
def send_message():
    data = request.get_json()
    if not data: return jsonify({"message": "Invalid request"}), 400
    message_data = {
        "name": data.get("name", "Unknown"),
        "email": data.get("email", ""),
        "subject": data.get("subject", ""),
        "message": data.get("message", ""),
        "createdAt": datetime.datetime.utcnow()
    }
    try:
        messages_collection.insert_one(message_data)
        return jsonify({"message": "Message sent successfully!"})
    except Exception as e:
        return jsonify({"message": "Failed to send message", "error": str(e)}), 500

@app.route("/order/<reference>")
def order_status(reference):
    order = orders_collection.find_one({"paymentReference": reference})
    if not order: abort(404)
    return render_template("order_status.html", order=convert_doc(order))

# ==========================
# ADMIN ROUTES
# ==========================
@app.route("/admin/seed")
def seed_admin():
    if admins_collection.find_one({"email": "admin@dimmahairshop.com"}):
        return "Admin already exists.", 200
    hashed = bcrypt.hashpw("admin123".encode(), bcrypt.gensalt())
    admins_collection.insert_one({"email": "admin@dimmahairshop.com", "password": hashed, "role": "admin", "created_at": datetime.datetime.utcnow()})
    return "✅ Admin created! DELETE THIS ROUTE.", 201

@app.route("/admin/login", methods=["GET", "POST"])
def admin_login_page():
    if request.method == "POST":
        email = request.form.get("email", "").strip().lower()
        password = request.form.get("password", "")
        admin = admins_collection.find_one({"email": email})
        if not admin or not bcrypt.checkpw(password.encode(), admin["password"]):
            flash("Invalid email or password.", "error")
            return redirect(url_for("admin_login_page"))
        token = jwt.encode({"id": str(admin["_id"]), "exp": datetime.datetime.utcnow() + datetime.timedelta(days=7)}, JWT_SECRET, algorithm="HS256")
        response = redirect(url_for("admin_products"))
        response.set_cookie("admin_token", token, httponly=True, secure=False, samesite="Lax", max_age=60*60*24*7)
        flash("Login successful!", "success")
        return response
    return render_template("admin_login.html")

@app.route("/admin/dashboard")
@token_required
def admin_dashboard(current_admin):
    return redirect(url_for("admin_products"))

@app.route("/admin/products")
@token_required
def admin_products(current_admin):
    try:
        products = convert_cursor(products_collection.find())
    except:
        products = []
    return render_template("admin_products.html", products=products)

@app.route("/admin/add-product-page")
@token_required
def admin_add_product_page(current_admin):
    return render_template("admin_add_product.html")

def _prepare_orders_for_admin(orders):
    for order in orders:
        if 'items' in order and isinstance(order['items'], list):
            order['orderItems'] = order.pop('items')
        else:
            order['orderItems'] = []
        order.setdefault('customerName', '—')
        order.setdefault('customerPhone', '—')
        order.setdefault('customerWhatsapp', '—')
        order.setdefault('customerEmail', '')
        order.setdefault('deliveryAddress', '—')
        order.setdefault('state', '—')
        order.setdefault('country', '—')
        order.setdefault('amount', 0)
        order.setdefault('status', 'Pending')
        order.setdefault('paymentReference', '—')
    return orders

@app.route("/admin/orders")
@token_required
def admin_orders(current_admin):
    try:
        orders = convert_cursor(orders_collection.find().sort("createdAt", -1))
    except:
        orders = []
    orders = _prepare_orders_for_admin(orders)
    return render_template("admin_orders.html", orders=orders)

@app.route("/admin/messages")
@token_required
def admin_messages(current_admin):
    try:
        messages = convert_cursor(messages_collection.find().sort("createdAt", -1))
    except:
        messages = []
    return render_template("admin_messages.html", messages=messages)

@app.route("/admin/wholesale")
@token_required
def admin_wholesale(current_admin):
    try:
        enquiries = convert_cursor(wholesale_collection.find().sort("createdAt", -1))
    except:
        enquiries = []
    return render_template("admin_wholesale.html", enquiries=enquiries)

@app.route("/admin/stock")
@token_required
def admin_stock(current_admin):
    try:
        products = convert_cursor(products_collection.find())
    except:
        products = []

    sold_totals, sold_by_length, sold_by_color = {}, {}, {}
    try:
        for order in orders_collection.find({"status": "Paid"}, {"items": 1}):
            for item in order.get("items", []):
                pid = item.get("id")
                if not pid:
                    continue
                qty = int(item.get("quantity") or 1)
                sold_totals[pid] = sold_totals.get(pid, 0) + qty
                length = (item.get("length") or "").strip()
                if length:
                    sold_by_length.setdefault(pid, {})
                    sold_by_length[pid][length] = sold_by_length[pid].get(length, 0) + qty
                color = (item.get("color") or "").strip()
                if color:
                    sold_by_color.setdefault(pid, {})
                    sold_by_color[pid][color] = sold_by_color[pid].get(color, 0) + qty
    except Exception as e:
        print(f"Stock aggregation error: {e}")

    for product in products:
        pid = str(product["_id"])
        product["sold"] = sold_totals.get(pid, 0)
        product["soldByLength"] = sold_by_length.get(pid, {})
        product["soldByColor"] = sold_by_color.get(pid, {})

    total_stock = sum(p.get("stock", 0) or 0 for p in products)
    total_sold = sum(p.get("sold", 0) for p in products)
    sold_out_count = sum(1 for p in products if (p.get("stock") or 0) <= 0)

    return render_template("admin_stock.html", products=products,
                            total_stock=total_stock, total_sold=total_sold, sold_out_count=sold_out_count)

@app.route("/admin/logout")
def admin_logout():
    response = redirect(url_for("admin_login_page"))
    response.delete_cookie("admin_token")
    flash("Logged out.", "success")
    return response

@app.route("/admin/register", methods=["GET", "POST"])
@token_required
def admin_register(current_admin):
    if request.method == "POST":
        email = request.form.get("email", "").strip().lower()
        password = request.form.get("password", "")
        confirm = request.form.get("confirm_password", "")
        if not email or not password: flash("Required.", "error"); return redirect(url_for("admin_register"))
        if password != confirm: flash("Passwords don't match.", "error"); return redirect(url_for("admin_register"))
        if admins_collection.find_one({"email": email}): flash("Already exists.", "error"); return redirect(url_for("admin_register"))
        hashed = bcrypt.hashpw(password.encode(), bcrypt.gensalt())
        admins_collection.insert_one({"email": email, "password": hashed, "created_by": current_admin["email"], "role": "admin", "created_at": datetime.datetime.utcnow()})
        flash("Admin created!", "success")
        return redirect(url_for("admin_products"))
    return render_template("admin_register.html")

def _read_product_form(form):
    """Shared field extraction for add/edit product - everything beyond name/price/category/image is optional."""
    length_stock = parse_length_stock(form)
    sale_price_raw = form.get("salePrice", "").strip()
    sale_price = None
    if sale_price_raw:
        try:
            sale_price = float(sale_price_raw)
        except ValueError:
            sale_price = None
    tags_raw = form.get("tags", "").strip()
    tags = [t.strip() for t in tags_raw.split(",") if t.strip()] if tags_raw else []
    return {
        "name": form.get("name", "").strip(),
        "price": form.get("price"),
        "salePrice": sale_price,
        "category": form.get("category", "").strip(),
        "description": form.get("description", "").strip(),
        "hairColors": form.getlist("hair_color"),
        "length_stock": length_stock,
        "stock": sum(length_stock.values()) if length_stock else form.get("stock"),
        "texture": form.get("texture", "").strip(),
        "hairType": form.get("hairType", "").strip(),
        "origin": form.get("origin", "").strip(),
        "weight": form.get("weight", "").strip(),
        "closureType": form.get("closureType", "").strip(),
        "capType": form.get("capType", "").strip(),
        "bundleInfo": form.get("bundleInfo", "").strip(),
        "tags": tags,
        "featured": form.get("featured") == "on",
        "newArrival": form.get("newArrival") == "on",
        "wholesaleAvailable": form.get("wholesaleAvailable") == "on",
    }

@app.route("/admin/edit-product/<product_id>", methods=["GET", "POST"])
@token_required
def edit_product(current_admin, product_id):
    obj_id = safe_objectid(product_id)
    if not obj_id: flash("Invalid ID.", "error"); return redirect(url_for("admin_products"))
    product = products_collection.find_one({"_id": obj_id})
    if not product: flash("Not found.", "error"); return redirect(url_for("admin_products"))
    if request.method == "POST":
        f = _read_product_form(request.form)
        valid, msg = validate_product_data(f["name"], f["price"], f["stock"], f["category"])
        if not valid: flash(msg, "error"); return redirect(url_for("edit_product", product_id=product_id))
        update_data = {
            "name": f["name"], "price": float(f["price"]), "salePrice": f["salePrice"], "category": f["category"],
            "stock": int(f["stock"]), "description": f["description"],
            "lengths": list(f["length_stock"].keys()), "lengthStock": f["length_stock"], "hairColors": f["hairColors"],
            "texture": f["texture"], "hairType": f["hairType"], "origin": f["origin"], "weight": f["weight"],
            "closureType": f["closureType"], "capType": f["capType"], "bundleInfo": f["bundleInfo"], "tags": f["tags"],
            "featured": f["featured"], "newArrival": f["newArrival"], "wholesaleAvailable": f["wholesaleAvailable"],
        }
        if 'image' in request.files and request.files['image'].filename:
            file = request.files['image']
            if not allowed_file(file.filename): flash("Invalid file.", "error"); return redirect(url_for("edit_product", product_id=product_id))
            url = upload_image(file)
            if url: update_data["image"] = url
            else: flash("Upload failed.", "error"); return redirect(url_for("edit_product", product_id=product_id))
        products_collection.update_one({"_id": obj_id}, {"$set": update_data})
        flash("Updated!", "success")
        return redirect(url_for("admin_products"))
    return render_template("edit_product.html", product=convert_doc(product))

@app.route("/admin/add-product", methods=["POST"])
@token_required
def add_product(current_admin):
    f = _read_product_form(request.form)
    valid, msg = validate_product_data(f["name"], f["price"], f["stock"], f["category"])
    if not valid: flash(msg, "error"); return redirect(url_for("admin_add_product_page"))
    if 'image' not in request.files: flash("No image.", "error"); return redirect(url_for("admin_add_product_page"))
    file = request.files['image']
    if not file.filename: flash("No file.", "error"); return redirect(url_for("admin_add_product_page"))
    if not allowed_file(file.filename): flash("Invalid type.", "error"); return redirect(url_for("admin_add_product_page"))
    url = upload_image(file)
    if not url: flash("Upload failed.", "error"); return redirect(url_for("admin_add_product_page"))
    products_collection.insert_one({
        "name": f["name"], "price": float(f["price"]), "salePrice": f["salePrice"], "image": url,
        "description": f["description"], "stock": int(f["stock"]),
        "category": f["category"], "lengths": list(f["length_stock"].keys()), "lengthStock": f["length_stock"],
        "hairColors": f["hairColors"], "texture": f["texture"], "hairType": f["hairType"], "origin": f["origin"],
        "weight": f["weight"], "closureType": f["closureType"], "capType": f["capType"], "bundleInfo": f["bundleInfo"],
        "tags": f["tags"], "featured": f["featured"], "newArrival": f["newArrival"], "wholesaleAvailable": f["wholesaleAvailable"],
        "created_at": datetime.datetime.utcnow()
    })
    flash("Added!", "success")
    return redirect(url_for("admin_products"))

@app.route("/admin/delete-product/<product_id>")
@token_required
def delete_product(current_admin, product_id):
    obj_id = safe_objectid(product_id)
    if not obj_id: flash("Invalid ID.", "error"); return redirect(url_for("admin_products"))
    products_collection.delete_one({"_id": obj_id})
    flash("Deleted!", "success")
    return redirect(url_for("admin_products"))

@app.route("/admin/update-order/<reference>", methods=["POST"])
@token_required
def update_order(current_admin, reference):
    status = request.form.get("status")
    if not status: flash("Status required.", "error"); return redirect(url_for("admin_orders"))
    orders_collection.update_one({"paymentReference": reference}, {"$set": {"status": status}})
    flash("Updated!", "success")
    return redirect(url_for("admin_orders"))

@app.route("/admin/delete-order/<reference>")
@token_required
def delete_order(current_admin, reference):
    result = orders_collection.delete_one({"paymentReference": reference})
    if result.deleted_count == 0: flash("Order not found.", "error")
    else: flash("Order deleted.", "success")
    return redirect(url_for("admin_orders"))

@app.route("/admin/clear-orders")
@token_required
def clear_orders(current_admin):
    result = orders_collection.delete_many({})
    flash(f"Deleted {result.deleted_count} orders.", "success")
    return redirect(url_for("admin_orders"))

@app.errorhandler(404)
def page_not_found(e): return render_template("404.html"), 404

@app.errorhandler(500)
def internal_server_error(e): return render_template("500.html"), 500

if __name__ == "__main__":
    app.run(host='0.0.0.0', port=int(os.environ.get("PORT", 5000)))
