import bcrypt
from pymongo import MongoClient
import certifi
import os
from dotenv import load_dotenv

load_dotenv()

MONGO_URI = os.getenv("MONGO_URI")
if not MONGO_URI:
    print("MONGO_URI not set in .env")
    exit()

client = MongoClient(MONGO_URI, tlsCAFile=certifi.where())
db = client["dimmahairshop"]
admins = db["admins"]

email = os.getenv("ADMIN_SEED_EMAIL", "admin@dimmahairshop.com")
password = os.getenv("ADMIN_SEED_PASSWORD", "admin123")

# Delete old admin if exists
admins.delete_one({"email": email})

# Create new admin
hashed = bcrypt.hashpw(password.encode("utf-8"), bcrypt.gensalt())
admins.insert_one({
    "email": email,
    "password": hashed,
    "role": "superadmin"
})

print("=" * 50)
print("ADMIN CREATED")
print("=" * 50)
print(f"Email:    {email}")
print(f"Password: {password}")
print("=" * 50)
print("\nLogin at: http://127.0.0.1:5000/admin/login")
print("Change this password after first login.")
