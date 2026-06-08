"""
Website Audit Automation – Streamlit UI
========================================
Run with:  streamlit run app.py
"""

from __future__ import annotations

import io
import json
import logging
import time
from datetime import datetime
from pathlib import Path
from typing import Optional

import pandas as pd
import streamlit as st

# ── Page config (must be first Streamlit call) ─────────────────────────────────
st.set_page_config(
    page_title="Website Audit Automation",
    page_icon="🔍",
    layout="wide",
    initial_sidebar_state="expanded",
)

logging.basicConfig(level=logging.INFO, format="%(levelname)s | %(name)s | %(message)s")

# Lazy imports so the page loads even if some deps are missing
try:
    from config import settings
    from config.settings import validate_config
    from audit.audit_engine import audit_bulk, AuditResult, reload_keywords
    from services.link_checker_service import reload_bad_domains
    _imports_ok = True
except Exception as _import_err:
    _imports_ok = False
    _import_error_msg = str(_import_err)


# ── CSS ───────────────────────────────────────────────────────────────────────
st.markdown(
    """
<style>
/* General */
.main .block-container { padding-top: 1.5rem; }

/* Risk badges */
.badge {
    display: inline-block;
    padding: 3px 10px;
    border-radius: 12px;
    font-size: 0.78rem;
    font-weight: 700;
    letter-spacing: 0.04em;
}
.badge-NO_RISK  { background:#198754; color:#fff; }
.badge-CLEAN    { background:#d4edda; color:#155724; }
.badge-LOW      { background:#cce5ff; color:#004085; }
.badge-MEDIUM   { background:#fff3cd; color:#856404; }
.badge-HIGH     { background:#f8d7da; color:#721c24; }
.badge-CRITICAL { background:#721c24; color:#fff; }
.badge-UNKNOWN  { background:#e2e3e5; color:#383d41; }
.badge-ERROR    { background:#343a40; color:#fff; }

/* Metric cards */
.metric-card {
    background: #f8f9fa;
    border: 1px solid #dee2e6;
    border-radius: 8px;
    padding: 12px 16px;
    text-align: center;
}
.metric-value { font-size: 1.6rem; font-weight: 700; color: #212529; }
.metric-label { font-size: 0.78rem; color: #6c757d; margin-top: 2px; }

/* Flag section */
.flag-box {
    background: #fff3cd;
    border-left: 4px solid #ffc107;
    padding: 10px 14px;
    border-radius: 4px;
    margin-bottom: 8px;
    font-size: 0.9rem;
}
.flag-box-red {
    background: #f8d7da;
    border-left: 4px solid #dc3545;
}
.flag-box-green {
    background: #d4edda;
    border-left: 4px solid #28a745;
}
</style>
""",
    unsafe_allow_html=True,
)


# ── Helpers ────────────────────────────────────────────────────────────────────

RISK_EMOJI = {
    "NO_RISK": "🟢",
    "CLEAN": "✅",
    "LOW": "🔵",
    "MEDIUM": "🟡",
    "HIGH": "🔴",
    "CRITICAL": "🚨",
    "UNKNOWN": "❓",
    "ERROR": "💥",
}

RISK_ORDER = ["CRITICAL", "HIGH", "MEDIUM", "LOW", "CLEAN", "NO_RISK", "UNKNOWN", "ERROR"]


def _badge(level: str) -> str:
    return f'<span class="badge badge-{level}">{RISK_EMOJI.get(level,"")} {level}</span>'


def _fmt_number(val: Optional[int]) -> str:
    if val is None:
        return "N/A"
    if val >= 1_000_000:
        return f"{val/1_000_000:.1f}M"
    if val >= 1_000:
        return f"{val/1_000:.1f}K"
    return str(val)


def _fmt_float(val: Optional[float], decimals: int = 1) -> str:
    return "N/A" if val is None else f"{val:.{decimals}f}"


