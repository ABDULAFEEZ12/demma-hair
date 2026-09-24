import os
from pymongo.mongo_client import MongoClient
from pymongo.server_api import ServerApi
from dotenv import load_dotenv

load_dotenv()

uri = os.getenv("MONGO_URI")
if not uri:
    print("MONGO_URI not set in .env")
    exit()

client = MongoClient(uri, server_api=ServerApi('1'))

try:
    client.admin.command('ping')
    print("Connected to MongoDB!")

    db = client["dimmahairshop"]
    db.connection_tests.insert_one({"status": "testing"})
    result = db.connection_tests.find_one({"status": "testing"})
    print("Data from DB:", result)

except Exception as e:
    print("Error:", e)
