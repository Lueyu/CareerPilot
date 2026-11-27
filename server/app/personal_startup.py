"""Import before personal runtime modules so malformed URLs cannot reach engine setup."""
from app.config import get_settings

get_settings().require_secure_personal_startup()