def _results_to_df(results: list[AuditResult], show_anchor: bool = True) -> pd.DataFrame:
    rows = []
    for r in results:
        row = {
            "Domain": r.domain,
            "Risk": r.risk_level,
            "Niche": r.ai_analysis.get("website_niche", "") if r.ai_analysis else "",
            "Authority Score": r.authority_score,
            "Organic Traffic": r.organic_traffic,
            "Ref. Domains": r.referring_domains,
            "Backlinks": r.total_backlinks,
            "Core KW Hits": len(r.core_keyword_hits),
            "P/G KW Hits": len(r.porn_gambling_keyword_hits),
            "Bad Links": len(r.bad_links_found) + sum(len(c.get("bad_links", [])) for c in r.deep_page_checks),
            "Competitor Links": len(r.competitor_links_found),
            "SERP P/G Results": len(r.serp_porn_gambling_results),
            "Is a Competitor?": "Yes" if r.ai_analysis.get("competitor_risk") else "No",
        }
        if show_anchor:
            row["Anchor Text"] = r.link_recommendation.get("best_keyword", "") if r.link_recommendation else ""
        rows.append(row)
    return pd.DataFrame(rows)


def _export_excel(results: list[AuditResult]) -> bytes:
    df = _results_to_df(results)
    buf = io.BytesIO()
    with pd.ExcelWriter(buf, engine="openpyxl") as writer:
        df.to_excel(writer, sheet_name="Summary", index=False)
        # Detailed sheets per domain
        for r in results:
            detail = r.to_dict()
            flat = {k: str(v) for k, v in detail.items()}
            pd.DataFrame([flat]).T.reset_index().rename(
                columns={"index": "Field", 0: "Value"}
            ).to_excel(writer, sheet_name=r.domain[:30], index=False)
    return buf.getvalue()


# ── Sidebar ────────────────────────────────────────────────────────────────────

def render_sidebar() -> None:
    with st.sidebar:
        st.image(
            "https://brightdata.com/favicon.ico",
            width=32,
        )
        st.title("⚙️ Settings")

        st.subheader("API Keys")
        st.caption("Keys are read from **.env** – edit that file to update them.")

        if not _imports_ok:
            st.error(f"Import error: {_import_error_msg}")
            return

        missing = validate_config()
        if missing:
            st.warning(f"Missing env vars: {', '.join(missing)}")
        else:
            st.success("All API keys configured ✓")

        st.divider()
        st.subheader("Keyword Files")
        core_path = settings.CORE_KEYWORDS_FILE
        pg_path = settings.PORN_GAMBLING_KEYWORDS_FILE
        lb_path = settings.LINKBUILDING_TARGETS_FILE

        if core_path.exists():
            core_count = sum(
                1 for l in core_path.read_text().splitlines()
                if l.strip() and not l.startswith("#")
            )
            st.caption(f"Core keywords: **{core_count}** entries")
        else:
            st.error("Core keywords file missing")

        if pg_path.exists():
            pg_count = sum(
                1 for l in pg_path.read_text().splitlines()
                if l.strip() and not l.startswith("#")
            )
            st.caption(f"Porn/Gambling keywords: **{pg_count}** entries")
        else:
            st.error("Porn/Gambling keywords file missing")

        bad_path = settings.KNOWN_BAD_SITES_FILE
        if bad_path.exists():
            bd_count = sum(
                1 for l in bad_path.read_text().splitlines()
                if l.strip() and not l.startswith("#")
            )
            st.caption(f"Known bad sites: **{bd_count}** entries")

        comp_path = settings.COMPETITOR_SITES_FILE
        if comp_path.exists():
            comp_count = sum(
                1 for l in comp_path.read_text().splitlines()
                if l.strip() and not l.startswith("#")
            )
            st.caption(f"Competitor sites: **{comp_count}** entries")

        if lb_path.exists():
            lb_count = sum(
                1 for l in lb_path.read_text().splitlines()
                if l.strip() and not l.startswith("#") and " - " in l
            )
            st.caption(f"Link-building targets: **{lb_count}** keyword→URL pairs")
        else:
            st.warning("⚠️ `keywords/linkbuilding_targets.txt` not found – link recommendations will be skipped.")

        if st.button("🔄 Reload keyword & bad-site lists", use_container_width=True):
            reload_keywords()
            reload_bad_domains()
            st.success("Lists reloaded!")

        st.divider()
        st.subheader("Presentation Mode")
        st.toggle("Show link-building targets", value=True, key="show_lb_targets")
        st.toggle("Show Anchor Text column", value=True, key="show_anchor_toggle")
        st.caption("Toggle these off before sharing a screenshot or screen share.")

        st.divider()
        st.subheader("Concurrency")
        conc = st.slider(
            "Max concurrent audits",
            min_value=1,
            max_value=10,
            value=settings.MAX_CONCURRENT_AUDITS,
            help="Higher = faster but uses more API quota",
        )
        settings.MAX_CONCURRENT_AUDITS = conc

        st.divider()
        st.caption(
            "**Bright Data zones used:**\n"
            f"- Web Unlocker: `{settings.BRIGHTDATA_WEB_UNLOCKER_ZONE}`\n"
            f"- SERP API: `{settings.BRIGHTDATA_SERP_ZONE}`\n\n"
            "Override zone names in `.env` if needed."
        )


