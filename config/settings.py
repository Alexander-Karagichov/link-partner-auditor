import os
from pathlib import Path
from dotenv import load_dotenv

BASE_DIR = Path(__file__).resolve().parent.parent

load_dotenv(BASE_DIR / ".env")


# ── SEMrush ──────────────────────────────────────────────────────────────────
SEMRUSH_API_KEY: str = os.getenv("SEMRUSH_API_KEY", "")
SEMRUSH_API_BASE: str = "https://api.semrush.com"
SEMRUSH_ANALYTICS_BASE: str = "https://api.semrush.com/analytics/v1"

# ── Bright Data ───────────────────────────────────────────────────────────────
# Single Bearer API Key – found at brightdata.com/cp/setting/users
BRIGHTDATA_API_KEY: str = os.getenv("BRIGHTDATA_API_KEY", "")

# Zone names (configurable via .env so the user can override if needed)
BRIGHTDATA_WEB_UNLOCKER_ZONE: str = os.getenv("BRIGHTDATA_WEB_UNLOCKER_ZONE", "web_unlocker1")
BRIGHTDATA_SERP_ZONE: str = os.getenv("BRIGHTDATA_SERP_ZONE", "serp_api_marketing_make_com")

# REST API endpoints
BRIGHTDATA_UNLOCKER_ENDPOINT: str = "https://api.brightdata.com/request"
BRIGHTDATA_SERP_ENDPOINT: str = "https://api.brightdata.com/serp/req"

# ── OpenAI ────────────────────────────────────────────────────────────────────
OPENAI_API_KEY: str = os.getenv("OPENAI_API_KEY", "")
OPENAI_MODEL: str = os.getenv("OPENAI_MODEL", "gpt-4o")

# ── App Settings ──────────────────────────────────────────────────────────────
MAX_CONCURRENT_AUDITS: int = int(os.getenv("MAX_CONCURRENT_AUDITS", "3"))
REQUEST_TIMEOUT: int = int(os.getenv("REQUEST_TIMEOUT", "60"))
MAX_KEYWORDS_CHECK: int = int(os.getenv("MAX_KEYWORDS_CHECK", "50"))

# ── File Paths ────────────────────────────────────────────────────────────────
KEYWORDS_DIR: Path = BASE_DIR / "keywords"
DATA_DIR: Path = BASE_DIR / "data"
REPORTS_DIR: Path = BASE_DIR / "reports"

CORE_KEYWORDS_FILE: Path = KEYWORDS_DIR / "bright_data_core_keywords.txt"
PORN_GAMBLING_KEYWORDS_FILE: Path = KEYWORDS_DIR / "porn_gambling_keywords.txt"
KNOWN_BAD_SITES_FILE: Path = DATA_DIR / "known_bad_sites.txt"
COMPETITOR_SITES_FILE: Path = DATA_DIR / "competitor_sites.txt"
LINKBUILDING_TARGETS_FILE: Path = KEYWORDS_DIR / "linkbuilding_targets.txt"

# Target domain used in link-building recommendations
LINKBUILDING_TARGET_DOMAIN: str = os.getenv("LINKBUILDING_TARGET_DOMAIN", "brightdata.com")

# Ensure output directories exist
REPORTS_DIR.mkdir(exist_ok=True)


def validate_config() -> list[str]:
    """Return a list of missing critical environment variable names."""
    missing = []
    if not SEMRUSH_API_KEY:
        missing.append("SEMRUSH_API_KEY")
    if not BRIGHTDATA_API_KEY:
        missing.append("BRIGHTDATA_API_KEY")
    if not OPENAI_API_KEY:
        missing.append("OPENAI_API_KEY")
    return missing
