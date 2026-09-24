import bcrypt
from pymongo import MongoClient
import os
from dotenv import load_dotenv

load_dotenv()

MONGO_URI = os.getenv("MONGO_URI")

if not MONGO_URI:
    print("MONGO_URI not found")
    exit()

client = MongoClient(MONGO_URI)
db = client["dimmahairshop"]
admins = db["admins"]

email = os.getenv("ADMIN_SEED_EMAIL", "admin@dimmahairshop.com")
password = os.getenv("ADMIN_SEED_PASSWORD", "admin123")

if admins.find_one({"email": email}):
    print("Admin already exists")
else:
    hashed_password = bcrypt.hashpw(password.encode("utf-8"), bcrypt.gensalt())
    admins.insert_one({
        "email": email,
        "password": hashed_password,
        "role": "superadmin"
    })
    print("Admin created successfully")

print("Using DB:", db.name)
