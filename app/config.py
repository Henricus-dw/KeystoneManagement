"""Application configuration for Keystone."""
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent

STATIC_DIR = BASE_DIR / "static"
TEMPLATES_DIR = BASE_DIR / "templates"
UPLOADS_DIR = STATIC_DIR / "uploads"
DB_PATH = BASE_DIR / "keystone.db"

DATABASE_URL = f"sqlite:///{DB_PATH}"

# In a real deployment this would come from the environment. It only signs the
# session cookie, so a static dev value is fine for an internal demo.
SECRET_KEY = "keystone-internal-demo-key-change-me"
SESSION_COOKIE = "keystone_session"

APP_NAME = "Keystone"
APP_TAGLINE = "Project execution, in focus."

UPLOADS_DIR.mkdir(parents=True, exist_ok=True)
