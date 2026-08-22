"""
migrate_to_firestore.py
------------------------------------------------------------
Run this ONCE to copy your existing MySQL data (items, firms,
purchases, sales, shop_settings, bill_counter, users) into the
new Firestore database. It preserves the old MySQL integer ids
as the Firestore document ids, so item_id / firm_id references
inside purchases and sales stay valid without a separate remap
step.

Usage:
    pip install mysqlclient firebase-admin
    python migrate_to_firestore.py

Requires the same MySQL connection this app used to use, plus
serviceAccountKey.json (or FIREBASE_CREDENTIALS env var) for the
target Firestore project. Safe to re-run: it overwrites documents
with the same id rather than duplicating them.
------------------------------------------------------------
"""
import os
import MySQLdb
import MySQLdb.cursors
from datetime import datetime, date

import firebase_admin
from firebase_admin import credentials, firestore

# ---- MySQL source config -- edit these or set as env vars ----
MYSQL_HOST = os.environ.get('MYSQL_HOST', 'localhost')
MYSQL_USER = os.environ.get('MYSQL_USER', 'root')
MYSQL_PASSWORD = os.environ.get('MYSQL_PASSWORD', '')
MYSQL_DB = os.environ.get('MYSQL_DB', 'sugandha')

# ---- Firestore target ----
cred_path = os.environ.get('FIREBASE_CREDENTIALS', 'serviceAccountKey.json')
cred = credentials.Certificate(cred_path)
firebase_admin.initialize_app(cred)
db = firestore.client()


def clean(v):
    """MySQL DATE/DECIMAL -> plain python types Firestore is happy with."""
    if isinstance(v, date) and not isinstance(v, datetime):
        return v.strftime('%Y-%m-%d')
    if hasattr(v, '__float__') and type(v).__name__ == 'Decimal':
        return float(v)
    return v


def migrate_table(table, collection, id_field='id'):
    conn = MySQLdb.connect(host=MYSQL_HOST, user=MYSQL_USER, passwd=MYSQL_PASSWORD,
                            db=MYSQL_DB, cursorclass=MySQLdb.cursors.DictCursor)
    cur = conn.cursor()
    cur.execute(f"SELECT * FROM {table}")
    rows = cur.fetchall()
    batch = db.batch()
    count = 0
    for row in rows:
        doc_id = str(row.pop(id_field))
        data = {k: clean(v) for k, v in row.items()}
        ref = db.collection(collection).document(doc_id)
        batch.set(ref, data)
        count += 1
        if count % 400 == 0:  # Firestore batch limit is 500 writes
            batch.commit()
            batch = db.batch()
    batch.commit()
    cur.close()
    conn.close()
    print(f"[OK] Migrated {count} rows: {table} -> collection '{collection}'")


def migrate_purchases_and_sales_int_refs():
    """
    purchases.item_id / firm_id and sales.item_id were MySQL ints.
    After migrate_table() they were copied as-is (ints), which is fine --
    firebase_db.py / the route files compare against Firestore doc ids as
    strings, so normalize them to strings here.
    """
    for collection, fields in [('purchases', ['item_id', 'firm_id']), ('sales', ['item_id'])]:
        docs = db.collection(collection).stream()
        for doc in docs:
            data = doc.to_dict()
            updates = {}
            for f in fields:
                if data.get(f) is not None and not isinstance(data[f], str):
                    updates[f] = str(data[f])
            if 'sold' in data and isinstance(data['sold'], int):
                updates['sold'] = bool(data['sold'])
            if updates:
                doc.reference.update(updates)
    print("[OK] Normalized item_id/firm_id/sold field types.")


def migrate_bill_counters():
    conn = MySQLdb.connect(host=MYSQL_HOST, user=MYSQL_USER, passwd=MYSQL_PASSWORD,
                            db=MYSQL_DB, cursorclass=MySQLdb.cursors.DictCursor)
    cur = conn.cursor()
    cur.execute("SELECT * FROM bill_counter")
    for row in cur.fetchall():
        db.collection('counters').document(row['bill_type']).set({'last_number': row['last_number']})
    cur.close()
    conn.close()
    print("[OK] Migrated bill_counter -> collection 'counters'")


def migrate_shop_settings():
    conn = MySQLdb.connect(host=MYSQL_HOST, user=MYSQL_USER, passwd=MYSQL_PASSWORD,
                            db=MYSQL_DB, cursorclass=MySQLdb.cursors.DictCursor)
    cur = conn.cursor()
    cur.execute("SELECT * FROM shop_settings WHERE id=1")
    row = cur.fetchone()
    if row:
        row.pop('id', None)
        data = {k: clean(v) for k, v in row.items()}
        db.collection('shop_settings').document('main').set(data)
    cur.close()
    conn.close()
    print("[OK] Migrated shop_settings -> shop_settings/main")


def migrate_users():
    """Optional: only if you have a `users` table already in MySQL."""
    conn = MySQLdb.connect(host=MYSQL_HOST, user=MYSQL_USER, passwd=MYSQL_PASSWORD,
                            db=MYSQL_DB, cursorclass=MySQLdb.cursors.DictCursor)
    cur = conn.cursor()
    try:
        cur.execute("SELECT * FROM users")
    except MySQLdb.ProgrammingError:
        print("[SKIP] No 'users' table found in MySQL -- app.py will seed a default admin user instead.")
        cur.close()
        conn.close()
        return
    for row in cur.fetchall():
        username = row.pop('username')
        row.pop('id', None)
        data = {k: clean(v) for k, v in row.items()}
        db.collection('users').document(username).set(data)
    cur.close()
    conn.close()
    print("[OK] Migrated users -> collection 'users'")


if __name__ == '__main__':
    migrate_table('items', 'items')
    migrate_table('firms', 'firms')
    migrate_table('purchases', 'purchases')
    migrate_table('sales', 'sales')
    migrate_purchases_and_sales_int_refs()
    migrate_bill_counters()
    migrate_shop_settings()
    migrate_users()
    print("\nMigration complete. Spot-check a few records in the Firebase console before decommissioning MySQL.")