# ── Results rendering ──────────────────────────────────────────────────────────

def render_summary_table(results: list[AuditResult]) -> None:
    """Render a colour-coded summary table of all audited domains."""
    st.subheader("📊 Audit Summary")

    show_anchor = st.session_state.get("show_anchor_toggle", True)
    df = _results_to_df(results, show_anchor=show_anchor)
    # Sort by risk severity
    df["_risk_order"] = df["Risk"].map({r: i for i, r in enumerate(RISK_ORDER)})
    df = df.sort_values("_risk_order").drop(columns=["_risk_order"])

    def highlight_risk(val: str) -> str:
        colours = {
            "NO_RISK":  "background-color:#198754;color:#ffffff",
            "CLEAN":    "background-color:#d4edda;color:#155724",
            "LOW":      "background-color:#cce5ff;color:#004085",
            "MEDIUM":   "background-color:#fff3cd;color:#5c4a00",
            "HIGH":     "background-color:#f8d7da;color:#5c1010",
            "CRITICAL": "background-color:#721c24;color:#ffffff",
            "ERROR":    "background-color:#343a40;color:#ffffff",
        }
        return colours.get(val, "")

    def highlight_traffic(val) -> str:
        try:
            v = int(val)
        except (TypeError, ValueError):
            return ""
        if v >= 100_000:
            return "background-color:#28a745;color:white"
        if v >= 10_000:
            return "background-color:#ffc107;color:#212529"
        return ""

    styled = df.style.map(highlight_risk, subset=["Risk"]).map(highlight_traffic, subset=["Organic Traffic"])
    st.dataframe(styled, use_container_width=True, hide_index=True)


