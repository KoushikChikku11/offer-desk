"""Offer Desk dashboard.   streamlit run app.py   (or: streamlit run app.py -- --db data/demo.db)"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import altair as alt
import pandas as pd
import streamlit as st

sys.path.insert(0, str(Path(__file__).parent / "src"))

from offerdesk import analytics, config, db, network  # noqa: E402
from offerdesk.scoring import explain  # noqa: E402

AMBER, GREEN, RED, GREY, BG, PANEL = "#FFA028", "#4AF626", "#FF433D", "#8A8A8A", "#000000", "#0B0B0B"

st.set_page_config(page_title="Offer Desk", page_icon="📈", layout="wide")
st.markdown(
    f"""
    <style>
      html, body, [class*="css"], .stApp {{ background:{BG}; color:{AMBER};
        font-family: 'IBM Plex Mono', 'Menlo', 'Consolas', monospace; }}
      h1, h2, h3, h4 {{ color:{AMBER}; letter-spacing:0.04em; text-transform:uppercase; }}
      [data-testid="stMetricValue"] {{ color:{AMBER}; }}
      [data-testid="stMetricLabel"] {{ color:{GREY}; text-transform:uppercase; }}
      .stTabs [data-baseweb="tab"] {{ color:{GREY}; text-transform:uppercase; }}
      .stTabs [aria-selected="true"] {{ color:{AMBER} !important; border-bottom-color:{AMBER} !important; }}
      .banner {{ background:{AMBER}; color:#000; padding:4px 10px; font-weight:700; letter-spacing:0.08em; }}
      .muted {{ color:{GREY}; }}
      header[data-testid="stHeader"] {{ background:{BG}; }}
      pre, code, [data-testid="stCode"] * {{ background:{PANEL} !important; color:{AMBER} !important; }}
      [data-testid="stCode"] {{ border:1px solid #222; }}
      label, .stRadio p, .stMultiSelect p {{ color:{AMBER} !important; }}
    </style>
    """,
    unsafe_allow_html=True,
)


def _args():
    p = argparse.ArgumentParser()
    p.add_argument("--db", default=str(config.DEFAULT_DB))
    return p.parse_known_args()[0]


ARGS = _args()
DB_PATH = Path(ARGS.db)
IS_DEMO = DB_PATH.name == "demo.db"
cfg = config.settings()


@st.cache_data(ttl=30)
def load(path: str):
    with db.connect(path) as conn:
        postings = pd.read_sql_query("SELECT * FROM postings", conn)
        apps, events = db.applications_frame(conn)
        contacts = pd.read_sql_query("SELECT * FROM contacts", conn)
        due = pd.DataFrame(network.due_followups(conn))
        coverage = pd.DataFrame(network.firm_coverage(conn))
    return postings, apps, events, contacts, due, coverage


postings, apps, events, contacts, due, coverage = load(str(DB_PATH))
outcomes = (analytics.build_outcomes(apps, events, cfg.get("analytics", {}).get("ghost_after_days", 30))
            if not apps.empty else pd.DataFrame())

title = "OFFER DESK" + ("  ·  SYNTHETIC DEMO DATA" if IS_DEMO else "")
st.markdown(f'<div class="banner">{title}</div>', unsafe_allow_html=True)

open_q = postings[(postings["active"] == 1) & (postings["status"] == "new")] if not postings.empty else postings
c1, c2, c3, c4, c5 = st.columns(5)
c1.metric("Open in queue", int((open_q["score"] >= 20).sum()) if not open_q.empty else 0)
c2.metric("Applications", len(outcomes))
c3.metric("Past screen", f"{outcomes['advanced'].mean():.0%}" if len(outcomes) else "n/a")
c4.metric("Interviews", int(outcomes["interviewed"].sum()) if len(outcomes) else 0)
c5.metric("Follow ups due", len(due))

tab_q, tab_p, tab_a, tab_n = st.tabs(["Queue", "Pipeline", "Analytics", "Network"])


def ci_chart(df: pd.DataFrame, cat: str, title: str, order: list | None = None):
    """Point estimate with a Wilson interval bar. Intervals first, so small n reads as uncertain."""
    if df.empty:
        st.caption("no data yet")
        return
    d = df.copy()
    d[cat] = d[cat].astype(str)
    d["label"] = d.apply(lambda r: f"{int(r['k'])}/{int(r['n'])}", axis=1)
    sort = order if order else alt.EncodingSortField(field="rate", order="descending")
    base = alt.Chart(d).encode(y=alt.Y(f"{cat}:N", sort=sort, title=None,
                                        axis=alt.Axis(labelLimit=180, labelPadding=8)))
    bars = base.mark_rule(color=GREY, strokeWidth=6, opacity=0.5).encode(
        x=alt.X("lo:Q", title="rate (95% Wilson interval)", axis=alt.Axis(format="%"), scale=alt.Scale(domain=[0, 1])),
        x2="hi:Q")
    pts = base.mark_point(filled=True, size=90, color=AMBER).encode(
        x="rate:Q", tooltip=[cat, "label", alt.Tooltip("rate:Q", format=".1%"),
                             alt.Tooltip("lo:Q", format=".1%"), alt.Tooltip("hi:Q", format=".1%")])
    text = base.mark_text(align="left", dx=8, color=GREY).encode(x="hi:Q", text="label")
    st.markdown(f"#### {title}")
    st.altair_chart((bars + pts + text).properties(height={"step": 34}, background=BG)
                    .configure_axis(labelColor=AMBER, titleColor=GREY, gridColor="#222")
                    .configure_view(stroke=None), width="stretch")


with tab_q:
    if open_q.empty:
        st.write("Queue is empty. Run `offerdesk sync`.")
    else:
        fam = st.multiselect("Families", sorted(open_q["family"].dropna().unique()))
        q = open_q[open_q["family"].isin(fam)] if fam else open_q
        q = q.sort_values("score", ascending=False).head(50)
        q["why"] = q["score_detail"].map(lambda s: explain(json.loads(s or "{}")))
        st.dataframe(
            q[["score", "firm", "title", "family", "location", "url", "why"]],
            column_config={"score": st.column_config.ProgressColumn("score", min_value=0, max_value=100, format="%.0f"),
                           "url": st.column_config.LinkColumn("link", display_text="open")},
            hide_index=True, width="stretch",
        )

with tab_p:
    if outcomes.empty:
        st.write("No applications logged yet.")
    else:
        f = analytics.funnel(outcomes)
        st.markdown("#### Funnel")
        chart = alt.Chart(f).mark_bar(color=AMBER).encode(
            x=alt.X("count:Q", title=None), y=alt.Y("stage:N", sort=list(f["stage"]), title=None),
            tooltip=["stage", "count", alt.Tooltip("rate:Q", format=".1%")])
        st.altair_chart(chart.properties(height=200, background=BG)
                        .configure_axis(labelColor=AMBER, gridColor="#222").configure_view(stroke=None),
                        width="stretch")
        show = outcomes.sort_values("applied_at", ascending=False)[
            ["id", "applied_at", "firm", "title", "resume_version", "referred", "status", "response_days"]]
        st.dataframe(show, hide_index=True, width="stretch", height=420)

with tab_a:
    if outcomes.empty:
        st.write("Log some applications first, or run `offerdesk demo` and open the demo database.")
    else:
        metric = st.radio("Outcome", list(analytics.METRICS), horizontal=True,
                          format_func=lambda m: analytics.METRICS[m])
        left, right = st.columns(2)
        with left:
            ci_chart(analytics.rate_table(outcomes, "resume_version", metric), "resume_version", "By resume")
            ci_chart(analytics.rate_table(outcomes, "referred", metric), "referred", "Referral vs cold")
        with right:
            ci_chart(analytics.rate_table(outcomes, "apply_lag", metric), "apply_lag", "Days after posting",
                     order=[b[2] for b in analytics.AGE_BUCKETS] + ["unknown"])
            ci_chart(analytics.rate_table(outcomes, "family", metric), "family", "By role family")

        st.markdown("#### A vs B")
        for col, a, b in (("resume_version", "CSQNT", "FIN"), ("referred", True, False)):
            st.code(analytics.describe_comparison(analytics.compare(outcomes, col, a, b, metric)), language=None)

        audit = analytics.router_audit(outcomes, metric)
        if not audit.empty:
            ci_chart(audit, "choice", "Router audit: following vs overriding the pick")

        st.markdown("#### Time to first reply")
        km = analytics.kaplan_meier(outcomes["response_days"], outcomes["replied"])
        med = analytics.median_survival(km)
        st.caption("Kaplan Meier estimate of P(no reply yet) by day. Silent applications are censored, "
                   "not counted as rejections." + (f" Median {med:.0f} days." if med else ""))
        st.altair_chart(alt.Chart(km).mark_line(interpolate="step-after", color=AMBER).encode(
            x=alt.X("t:Q", title="days since applying"),
            y=alt.Y("survival:Q", title="still waiting", axis=alt.Axis(format="%"), scale=alt.Scale(domain=[0, 1])))
            .properties(height=260, background=BG)
            .configure_axis(labelColor=AMBER, titleColor=GREY, gridColor="#222").configure_view(stroke=None),
            width="stretch")

with tab_n:
    left, right = st.columns(2)
    with left:
        st.markdown("#### Follow ups due")
        if due.empty:
            st.caption("nothing due")
        else:
            st.dataframe(due[["name", "firm", "status", "days_quiet"]], hide_index=True, width="stretch")
        st.markdown("#### Contacts")
        if not contacts.empty:
            st.dataframe(contacts[["name", "firm", "role", "relationship", "status", "last_contact"]],
                         hide_index=True, width="stretch")
    with right:
        st.markdown("#### Coverage of target firms")
        st.caption("Firms in your queue or pipeline where you do not know anyone yet are at the top.")
        if not coverage.empty:
            st.dataframe(coverage, hide_index=True, width="stretch", height=480)
