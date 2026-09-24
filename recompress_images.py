"""
One-off / rerunnable maintenance script: re-compress existing product images
hosted on ImgBB and update each product's `image` URL in MongoDB.

Usage:
    python recompress_images.py            # process everything
    python recompress_images.py --limit 20 # process only the first 20 (dry test)

Safe to re-run: images already small enough are skipped, and a product's
`image` field is only updated after the recompressed upload succeeds.
"""
import os
import sys
import time
import base64
import argparse
from io import BytesIO

import certifi
import requests
from PIL import Image
from pymongo import MongoClient
from dotenv import load_dotenv

load_dotenv()

MONGO_URI = os.getenv("MONGO_URI")
IMGBB_API_KEY = os.getenv("IMGBB_API_KEY")

MAX_IMAGE_DIMENSION = 1600
IMAGE_JPEG_QUALITY = 80
SKIP_IF_UNDER_BYTES = 300 * 1024  # already small enough, don't bother
REQUEST_TIMEOUT = 20
DELAY_BETWEEN_ITEMS = 0.4

client = MongoClient(MONGO_URI, tls=True, tlsCAFile=certifi.where(), tlsAllowInvalidCertificates=True, serverSelectionTimeoutMS=15000)
db = client["dimmahairshop"]
products_collection = db["products"]


def compress_image(file_data):
    img = Image.open(BytesIO(file_data))
    img = img.convert("RGB")
    if max(img.size) > MAX_IMAGE_DIMENSION:
        img.thumbnail((MAX_IMAGE_DIMENSION, MAX_IMAGE_DIMENSION), Image.LANCZOS)
    buffer = BytesIO()
    img.save(buffer, format="JPEG", quality=IMAGE_JPEG_QUALITY, optimize=True)
    return buffer.getvalue()


def upload_to_imgbb(file_data):
    encoded = base64.b64encode(file_data).decode("utf-8")
    resp = requests.post(
        "https://api.imgbb.com/1/upload",
        data={"key": IMGBB_API_KEY, "image": encoded},
        timeout=REQUEST_TIMEOUT,
    )
    result = resp.json()
    if result.get("success"):
        return result["data"]["url"]
    raise RuntimeError(f"imgbb upload failed: {result}")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--limit", type=int, default=None)
    args = parser.parse_args()

    query = {"image": {"$regex": "ibb.co"}}
    cursor = products_collection.find(query, {"_id": 1, "name": 1, "image": 1})
    if args.limit:
        cursor = cursor.limit(args.limit)
    items = list(cursor)

    total = len(items)
    processed = skipped = failed = 0
    bytes_before = bytes_after = 0
    start = time.time()

    print(f"Found {total} products with ibb.co images to check.")

    for i, product in enumerate(items, 1):
        pid = product["_id"]
        url = product.get("image")
        name = product.get("name", "?")
        try:
            r = requests.get(url, timeout=REQUEST_TIMEOUT)
            r.raise_for_status()
            original = r.content
            orig_size = len(original)

            if orig_size <= SKIP_IF_UNDER_BYTES:
                skipped += 1
                print(f"[{i}/{total}] SKIP (already {orig_size/1024:.0f}KB) {name}")
                continue

            compressed = compress_image(original)
            comp_size = len(compressed)

            if comp_size >= orig_size:
                skipped += 1
                print(f"[{i}/{total}] SKIP (compression didn't help) {name}")
                continue

            new_url = upload_to_imgbb(compressed)
            products_collection.update_one({"_id": pid}, {"$set": {"image": new_url}})

            processed += 1
            bytes_before += orig_size
            bytes_after += comp_size
            pct = 100 * (1 - comp_size / orig_size)
            print(f"[{i}/{total}] OK {name}: {orig_size/1024:.0f}KB -> {comp_size/1024:.0f}KB (-{pct:.0f}%)")

        except Exception as e:
            failed += 1
            print(f"[{i}/{total}] FAILED {name} ({url}): {e}")

        time.sleep(DELAY_BETWEEN_ITEMS)

    elapsed = time.time() - start
    print("\n--- Done ---")
    print(f"Processed: {processed}  Skipped: {skipped}  Failed: {failed}  Total: {total}")
    if bytes_before:
        print(f"Size before: {bytes_before/1024/1024:.1f}MB  after: {bytes_after/1024/1024:.1f}MB  saved: {(bytes_before-bytes_after)/1024/1024:.1f}MB")
    print(f"Elapsed: {elapsed/60:.1f} min")


if __name__ == "__main__":
    main()
