"""Firebase Cloud Messaging for push notifications."""
import os
import json
import logging
import base64
from pathlib import Path
from typing import Optional, List
from dataclasses import dataclass, field
from datetime import datetime

logger = logging.getLogger(__name__)

# Try to import firebase-admin
try:
    import firebase_admin
    from firebase_admin import credentials, messaging
    FIREBASE_AVAILABLE = True
except ImportError:
    FIREBASE_AVAILABLE = False
    logger.warning("firebase-admin not installed. Run: pip install firebase-admin")


@dataclass
class DeviceToken:
    """Registered device token."""
    token: str
    device_name: str = ""
    platform: str = "android"
    registered_at: float = 0
    last_used: float = 0


class FirebaseNotificationService:
    """
    Firebase Cloud Messaging service for sending push notifications.

    Setup:
    1. Create a Firebase project at https://console.firebase.google.com
    2. Go to Project Settings > Service Accounts
    3. Generate new private key (downloads JSON file)
    4. Place the JSON file as 'firebase-credentials.json' in the project root
       OR set FIREBASE_CREDENTIALS_JSON environment variable with the JSON content
    """

    def __init__(self, credentials_path: str = None):
        self.initialized = False
        self.app = None
        self._device_tokens: dict[str, DeviceToken] = {}
        self._tokens_file = Path("data/fcm_tokens.json")

        if not FIREBASE_AVAILABLE:
            logger.error("Firebase Admin SDK not available")
            return

        # Try to initialize Firebase
        self._init_firebase(credentials_path)

        # Load saved device tokens
        self._load_tokens()

    def _init_firebase(self, credentials_path: str = None):
        """Initialize Firebase Admin SDK."""
        try:
            # Check if already initialized
            try:
                self.app = firebase_admin.get_app()
                self.initialized = True
                logger.info("Firebase already initialized")
                return
            except ValueError:
                pass  # Not initialized yet

            cred = None

            # Try credentials path
            if credentials_path and Path(credentials_path).exists():
                cred = credentials.Certificate(credentials_path)
                logger.info(f"Using Firebase credentials from: {credentials_path}")

            # Try default path
            elif Path("firebase-credentials.json").exists():
                cred = credentials.Certificate("firebase-credentials.json")
                logger.info("Using Firebase credentials from: firebase-credentials.json")

            # Try environment variable (JSON string)
            elif os.environ.get("FIREBASE_CREDENTIALS_JSON"):
                cred_dict = json.loads(os.environ["FIREBASE_CREDENTIALS_JSON"])
                cred = credentials.Certificate(cred_dict)
                logger.info("Using Firebase credentials from environment variable")

            # Try environment variable (base64 encoded)
            elif os.environ.get("FIREBASE_CREDENTIALS_BASE64"):
                cred_json = base64.b64decode(os.environ["FIREBASE_CREDENTIALS_BASE64"]).decode()
                cred_dict = json.loads(cred_json)
                cred = credentials.Certificate(cred_dict)
                logger.info("Using Firebase credentials from base64 environment variable")

            if cred:
                self.app = firebase_admin.initialize_app(cred)
                self.initialized = True
                logger.info("Firebase initialized successfully")
            else:
                logger.warning(
                    "Firebase credentials not found. "
                    "Place firebase-credentials.json in project root or set FIREBASE_CREDENTIALS_JSON env var"
                )

        except Exception as e:
            logger.error(f"Failed to initialize Firebase: {e}")

    def _load_tokens(self):
        """Load saved device tokens from file."""
        try:
            if self._tokens_file.exists():
                with open(self._tokens_file, 'r') as f:
                    data = json.load(f)
                    for token_data in data.get('tokens', []):
                        token = DeviceToken(**token_data)
                        self._device_tokens[token.token] = token
                logger.info(f"Loaded {len(self._device_tokens)} device tokens")
        except Exception as e:
            logger.error(f"Failed to load device tokens: {e}")

    def _save_tokens(self):
        """Save device tokens to file."""
        try:
            self._tokens_file.parent.mkdir(parents=True, exist_ok=True)
            data = {
                'tokens': [
                    {
                        'token': t.token,
                        'device_name': t.device_name,
                        'platform': t.platform,
                        'registered_at': t.registered_at,
                        'last_used': t.last_used,
                    }
                    for t in self._device_tokens.values()
                ]
            }
            with open(self._tokens_file, 'w') as f:
                json.dump(data, f, indent=2)
        except Exception as e:
            logger.error(f"Failed to save device tokens: {e}")

    def register_token(self, token: str, device_name: str = "", platform: str = "android") -> bool:
        """
        Register a device token for push notifications.

        Args:
            token: FCM device token
            device_name: Optional friendly name for the device
            platform: 'android' or 'ios'

        Returns:
            True if registered successfully
        """
        if not token:
            return False

        now = datetime.now().timestamp()

        if token in self._device_tokens:
            # Update existing token
            self._device_tokens[token].device_name = device_name or self._device_tokens[token].device_name
            self._device_tokens[token].last_used = now
        else:
            # Add new token
            self._device_tokens[token] = DeviceToken(
                token=token,
                device_name=device_name,
                platform=platform,
                registered_at=now,
                last_used=now,
            )
            logger.info(f"Registered new device: {device_name or 'Unknown'} ({platform})")

        self._save_tokens()
        return True

    def unregister_token(self, token: str) -> bool:
        """Remove a device token."""
        if token in self._device_tokens:
            del self._device_tokens[token]
            self._save_tokens()
            return True
        return False

    def get_registered_devices(self) -> List[dict]:
        """Get list of registered devices."""
        return [
            {
                'token': t.token[:20] + '...',  # Truncate for security
                'device_name': t.device_name,
                'platform': t.platform,
                'registered_at': t.registered_at,
                'last_used': t.last_used,
            }
            for t in self._device_tokens.values()
        ]

    def send_notification(
        self,
        title: str,
        body: str,
        image_url: str = None,
        data: dict = None,
        tokens: List[str] = None,
    ) -> dict:
        """
        Send push notification to registered devices.

        Args:
            title: Notification title
            body: Notification body text
            image_url: Optional image URL to display
            data: Optional data payload
            tokens: Specific tokens to send to (default: all registered)

        Returns:
            Dict with success/failure counts
        """
        if not self.initialized:
            logger.warning("Firebase not initialized, cannot send notification")
            return {'success': 0, 'failure': 0, 'error': 'Firebase not initialized'}

        target_tokens = tokens or list(self._device_tokens.keys())

        if not target_tokens:
            logger.warning("No device tokens registered")
            return {'success': 0, 'failure': 0, 'error': 'No devices registered'}

        # Build notification
        notification = messaging.Notification(
            title=title,
            body=body,
            image=image_url,
        )

        # Android-specific config
        android_config = messaging.AndroidConfig(
            priority='high',
            notification=messaging.AndroidNotification(
                icon='ic_stat_icon_config_sample',
                color='#00d4aa',
                sound='default',
                channel_id='person_alerts',
                priority='high',
                visibility='public',
            ),
        )

        # Build message
        message_data = data or {}
        message_data['timestamp'] = str(datetime.now().timestamp())

        results = {'success': 0, 'failure': 0, 'failed_tokens': []}

        # Send to each token
        for token in target_tokens:
            try:
                message = messaging.Message(
                    notification=notification,
                    android=android_config,
                    data={k: str(v) for k, v in message_data.items()},
                    token=token,
                )

                response = messaging.send(message)
                results['success'] += 1

                # Update last used time
                if token in self._device_tokens:
                    self._device_tokens[token].last_used = datetime.now().timestamp()

            except messaging.UnregisteredError:
                # Token is no longer valid, remove it
                logger.warning(f"Removing invalid token: {token[:20]}...")
                self.unregister_token(token)
                results['failure'] += 1
                results['failed_tokens'].append(token[:20])

            except Exception as e:
                logger.error(f"Failed to send to token {token[:20]}...: {e}")
                results['failure'] += 1
                results['failed_tokens'].append(token[:20])

        if results['success'] > 0:
            self._save_tokens()

        logger.info(f"Notification sent: {results['success']} success, {results['failure']} failed")
        return results

    def send_person_alert(
        self,
        screenshot_base64: str = None,
        person_count: int = 1,
        confidence: float = 0.9,
        timestamp: float = None,
    ) -> dict:
        """
        Send person detection alert notification.

        Args:
            screenshot_base64: Base64 encoded screenshot (not used directly in FCM)
            person_count: Number of people detected
            confidence: Detection confidence
            timestamp: Detection timestamp

        Returns:
            Send results
        """
        title = "Person Detected"
        body = f"{person_count} {'people' if person_count > 1 else 'person'} detected"

        if confidence:
            body += f" ({confidence*100:.0f}% confidence)"

        data = {
            'type': 'person_alert',
            'person_count': person_count,
            'confidence': confidence,
            'alert_timestamp': timestamp or datetime.now().timestamp(),
        }

        return self.send_notification(title=title, body=body, data=data)


# Global instance
_firebase_service: Optional[FirebaseNotificationService] = None


def get_firebase_service() -> FirebaseNotificationService:
    """Get or create the global Firebase service instance."""
    global _firebase_service
    if _firebase_service is None:
        _firebase_service = FirebaseNotificationService()
    return _firebase_service


def init_firebase(credentials_path: str = None) -> FirebaseNotificationService:
    """Initialize Firebase service with optional credentials path."""
    global _firebase_service
    _firebase_service = FirebaseNotificationService(credentials_path)
    return _firebase_service
