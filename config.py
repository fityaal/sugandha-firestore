import os


class Config:
    SECRET_KEY = os.environ.get('SECRET_KEY', 'sugandha-secret-2024')

    # Path to the Firebase service account JSON key (downloaded from
    # Firebase Console > Project Settings > Service Accounts > Generate new private key).
    # Keep this file OUT of version control (see .gitignore).
    FIREBASE_CREDENTIALS = os.environ.get('FIREBASE_CREDENTIALS', 'serviceAccountKey.json')

    # Optional: only needed if it can't be inferred from the credentials file.
    FIREBASE_PROJECT_ID = os.environ.get('FIREBASE_PROJECT_ID', '')
