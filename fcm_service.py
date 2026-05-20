import os
import json

import firebase_admin
from firebase_admin import credentials, messaging

_firebase_ready = False
_last_error = None


def _initialize_firebase():
    global _firebase_ready, _last_error

    if _firebase_ready:
        return True

    if firebase_admin._apps:
        _firebase_ready = True
        return True

    service_account_json = os.getenv("FIREBASE_SERVICE_ACCOUNT_JSON")
    if service_account_json:
        try:
            cred = credentials.Certificate(json.loads(service_account_json))
            firebase_admin.initialize_app(cred)
            _firebase_ready = True
            print("Firebase Admin initialized from environment variable")
            return True
        except Exception as e:
            _last_error = f"Firebase env credential initialization failed: {e}"
            print(_last_error)

    cred_path = os.getenv(
        "GOOGLE_APPLICATION_CREDENTIALS",
        "firebase_service_account.json",
    )

    if not os.path.exists(cred_path):
        _last_error = f"Firebase credentials file not found: {cred_path}"
        print(_last_error)
        print("FCM disabled. Backend will continue without push notifications.")
        return False

    try:
        cred = credentials.Certificate(cred_path)
        firebase_admin.initialize_app(cred)
        _firebase_ready = True
        print("Firebase Admin initialized successfully")
        return True
    except Exception as e:
        _last_error = f"Firebase initialization failed: {e}"
        print(_last_error)
        return False


def get_firebase_status():
    cred_path = os.getenv(
        "GOOGLE_APPLICATION_CREDENTIALS",
        "firebase_service_account.json",
    )
    ready = _initialize_firebase()
    return {
        "ready": ready,
        "env_json_present": bool(os.getenv("FIREBASE_SERVICE_ACCOUNT_JSON")),
        "credential_path": cred_path,
        "credential_file_exists": os.path.exists(cred_path),
        "last_error": _last_error,
    }


def _string_data(data):
    return {str(k): "" if v is None else str(v) for k, v in (data or {}).items()}


def send_data_to_topic(topic: str, data: dict):
    if not _initialize_firebase():
        print(f"Skipping FCM send to topic={topic} because Firebase is not configured.")
        return None

    try:
        message = messaging.Message(
            topic=topic,
            data=_string_data(data),
            android=messaging.AndroidConfig(priority="high"),
        )

        response = messaging.send(message)
        print(f"FCM topic data sent successfully: {response}")
        return response
    except Exception as e:
        print(f"FCM topic send failed: {e}")
        return None


def send_notification_to_topic(topic: str, title: str, body: str, data=None):
    if not _initialize_firebase():
        print(f"Skipping FCM notification to topic={topic} because Firebase is not configured.")
        return None

    try:
        message = messaging.Message(
            topic=topic,
            notification=messaging.Notification(title=title, body=body),
            data=_string_data(data),
            android=messaging.AndroidConfig(
                priority="high",
                notification=messaging.AndroidNotification(
                    priority="high",
                    default_sound=True,
                ),
            ),
        )

        response = messaging.send(message)
        print(f"FCM topic notification sent successfully: {response}")
        return response
    except Exception as e:
        print(f"FCM topic notification failed: {e}")
        return None


def send_notification_to_token(token: str, title: str, body: str, data=None):
    if not token:
        return None

    if not _initialize_firebase():
        print("FCM disabled. Skipping token notification.")
        return None

    try:
        message = messaging.Message(
            token=token,
            notification=messaging.Notification(title=title, body=body),
            data=_string_data(data),
            android=messaging.AndroidConfig(
                priority="high",
                notification=messaging.AndroidNotification(
                    priority="high",
                    default_sound=True,
                ),
            ),
        )

        response = messaging.send(message)
        print(f"FCM token notification sent: {response}")
        return response
    except Exception as e:
        print(f"FCM token send failed: {e}")
        return None
