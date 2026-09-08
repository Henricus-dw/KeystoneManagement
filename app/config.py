"""Application configuration for Keystone."""
import os
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent

STATIC_DIR = BASE_DIR / "static"
TEMPLATES_DIR = BASE_DIR / "templates"
UPLOADS_DIR = STATIC_DIR / "uploads"
# Server cover images live OUTSIDE the (locked, read-only) static tree, in a
# directory the service can actually write to. They are served through an
# authenticated route, so they never need to sit under the public /static mount.
DATA_DIR = BASE_DIR / "data"
SERVER_IMAGES_DIR = DATA_DIR / "server_images"
DB_PATH = BASE_DIR / "keystone.db"

DATABASE_URL = f"sqlite:///{DB_PATH}"

# In a real deployment this would come from the environment. It only signs the
# session cookie, so a static dev value is fine for an internal demo.
SECRET_KEY = "keystone-internal-demo-key-change-me"
SESSION_COOKIE = "keystone_session"

APP_NAME = "Keystone"
APP_TAGLINE = "Project execution, in focus."

SMTP_HOST = os.getenv("KEYSTONE_SMTP_HOST", "")
SMTP_PORT = int(os.getenv("KEYSTONE_SMTP_PORT", "587"))
SMTP_USERNAME = os.getenv("KEYSTONE_SMTP_USERNAME", "")
SMTP_PASSWORD = os.getenv("KEYSTONE_SMTP_PASSWORD", "")
SMTP_FROM = os.getenv("KEYSTONE_SMTP_FROM", SMTP_USERNAME or "keystone@localhost")
APP_BASE_URL = os.getenv("KEYSTONE_APP_BASE_URL", "http://127.0.0.1:8000").rstrip("/")

UPLOADS_DIR.mkdir(parents=True, exist_ok=True)
SERVER_IMAGES_DIR.mkdir(parents=True, exist_ok=True)
