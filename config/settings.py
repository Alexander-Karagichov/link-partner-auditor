import os
from pathlib import Path
from dotenv import load_dotenv

BASE_DIR = Path(__file__).resolve().parent.parent

load_dotenv(BASE_DIR / ".env")


# ── SEMrush ──────────────────────────────────────────────────────────────────
SEMRUSH_API_KEY: str = os.getenv("SEMRUSH_API_KEY", "")
SEMRUSH_API_BASE: str = "https://api.semrush.com"
SEMRUSH_ANALYTICS_BASE: str = "https://api.semrush.com/analytics/v1"

# ── DataForSEO (alternative SEO provider) ─────────────────────────────────────
DATAFORSEO_LOGIN: str = os.getenv("DATAFORSEO_LOGIN", "")
DATAFORSEO_PASSWORD: str = os.getenv("DATAFORSEO_PASSWORD", "")
DATAFORSEO_LOCATION_CODE: int = int(os.getenv("DATAFORSEO_LOCATION_CODE", "2840"))  # 2840 = United States
DATAFORSEO_LANGUAGE: str = os.getenv("DATAFORSEO_LANGUAGE", "English")

# ── SEO Provider Selection ────────────────────────────────────────────────────
# Which backend provides SEO data: "semrush" (default) or "dataforseo".
SEO_PROVIDER: str = os.getenv("SEO_PROVIDER", "semrush").strip().lower()

# How many of the domain's top traffic countries to run ranking/keyword checks
# against (SEMrush). Default 1 = just the #1 market (most efficient, and the
# market that matters most). Raise for more coverage at more API units;
# 0 falls back to the US database.
SEMRUSH_TOP_COUNTRIES: int = int(os.getenv("SEMRUSH_TOP_COUNTRIES", "1"))

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

# ── Anthropic (Claude) ────────────────────────────────────────────────────────
ANTHROPIC_API_KEY: str = os.getenv("ANTHROPIC_API_KEY", "")
ANTHROPIC_MODEL: str = os.getenv("ANTHROPIC_MODEL", "claude-opus-4-8")

# ── LLM Provider Selection ────────────────────────────────────────────────────
# Which backend the AI analysis uses: "openai" (default) or "anthropic".
LLM_PROVIDER: str = os.getenv("LLM_PROVIDER", "openai").strip().lower()

# ── App Settings ──────────────────────────────────────────────────────────────
MAX_CONCURRENT_AUDITS: int = int(os.getenv("MAX_CONCURRENT_AUDITS", "3"))
REQUEST_TIMEOUT: int = int(os.getenv("REQUEST_TIMEOUT", "60"))
MAX_KEYWORDS_CHECK: int = int(os.getenv("MAX_KEYWORDS_CHECK", "50"))

# ── Throughput / reliability tuning ───────────────────────────────────────────
# How many of a domain's own sub-queries (SERP terms, SEMrush filter terms) run
# at once. Lower = gentler on the APIs (fewer timeouts), higher = faster.
INNER_CONCURRENCY: int = int(os.getenv("INNER_CONCURRENCY", "5"))
# Bright Data Web Unlocker can be slow under load — give it a longer timeout and
# retry on read-timeouts so concurrent audits don't fail spuriously.
BRIGHTDATA_TIMEOUT: int = int(os.getenv("BRIGHTDATA_TIMEOUT", "60"))
BRIGHTDATA_MAX_RETRIES: int = int(os.getenv("BRIGHTDATA_MAX_RETRIES", "2"))
# Cap how many flagged pages get deep-checked per domain (bounds runtime on
# spam-heavy domains that rank for hundreds of gambling pages).
MAX_DEEP_PAGES_PER_DOMAIN: int = int(os.getenv("MAX_DEEP_PAGES_PER_DOMAIN", "25"))
# Max Google site: SERP check terms run per domain (each term = 1 Bright Data
# request). Caps the serp_porn_gambling_keywords.txt list regardless of its length.
SERP_MAX_TERMS: int = int(os.getenv("SERP_MAX_TERMS", "10"))
# Reciprocal-link (PBN) check: max strange outbound domains whose homepage we
# fetch to see if they link back. Each = 1 Bright Data scrape. 0 disables.
RECIPROCAL_MAX_CHECKS: int = int(os.getenv("RECIPROCAL_MAX_CHECKS", "10"))
ENABLE_RECIPROCITY: bool = os.getenv("ENABLE_RECIPROCITY", "true").strip().lower() in ("1", "true", "yes")

# ── File Paths ────────────────────────────────────────────────────────────────
KEYWORDS_DIR: Path = BASE_DIR / "keywords"
DATA_DIR: Path = BASE_DIR / "data"
REPORTS_DIR: Path = BASE_DIR / "reports"

SEMRUSH_CORE_KEYWORDS_FILE: Path = KEYWORDS_DIR / "semrush_core_business_keywords.txt"
SERP_CORE_KEYWORDS_FILE: Path = KEYWORDS_DIR / "serp_core_business_keywords.txt"
PORN_GAMBLING_KEYWORDS_FILE: Path = KEYWORDS_DIR / "semrush_porn_gambling_keywords.txt"
SERP_CHECK_TERMS_FILE: Path = KEYWORDS_DIR / "serp_porn_gambling_keywords.txt"
KNOWN_BAD_SITES_FILE: Path = DATA_DIR / "known_bad_sites.txt"
COMPETITOR_SITES_FILE: Path = DATA_DIR / "competitor_sites.txt"
LINKBUILDING_TARGETS_FILE: Path = KEYWORDS_DIR / "linkbuilding_targets.txt"
LEGIT_DOMAINS_FILE: Path = DATA_DIR / "legit_domains.txt"

# Target domain used in link-building recommendations
LINKBUILDING_TARGET_DOMAIN: str = os.getenv("LINKBUILDING_TARGET_DOMAIN", "brightdata.com")

# Ensure output directories exist
REPORTS_DIR.mkdir(exist_ok=True)


def validate_config() -> list[str]:
    """Return a list of missing critical environment variable names."""
    missing = []
    # SEO provider — only the active one's credentials are required.
    if SEO_PROVIDER == "dataforseo":
        if not DATAFORSEO_LOGIN or not DATAFORSEO_PASSWORD:
            missing.append("DATAFORSEO_LOGIN/DATAFORSEO_PASSWORD")
    elif not SEMRUSH_API_KEY:
        missing.append("SEMRUSH_API_KEY")
    if not BRIGHTDATA_API_KEY:
        missing.append("BRIGHTDATA_API_KEY")
    # Only the active LLM provider's key is required.
    if LLM_PROVIDER == "anthropic":
        if not ANTHROPIC_API_KEY:
            missing.append("ANTHROPIC_API_KEY")
    elif not OPENAI_API_KEY:
        missing.append("OPENAI_API_KEY")
    return missing
