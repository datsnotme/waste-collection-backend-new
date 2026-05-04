import os
import firebase_admin
from firebase_admin import credentials, messaging

_firebase_ready = False


def _initialize_firebase():
    global _firebase_ready

    if _firebase_ready:
        return True

    if firebase_admin._apps:
        _firebase_ready = True
        return True

    cred_path = os.getenv(
        "GOOGLE_APPLICATION_CREDENTIALS",
        "firebase_service_account.json",
    )

    if not os.path.exists(cred_path):
        print(f"⚠️ Firebase credentials file not found: {cred_path}")
        print("⚠️ FCM disabled. Backend will continue without push notifications.")
        return False

    try:
        cred = credentials.Certificate(cred_path)
        firebase_admin.initialize_app(cred)
        _firebase_ready = True
        print("✅ Firebase Admin initialized successfully")
        return True
    except Exception as e:
        print(f"❌ Firebase initialization failed: {e}")
        return False


def send_data_to_topic(topic: str, data: dict):
    if not _initialize_firebase():
        print(f"⚠️ Skipping FCM send to topic={topic} because Firebase is not configured.")
        return None

    try:
        message = messaging.Message(
            topic=topic,
            data={str(k): "" if v is None else str(v) for k, v in data.items()},
            android=messaging.AndroidConfig(priority="high"),
        )

        response = messaging.send(message)
        print(f"✅ FCM sent successfully: {response}")
        return response
    except Exception as e:
        print(f"❌ FCM send failed: {e}")
        return None