def render_domain_detail(result: AuditResult) -> None:
    """Render a full-detail expandable card for a single domain."""
    risk = result.risk_level
    emoji = RISK_EMOJI.get(risk, "❓")

    with st.expander(f"{emoji} **{result.domain}** — {risk}", expanded=False):

        # ── AI Analysis banner ─────────────────────────────────────────────
        if result.ai_analysis.get("summary"):
            comp_risk = result.ai_analysis.get("competitor_risk")
            col_c, col_r = st.columns([1, 3])
            with col_c:
                if comp_risk:
                    st.markdown("**Is a Competitor: ⚠️ Yes**")
                else:
                    st.markdown("**Is a Competitor: ✅ No**")
                st.markdown(f"**Risk: {emoji} {risk}**")
                if result.ai_analysis.get("website_niche"):
                    st.caption(f"🏷️ {result.ai_analysis["website_niche"]}")
            with col_r:
                st.markdown(
                    f"**AI Summary:** {result.ai_analysis.get('summary', 'N/A')}"
                )

            if result.ai_analysis.get("key_findings"):
                st.markdown("**Key Findings:**")
                for finding in result.ai_analysis["key_findings"]:
                    st.markdown(f"- {finding}")

            if result.ai_analysis.get("recommendation"):
                st.info(f"💡 **Recommendation:** {result.ai_analysis['recommendation']}")

        st.divider()

        # ── Metric row ─────────────────────────────────────────────────────
        tabs = st.tabs(["📈 SEO Metrics", "🔑 SEMrush Rankings", "🌍 SERP Check", "🔗 Links & Page Check", "🔗 Link Building", "📋 Raw Data"])

        # Tab 0 – SEO Metrics
        with tabs[0]:
            c1, c2, c3, c4 = st.columns(4)
            with c1:
                st.metric("Authority Score", _fmt_number(result.authority_score))
            with c2:
                st.metric("Organic Traffic/mo", _fmt_number(result.organic_traffic))
            with c3:
                st.metric("Referring Domains", _fmt_number(result.referring_domains))
            with c4:
                st.metric("Total Backlinks", _fmt_number(result.total_backlinks))
            if result.seo_error:
                st.caption(f"⚠️ SEO data error: {result.seo_error}")
            if result.backlinks_error:
                st.caption(f"⚠️ Backlinks data error: {result.backlinks_error}")

        # Tab 1 – Rankings
        with tabs[1]:
            col_a, col_b = st.columns(2)
            with col_a:
                st.markdown(f"**Core Keyword Hits on SEMrush: {len(result.core_keyword_hits)}**")
                if result.core_keyword_hits:
                    st.dataframe(
                        pd.DataFrame(result.core_keyword_hits),
                        use_container_width=True,
                        hide_index=True,
                    )
                else:
                    st.success("No core keyword rankings found for this domain.")
            with col_b:
                st.markdown(f"**Porn/Gambling Keyword Hits on SEMrush: {len(result.porn_gambling_keyword_hits)}**")
                if result.porn_gambling_keyword_hits:
                    st.error("⚠️ Domain ranks for adult/gambling keywords!")
                    st.dataframe(
                        pd.DataFrame(result.porn_gambling_keyword_hits),
                        use_container_width=True,
                        hide_index=True,
                    )
                else:
                    st.success("No porn/gambling keyword rankings found.")
            if result.rankings_error:
                st.caption(f"⚠️ Rankings error: {result.rankings_error}")

        # Tab 3 – Links & Page Check (merged)
        with tabs[3]:
            if result.scrape_error:
                st.warning(f"Scrape error: {result.scrape_error}")

            # ── Aggregate all bad/competitor links ────────────────────────
            all_bad_links_hp = list(result.bad_links_found)  # homepage bad links (no source_page needed)
            all_bad_links_dp: list[dict] = []
            for c in result.deep_page_checks:
                for bl in c.get("bad_links", []):
                    all_bad_links_dp.append(dict(bl, source_page=c["page_url"]))

            all_kw_flags_hp = list(result.keyword_link_flags)
            all_kw_flags_dp: list[str] = []
            for c in result.deep_page_checks:
                all_kw_flags_dp.extend(c.get("keyword_flags", []))

            # ── P/G Links ─────────────────────────────────────────────────
            st.subheader("P/G Links")
            total_pg_issues = (len(all_bad_links_hp) + len(all_bad_links_dp) +
                               len(all_kw_flags_hp) + len(all_kw_flags_dp))
            if total_pg_issues:
                st.error(f"🚨 {total_pg_issues} P/G link issue(s) found across the site.")
            else:
                st.success("No P/G links found anywhere on the site.")

            # Homepage section
            hp_known = [b for b in all_bad_links_hp if not b.get("matched_bad_domain", "").startswith("[AI:")]
            hp_ai = [b for b in all_bad_links_hp if b.get("matched_bad_domain", "").startswith("[AI:")]
            with st.expander(
                f"🏠 Homepage — {'⚠️ issues found' if (hp_known or hp_ai or all_kw_flags_hp) else '✅ clean'}",
                expanded=False,
            ):
                st.caption(f"Total links on page: {result.total_links_on_page}")
                if hp_known:
                    st.error(f"🚨 {len(hp_known)} known bad-site link(s):")
                    st.dataframe(pd.DataFrame(hp_known), use_container_width=True, hide_index=True)
                if hp_ai:
                    st.error(f"🤖 {len(hp_ai)} AI-detected gambling/adult link(s):")
                    st.dataframe(pd.DataFrame(hp_ai), use_container_width=True, hide_index=True)
                if all_kw_flags_hp:
                    st.warning(f"⚠️ {len(all_kw_flags_hp)} external gambling/adult link(s):")
                    for flag in all_kw_flags_hp[:20]:
                        st.markdown(f"- `{flag}`")
                if not hp_known and not hp_ai and not all_kw_flags_hp:
                    st.success("No P/G issues on homepage.")

            # Deep pages
            if not result.deep_page_checks:
                if result.porn_gambling_keyword_hits:
                    st.info("ℹ️ Porn/gambling rankings found but no page URLs were available to deep-check.")
            else:
                for check in result.deep_page_checks:
                    pg_known = [b for b in check.get("bad_links", []) if not b.get("matched_bad_domain", "").startswith("[AI:")]
                    pg_ai = [b for b in check.get("bad_links", []) if b.get("matched_bad_domain", "").startswith("[AI:")]
                    pg_kw = check.get("keyword_flags", [])
                    has_issues = bool(pg_known or pg_ai or pg_kw)
                    with st.expander(
                        f"🔎 {check['page_url']} — {'⚠️ issues found' if has_issues else '✅ clean'}",
                        expanded=False,
                    ):
                        st.caption(f"Triggered by keyword: **{check.get('triggering_keyword', 'N/A')}**  |  Total links: {check.get('total_links', 0)}")
                        if check.get("error"):
                            st.warning(f"Scrape error: {check['error']}")
                        else:
                            if pg_known:
                                st.error(f"🚨 {len(pg_known)} known bad-site link(s):")
                                st.dataframe(pd.DataFrame(pg_known), use_container_width=True, hide_index=True)
                            if pg_ai:
                                st.error(f"🤖 {len(pg_ai)} AI-detected gambling/adult link(s):")
                                st.dataframe(pd.DataFrame(pg_ai), use_container_width=True, hide_index=True)
                            if pg_kw:
                                st.warning(f"⚠️ {len(pg_kw)} external gambling/adult link(s):")
                                for flag in pg_kw[:15]:
                                    st.markdown(f"- `{flag}`")
                            if not pg_known and not pg_ai and not pg_kw:
                                st.success("No P/G issues on this page.")

            # ── Competitor Links ──────────────────────────────────────────
            st.subheader("Competitor Links")
            if result.competitor_links_found:
                st.error(f"🏴 {len(result.competitor_links_found)} competitor link(s) found across the site!")
                st.dataframe(
                    pd.DataFrame(result.competitor_links_found),
                    use_container_width=True,
                    hide_index=True,
                )
            else:
                st.success("No competitor links found across the site.")

        # Tab 2 – SERP Check
        with tabs[2]:
            st.markdown("**Google `site:` search for porn/gambling content**")
            if result.serp_porn_gambling_error:
                st.warning(f"SERP error: {result.serp_porn_gambling_error}")
            if result.serp_porn_gambling_results:
                st.error(
                    f"🚨 Google found {len(result.serp_porn_gambling_results)} result(s) "
                    f"matching adult/gambling queries for this domain!"
                )
                st.dataframe(
                    pd.DataFrame(result.serp_porn_gambling_results),
                    use_container_width=True,
                    hide_index=True,
                )
            else:
                st.success("No adult/gambling pages found via Google site: search.")

        # Tab 4 – Link Building Recommendation
        with tabs[4]:
            rec = result.link_recommendation
            if result.link_recommendation_error:
                st.warning(f"⚠️ {result.link_recommendation_error}")
            elif not rec or not rec.get("best_keyword"):
                st.info("ℹ️ No link-building recommendation generated. Add targets to `keywords/linkbuilding_targets.txt`.")
            else:
                st.markdown("### 🔗 Recommended Link Request")
                c1, c2 = st.columns(2)
                with c1:
                    st.markdown("**Anchor Text (keyword)**")
                    st.code(rec.get("best_keyword", "—"))
                    st.markdown("**Target URL**")
                    target = rec.get("target_url", "—")
                    st.markdown(f"[{target}]({target})" if target.startswith("http") else target)
                with c2:
                    st.markdown("**Suggested Guest Post Topic**")
                    st.info(f"📝 {rec.get('guest_post_topic', '—')}")
                st.markdown("**Why this keyword fits this site:**")
                st.write(rec.get("reasoning", "—"))

        # Tab 5 – Raw Data
        with tabs[5]:
            st.json(result.to_dict())


