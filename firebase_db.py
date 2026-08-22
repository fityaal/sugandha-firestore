"""
firebase_db.py
Initializes the Firebase Admin SDK and exposes a shared Firestore client,
plus small helpers used throughout the route modules so every blueprint
talks to Firestore the same way.
"""
import os
import time
import firebase_admin
from firebase_admin import credentials, firestore
from google.cloud.firestore_v1 import FieldFilter

_app = None
db = None


def init_firebase():
    """Call once, from create_app(). Safe to call multiple times (no-op after first)."""
    global _app, db
    if _app is not None:
        return db

    cred_path = os.environ.get('FIREBASE_CREDENTIALS', 'serviceAccountKey.json')
    project_id = os.environ.get('FIREBASE_PROJECT_ID')  # optional, read from key file if omitted

    if os.path.exists(cred_path):
        cred = credentials.Certificate(cred_path)
        _app = firebase_admin.initialize_app(cred, {'projectId': project_id} if project_id else None)
    else:
        # Falls back to Application Default Credentials (e.g. on Cloud Run / GCE,
        # or `gcloud auth application-default login` in local dev).
        _app = firebase_admin.initialize_app(options={'projectId': project_id} if project_id else None)

    db = firestore.client()
    return db


def doc_to_dict(doc):
    """Convert a Firestore DocumentSnapshot into a plain dict with its id included."""
    if doc is None or not doc.exists:
        return None
    data = doc.to_dict() or {}
    data['id'] = doc.id
    return data


def docs_to_list(docs):
    return [doc_to_dict(d) for d in docs]


def next_sequence(counter_name: str, start_at: int = 0) -> int:
    """
    Atomically increments and returns the next integer in a named sequence,
    stored in the 'counters' collection. Replaces MySQL's bill_counter table
    and AUTO_INCREMENT-style needs for human-readable bill numbers.
    """
    ref = db.collection('counters').document(counter_name)

    @firestore.transactional
    def _increment(transaction):
        snapshot = ref.get(transaction=transaction)
        current = snapshot.get('last_number') if snapshot.exists else start_at
        current = current or start_at
        new_value = current + 1
        transaction.set(ref, {'last_number': new_value}, merge=True)
        return new_value

    transaction = db.transaction()
    return _increment(transaction)


def peek_sequence(counter_name: str, start_at: int = 0) -> int:
    """Read the next value WITHOUT incrementing (for display only)."""
    snap = db.collection('counters').document(counter_name).get()
    current = snap.get('last_number') if snap.exists else start_at
    current = current or start_at
    return current + 1


# ---------------------------------------------------------------------------
# Read caching
#
# The dashboard, stock overview, and both reports don't do server-side
# GROUP BY / LIKE (Firestore can't), so they pull an entire collection into
# Python and filter/aggregate there. Each document in that pull is a billed
# Firestore "read" -- with thousands of historical purchase/sale records,
# a handful of page loads can burn through the free tier's 50,000-reads/day
# quota in minutes. This is a short-TTL, per-process cache for exactly those
# whole-collection reads, so repeated page loads within a few seconds reuse
# the same data instead of re-fetching every document each time.
#
# Writes (save/delete) call invalidate_cache() for the affected collection
# so your own changes always show up immediately; other staff/devices will
# see them within CACHE_TTL_SECONDS.
# ---------------------------------------------------------------------------
CACHE_TTL_SECONDS = 20
_collection_cache = {}  # collection_name -> (fetched_at, list_of_dicts)


def cached_docs_list(collection_name: str, ttl: int = None):
    if ttl is None:
        ttl = CACHE_TTL_SECONDS
    now = time.time()
    cached = _collection_cache.get(collection_name)
    if cached and (now - cached[0]) < ttl:
        return cached[1]
    docs = docs_to_list(db.collection(collection_name).stream())
    _collection_cache[collection_name] = (now, docs)
    return docs


def invalidate_cache(collection_name: str):
    _collection_cache.pop(collection_name, None)