# ── Main App ───────────────────────────────────────────────────────────────────

def main() -> None:
    render_sidebar()

    st.title("🔍 Website Audit Automation")
    st.caption(
        "Powered by **SEMrush** · **Bright Data** · **OpenAI**  |  "
        f"Date: {datetime.now().strftime('%B %d, %Y')}"
    )

    if not _imports_ok:
        st.error(f"❌ Failed to import dependencies: {_import_error_msg}")
        st.info("Run `pip install -r requirements.txt` and check your .env file.")
        return

    # ── Input ──────────────────────────────────────────────────────────────────
    st.subheader("🌐 Enter Websites to Audit")

    input_method = st.radio(
        "Input method",
        ["Type / Paste URLs", "Upload CSV"],
        horizontal=True,
        label_visibility="collapsed",
    )

    urls_to_audit: list[str] = []

    if input_method == "Type / Paste URLs":
        raw_input = st.text_area(
            "Enter one URL per line",
            height=160,
            placeholder="example.com\nhttps://another-site.org\nthird-domain.net",
            help="You can include or omit the https:// – it will be added automatically.",
        )
        if raw_input.strip():
            urls_to_audit = [
                u.strip() for u in raw_input.strip().splitlines() if u.strip()
            ]
    else:
        uploaded = st.file_uploader(
            "Upload a CSV file with a column named `url` or `domain`",
            type=["csv"],
        )
        if uploaded:
            try:
                df_upload = pd.read_csv(uploaded)
                col = next(
                    (c for c in df_upload.columns if c.lower() in ("url", "domain", "website")),
                    df_upload.columns[0],
                )
                urls_to_audit = df_upload[col].dropna().astype(str).tolist()
                st.success(f"Loaded {len(urls_to_audit)} URLs from CSV.")
            except Exception as exc:
                st.error(f"Could not parse CSV: {exc}")

    if urls_to_audit:
        st.markdown(
            f"**{len(urls_to_audit)} domain(s)** queued: "
            + ", ".join(f"`{u}`" for u in urls_to_audit[:8])
            + ("…" if len(urls_to_audit) > 8 else "")
        )

    # ── Link-building targets (keyword → URL) ─────────────────────────────────
    st.subheader("🔗 Link-Building Targets")
    _lb_file = settings.LINKBUILDING_TARGETS_FILE
    _lb_default = ""
    if _lb_file.exists():
        _lb_default = "\n".join(
            l.strip() for l in _lb_file.read_text(encoding="utf-8").splitlines()
            if l.strip() and not l.startswith("#")
        )
    # Always parse targets from the text area (or file if hidden)
    # so they are available when the audit runs regardless of toggle state
    _show_lb_targets = st.session_state.get("show_lb_targets", True)
    linkbuilding_targets: list[dict] = []
    if _show_lb_targets:
        st.caption(
            "Format: `Keyword - https://your-url.com/page` · one per line · lines starting with `#` are ignored.  "
            "GPT picks the best-matching pair based on the site's content. Leave empty to skip."
        )
        lb_raw = st.text_area(
            "Link-building targets",
            value=_lb_default,
            height=180,
            placeholder="Your keyword - https://www.example.com/page\nAnother keyword - https://www.example.com/other",
            label_visibility="collapsed",
        )
    else:
        lb_raw = _lb_default
        st.caption(f"*Content hidden — toggle in the sidebar to show.*")
    for _line in lb_raw.splitlines():
        _line = _line.strip()
        if not _line or _line.startswith("#") or " - " not in _line:
            continue
        _kw, _, _url = _line.partition(" - ")
        _kw, _url = _kw.strip(), _url.strip()
        if _kw and _url:
            linkbuilding_targets.append({"keyword": _kw, "url": _url})
    if linkbuilding_targets and _show_lb_targets:
        st.caption(f"**{len(linkbuilding_targets)}** keyword→URL pair(s) loaded for this audit.")

    # ── Config warnings ────────────────────────────────────────────────────────
    missing_keys = validate_config()
    if missing_keys:
        st.warning(
            f"⚠️ Missing API keys: **{', '.join(missing_keys)}**.  "
            "Add them to your **.env** file before running an audit."
        )

    # ── Run button ─────────────────────────────────────────────────────────────
    run_disabled = not urls_to_audit or bool(missing_keys)
    run_btn = st.button(
        "🚀 Start Audit",
        type="primary",
        disabled=run_disabled,
        use_container_width=True,
    )

    # ── Session state ──────────────────────────────────────────────────────────
    if "audit_results" not in st.session_state:
        st.session_state.audit_results = []
    if "audit_running" not in st.session_state:
        st.session_state.audit_running = False

    if run_btn and urls_to_audit:
        st.session_state.audit_running = True
        st.session_state.audit_results = []

        # Progress UI
        progress_bar = st.progress(0, text="Starting audit…")
        status_text = st.empty()
        results_so_far: list[AuditResult] = []
        start_time = time.time()

        def _progress_cb(domain_url: str, done: int, total: int) -> None:
            pct = done / total
            elapsed = time.time() - start_time
            eta = (elapsed / done) * (total - done) if done else 0
            progress_bar.progress(
                pct,
                text=f"Audited {done}/{total} domains — ETA {eta:.0f}s",
            )
            status_text.markdown(f"✅ Completed: `{domain_url}`")

        with st.spinner("Running audit pipeline…"):
            results = audit_bulk(
                urls_to_audit,
                progress_callback=_progress_cb,
                linkbuilding_targets=linkbuilding_targets,
            )

        progress_bar.progress(1.0, text="Audit complete!")
        status_text.empty()
        st.session_state.audit_results = results
        st.session_state.audit_running = False

        elapsed = time.time() - start_time
        st.success(
            f"✅ Audit complete! {len(results)} domain(s) processed in {elapsed:.1f}s."
        )

    # ── Render results ─────────────────────────────────────────────────────────
    if st.session_state.audit_results:
        results: list[AuditResult] = st.session_state.audit_results

        # Summary table
        render_summary_table(results)

        # Export buttons
        col_csv, col_xlsx, col_json = st.columns([1, 1, 1])
        df_export = _results_to_df(results)
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")

        with col_csv:
            st.download_button(
                "⬇️ Download CSV",
                data=df_export.to_csv(index=False).encode("utf-8"),
                file_name=f"audit_{timestamp}.csv",
                mime="text/csv",
                use_container_width=True,
            )
        with col_xlsx:
            st.download_button(
                "⬇️ Download Excel",
                data=_export_excel(results),
                file_name=f"audit_{timestamp}.xlsx",
                mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                use_container_width=True,
            )
        with col_json:
            json_data = json.dumps(
                [r.to_dict() for r in results], indent=2, default=str
            )
            st.download_button(
                "⬇️ Download JSON",
                data=json_data.encode("utf-8"),
                file_name=f"audit_{timestamp}.json",
                mime="application/json",
                use_container_width=True,
            )

        st.divider()

        # Detailed per-domain view
        st.subheader("🔎 Detailed Results")
        # Sort: most risky first
        sorted_results = sorted(
            results,
            key=lambda r: RISK_ORDER.index(r.risk_level)
            if r.risk_level in RISK_ORDER
            else len(RISK_ORDER),
        )
        for r in sorted_results:
            render_domain_detail(r)

    # ── Footer ─────────────────────────────────────────────────────────────────
    st.divider()
    with st.expander("ℹ️ About this tool", expanded=False):
        st.markdown(
            """
**Website Audit Automation** checks domains across multiple dimensions:

| Check | Data Source |
|---|---|
| AI Visibility, Mentions, Cited Pages | SEMrush AI Toolkit API |
| Authority Score, Organic Traffic | SEMrush Domain Overview API |
| Referring Domains, Backlinks | SEMrush Analytics API |
| Core keyword rankings | SEMrush Organic Research |
| Porn/Gambling keyword rankings | SEMrush Organic Research |
| Bad outbound links on homepage | Bright Data Web Unlocker (`web_unlocker1`) |
| Google site: porn/gambling check | Bright Data SERP API (`serp_api_marketing_make_com`) |
| Risk scoring & analysis | OpenAI GPT-4o |

**Keyword files** (editable in `/keywords/`):
- `bright_data_core_keywords.txt` – Bright Data's core business keywords
- `porn_gambling_keywords.txt` – adult & gambling terms to flag

**Bad sites list** (editable in `/data/known_bad_sites.txt`):
A curated list of known shady gambling and adult affiliate sites.

**Competitor sites list** (editable in `/data/competitor_sites.txt`):
One domain per line. The audit checks if the target website links out to any of these domains.
""",
            unsafe_allow_html=False,
        )


if __name__ == "__main__":
    main()
