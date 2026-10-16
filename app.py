"""
Conference Tracker: Streamlit front-end
Reads the Google Sheet published by the Apps Script tracker (Conferences + Change log)
and turns it into a searchable view of upcoming meetings, deadlines and changes.

Run locally:   pip install -r requirements.txt   then   streamlit run app.py
Data sources:  the two published-CSV links below, or override them in Streamlit secrets
               (CONFERENCES_CSV, CHANGELOG_CSV) or environment variables of the same names.
"""
import os
from datetime import date, datetime, timedelta, timezone

import pandas as pd
import plotly.graph_objects as go
import streamlit as st

DEFAULT_CONFERENCES_CSV = ("https://docs.google.com/spreadsheets/d/e/2PACX-1vS_ct1Ua3bjynHf3YhOOCY2QhtpaQDLTa0VVBQ1U4LCCxotnc"
                           "GwZ4dN8ZK-aENthjMXixvnjCA9y46f/pub?gid=379608463&single=true&output=csv")
DEFAULT_CHANGELOG_CSV = ("https://docs.google.com/spreadsheets/d/e/2PACX-1vS_ct1Ua3bjynHf3YhOOCY2QhtpaQDLTa0VVBQ1U4LCCxotnc"
                         "GwZ4dN8ZK-aENthjMXixvnjCA9y46f/pub?gid=1159558729&single=true&output=csv")

DATE_COLS = ["Start date", "End date", "Abstract deadline", "Late-breaking deadline",
             "Abstract release date", "Early-bird deadline", "Last checked", "Last changed", "Next check"]
KEY_DATES = {  # column -> how it is described to users
    "Abstract deadline": "Abstract deadline",
    "Late-breaking deadline": "Late-breaking deadline",
    "Abstract release date": "Abstracts released",
    "Early-bird deadline": "Early-bird registration ends",
    "Start date": "Meeting starts",
}
KIND_COLOURS = {  # chosen to read on both light and dark backgrounds
    "Meeting starts": "#2A7F8E",
    "Abstract deadline": "#C2410C",
    "Late-breaking deadline": "#B91C5C",
    "Abstracts released": "#6D4FC2",
    "Early-bird registration ends": "#A16207",
}

st.set_page_config(page_title="Conference tracker", page_icon="🗓️", layout="wide")

st.markdown("""
<style>
@import url('https://fonts.googleapis.com/css2?family=Source+Sans+3:wght@400;600;700&display=swap');
html, body, [class*="css"], .stMarkdown, .stDataFrame { font-family: 'Source Sans 3', system-ui, sans-serif; }
h1 { font-weight: 700; letter-spacing: -0.01em; margin-bottom: 0.1rem; }
.next-up { display: grid; grid-template-columns: repeat(auto-fit, minmax(220px, 1fr)); gap: 0.25rem 1.5rem;
           margin: 0.5rem 0 1.25rem; }
.next-up .item { border-left: 4px solid var(--kind); padding: 0.35rem 0 0.35rem 0.75rem; }
.next-up .days { font-size: 2rem; font-weight: 700; line-height: 1.1; color: var(--kind); }
.next-up .days small { font-size: 0.9rem; font-weight: 600; opacity: 0.8; }
.next-up .what { font-weight: 600; }
.next-up .meta { opacity: 0.75; font-size: 0.92rem; }
.quiet { opacity: 0.7; font-size: 0.92rem; }
.next-up { grid-template-columns: repeat(auto-fit, minmax(180px, 1fr)); }
[data-baseweb="tag"] { background-color: #2A7F8E !important; }
</style>
""", unsafe_allow_html=True)


# ---------------------------------------------------------------- data
def _source(name, default):
    try:
        if name in st.secrets:
            return st.secrets[name]
    except Exception:
        pass
    return os.environ.get(name, default)


@st.cache_data(ttl=900, show_spinner="Loading the latest tracker data...")
def load_data(conf_src, log_src):
    conf = pd.read_csv(conf_src, dtype=str).dropna(how="all")
    conf = conf[conf["Conference"].notna()].copy()
    for c in DATE_COLS:
        if c in conf.columns:
            conf[c] = pd.to_datetime(conf[c], errors="coerce").dt.normalize()
    conf["Active"] = conf["Active"].fillna("Yes")
    conf["Label"] = conf["Acronym"].fillna(conf["Conference"])
    for c in ["Therapeutic area", "Status", "Confidence", "Review flag", "Notes", "Evidence", "Source URL",
              "Page to watch", "City", "Country", "Edition", "Method", "Page fingerprint", "Organizer"]:
        conf[c] = conf[c].fillna("") if c in conf.columns else ""
    conf["Area group"] = conf["Therapeutic area"].str.split("/").str[0].str.strip().replace("", "Other")

    try:
        log = pd.read_csv(log_src, dtype=str).dropna(how="all")
        log["Timestamp"] = pd.to_datetime(log["Timestamp"], errors="coerce", format="mixed")
        log = log.sort_values("Timestamp", ascending=False)
    except Exception:
        log = pd.DataFrame(columns=["Timestamp", "ID", "Conference", "Field", "Old value", "New value",
                                    "Change type", "Source URL"])
    return conf, log, datetime.now()


def key_dates(conf):
    """One row per (conference, key date)."""
    rows = []
    for _, r in conf.iterrows():
        for col, kind in KEY_DATES.items():
            d = r.get(col)
            if pd.notna(d):
                rows.append({"Date": d, "What": kind, "Conference": r["Label"], "Full name": r["Conference"],
                             "Area": r["Area group"], "City": r["City"], "Confidence": r["Confidence"],
                             "Source": r["Source URL"] or None})
    df = pd.DataFrame(rows, columns=["Date", "What", "Conference", "Full name", "Area", "City", "Confidence", "Source"])
    if not df.empty:
        df["Days left"] = (df["Date"] - pd.Timestamp(date.today())).dt.days
    else:
        df["Days left"] = pd.Series(dtype=int)
    return df.sort_values("Date")


def to_ics(dates_df, conf):
    """Calendar file: one all-day event per key date, plus multi-day meeting blocks."""
    def txt(x):
        return "" if x is None or (isinstance(x, float) and pd.isna(x)) else str(x)

    def esc(s):
        return txt(s).replace("\\", "\\\\").replace(";", "\\;").replace(",", "\\,").replace("\n", "\\n")
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    lines = ["BEGIN:VCALENDAR", "VERSION:2.0", "PRODID:-//Conference Tracker//EN", "CALSCALE:GREGORIAN"]
    for i, r in dates_df.iterrows():
        start = r["Date"].date()
        end = start + timedelta(days=1)
        if r["What"] == "Meeting starts":
            row = conf[conf["Label"] == r["Conference"]].iloc[0]
            if pd.notna(row["End date"]):
                end = row["End date"].date() + timedelta(days=1)
            summary = f"{r['Conference']} ({txt(r['City'])})" if txt(r["City"]) else r["Conference"]
        else:
            summary = f"{r['Conference']}: {r['What']}"
        lines += ["BEGIN:VEVENT", f"UID:{r['Conference']}-{r['What']}-{start:%Y%m%d}@conference-tracker".replace(" ", "-"),
                  f"DTSTAMP:{stamp}", f"DTSTART;VALUE=DATE:{start:%Y%m%d}", f"DTEND;VALUE=DATE:{end:%Y%m%d}",
                  f"SUMMARY:{esc(summary)}",
                  f"DESCRIPTION:{esc(txt(r['Full name']) + ' | source: ' + (txt(r['Source']) or 'n/a'))}", "END:VEVENT"]
    lines.append("END:VCALENDAR")
    return "\r\n".join(lines).encode("utf-8")


conf_all, log, loaded_at = load_data(_source("CONFERENCES_CSV", DEFAULT_CONFERENCES_CSV),
                                     _source("CHANGELOG_CSV", DEFAULT_CHANGELOG_CSV))
today = pd.Timestamp(date.today())

# ---------------------------------------------------------------- sidebar filters
with st.sidebar:
    st.header("Filters")
    areas = sorted(conf_all["Area group"].unique())
    pick_areas = st.multiselect("Therapeutic area", areas, placeholder="All areas")
    window = st.select_slider("Look ahead", options=[30, 60, 90, 180, 365], value=90, format_func=lambda d: f"{d} days")
    kinds = st.pills("Date types", list(KIND_COLOURS), selection_mode="multi", default=list(KIND_COLOURS)) or []
    hide_low = st.toggle("Hide low-confidence dates", value=False,
                         help="Low confidence means the date came from rules or weak evidence and should be checked.")
    search = st.text_input("Search conferences", placeholder="e.g. ASH, Alzheimer, Boston")
    st.divider()
    if st.button("Reload data now", width="stretch"):
        load_data.clear()
        st.rerun()
    st.caption(f"Data loaded {loaded_at:%d %b %Y, %H:%M}. Refreshes automatically every 15 minutes; "
               "the tracker itself updates the sheet hourly.")

conf = conf_all.copy()
if pick_areas:
    conf = conf[conf["Area group"].isin(pick_areas)]
if search:
    s = search.lower()
    conf = conf[conf[["Conference", "Acronym", "City", "Therapeutic area", "Organizer"]]
                .fillna("").apply(lambda c: c.str.lower().str.contains(s, regex=False)).any(axis=1)]
dates = key_dates(conf)
if hide_low and not dates.empty:
    dates = dates[dates["Confidence"].str.lower() != "low"]
dates = dates[dates["What"].isin(kinds)]
upcoming = dates[(dates["Days left"] >= 0) & (dates["Days left"] <= window)]

# ---------------------------------------------------------------- header + next up
st.title("Conference tracker")
n_dated = conf_all["Start date"].notna().sum()
st.markdown(f"<div class='quiet'>{len(conf_all)} conferences tracked, {n_dated} with confirmed or tentative dates. "
            "Every date links back to the page it came from.</div>", unsafe_allow_html=True)

soonest = dates[dates["Days left"] >= 0].head(4)
if soonest.empty:
    st.info("No upcoming dates match these filters yet. Widen the look-ahead window, clear the filters, "
            "or let the tracker check more conferences.")
else:
    items = []
    for _, r in soonest.iterrows():
        colour = KIND_COLOURS.get(r["What"], "#2A7F8E")
        d = int(r["Days left"])
        when = "today" if d == 0 else ("tomorrow" if d == 1 else f"{d}<small> days</small>")
        flag = " (check: low confidence)" if str(r["Confidence"]).lower() == "low" else ""
        items.append(f"<div class='item' style='--kind:{colour}'><div class='days'>{when}</div>"
                     f"<div class='what'>{r['Conference']}: {r['What'].lower()}</div>"
                     f"<div class='meta'>{r['Date']:%a %d %b %Y}{', ' + r['City'] if r['City'] else ''}{flag}</div></div>")
    st.markdown("<div class='next-up'>" + "".join(items) + "</div>", unsafe_allow_html=True)

tab_time, tab_dead, tab_change, tab_conf, tab_quality = st.tabs(
    ["Timeline", "Deadlines", "What changed", "Conferences", "Data quality"])

# ---------------------------------------------------------------- timeline
with tab_time:
    span_end = today + pd.Timedelta(days=window)
    meet = conf[conf["Start date"].notna()].copy()
    meet["End"] = meet["End date"].fillna(meet["Start date"]) + pd.Timedelta(days=1)
    meet = meet[(meet["End"] >= today) & (meet["Start date"] <= span_end)].sort_values("Start date")
    marks = upcoming[upcoming["What"] != "Meeting starts"]
    labels = list(dict.fromkeys(list(meet["Label"]) + list(marks["Conference"])))
    if not labels:
        st.info(f"No meetings or deadlines in the next {window} days for these filters. "
                "Try a longer look-ahead or fewer filters in the sidebar.")
    else:
        fig = go.Figure()
        if "Meeting starts" in kinds:
            for area, grp in meet.groupby("Area group"):
                when = grp["Start date"].dt.strftime("%d %b") + " to " + (grp["End"] - pd.Timedelta(days=1)).dt.strftime("%d %b %Y")
                fig.add_trace(go.Bar(
                    base=grp["Start date"], x=(grp["End"] - grp["Start date"]).dt.total_seconds() * 1000,
                    y=grp["Label"], orientation="h", name=area,
                    customdata=list(zip(grp["Conference"], grp["City"], when)),
                    hovertemplate="<b>%{customdata[0]}</b><br>%{customdata[2]}<br>%{customdata[1]}<extra></extra>",
                ))
        for kind, grp in marks.groupby("What"):
            fig.add_trace(go.Scatter(
                x=grp["Date"], y=grp["Conference"], mode="markers", name=kind,
                marker=dict(symbol="diamond", size=12, color=KIND_COLOURS.get(kind),
                            line=dict(width=1, color="white")),
                customdata=grp[["Full name"]].values,
                hovertemplate="<b>%{customdata[0]}</b><br>" + kind + ": %{x|%d %b %Y}<extra></extra>",
            ))
        fig.add_vline(x=today, line_width=2, line_dash="dot", line_color="#94A3B8")
        fig.add_annotation(x=today, y=1.02, yref="paper", text="today", showarrow=False, font=dict(size=12))
        fig.update_layout(
            barmode="overlay", height=max(320, 34 * len(labels) + 120), margin=dict(l=10, r=10, t=30, b=10),
            xaxis=dict(type="date", range=[today - pd.Timedelta(days=3), span_end], showgrid=True),
            yaxis=dict(categoryorder="array", categoryarray=labels[::-1], title=None),
            legend=dict(orientation="h", yanchor="bottom", y=-0.25 if len(labels) < 6 else -0.12, x=0),
        )
        st.plotly_chart(fig, width="stretch")
        st.caption("Bars are meetings, coloured by therapeutic area. Diamonds are deadlines and abstract releases.")

# ---------------------------------------------------------------- deadlines
with tab_dead:
    if upcoming.empty:
        st.info(f"Nothing due in the next {window} days for these filters.")
    else:
        show = upcoming[["Days left", "Date", "What", "Conference", "Area", "City", "Confidence", "Source"]]
        st.dataframe(
            show, hide_index=True, width="stretch",
            column_config={
                "Days left": st.column_config.NumberColumn(format="%d"),
                "Date": st.column_config.DateColumn(format="ddd D MMM YYYY"),
                "Source": st.column_config.LinkColumn("Source", display_text="open"),
            },
        )
    c1, c2 = st.columns(2)
    c1.download_button("Download these dates as a calendar file (.ics)", to_ics(upcoming, conf),
                       file_name="conference_dates.ics", mime="text/calendar", disabled=upcoming.empty,
                       width="stretch",
                       help="Open the file to add every date to Outlook, Google Calendar or Apple Calendar.")
    c2.download_button("Download these dates as CSV", upcoming.to_csv(index=False).encode("utf-8"),
                       file_name="conference_dates.csv", mime="text/csv", disabled=upcoming.empty,
                       width="stretch")

# ---------------------------------------------------------------- what changed
with tab_change:
    days_back = st.radio("Show changes from the last", [7, 30, 90], index=1, horizontal=True,
                         format_func=lambda d: f"{d} days")
    recent = log[log["Timestamp"] >= pd.Timestamp(datetime.now() - timedelta(days=days_back))]
    if recent.empty:
        st.info(f"No changes in the last {days_back} days. The tracker logs every date it fills in or changes.")
    else:
        kinds_count = recent["Change type"].value_counts()
        st.markdown(" ".join(f"**{v}** {k.lower()}" for k, v in kinds_count.items()) +
                    f" across **{recent['ID'].nunique()}** conference(s).")
        for day, grp in recent.groupby(recent["Timestamp"].dt.date, sort=False):
            st.subheader(f"{day:%A %d %B}")
            for (cid, name), g in grp.groupby(["ID", "Conference"], sort=False):
                parts = []
                for _, r in g.iterrows():
                    old = r["Old value"] if isinstance(r["Old value"], str) and r["Old value"] else None
                    parts.append(f"{r['Field']}: {old + ' → ' if old else ''}**{r['New value']}**")
                src = g["Source URL"].dropna()
                link = f" ([source]({src.iloc[0]}))" if len(src) else ""
                tag = ", ".join(sorted(g["Change type"].dropna().unique()))
                st.markdown(f"**{name}** _{tag.lower()}_{link}  \n" + "; ".join(parts))

# ---------------------------------------------------------------- conferences
with tab_conf:
    table = conf[["Label", "Conference", "Area group", "Start date", "End date", "City", "Status", "Confidence",
                  "Last checked", "Active"]].rename(columns={"Label": "Acronym", "Area group": "Area"})
    st.dataframe(table.sort_values(["Start date", "Acronym"], na_position="last"), hide_index=True,
                 width="stretch",
                 column_config={c: st.column_config.DateColumn(format="D MMM YYYY")
                                for c in ["Start date", "End date", "Last checked"]})
    choice = st.selectbox("Open a conference", ["Choose one..."] + list(conf.sort_values("Label")["Label"]))
    if choice != "Choose one...":
        r = conf[conf["Label"] == choice].iloc[0]
        st.subheader(r["Conference"])
        if r["Edition"]:
            st.caption(r["Edition"])
        left, right = st.columns([1, 1])
        with left:
            fmt = lambda d: f"{d:%a %d %b %Y}" if pd.notna(d) else "not published yet"
            meeting = fmt(r["Start date"]) + (f" to {fmt(r['End date'])}" if pd.notna(r["End date"]) else "")
            st.markdown(f"**Meeting:** {meeting}  \n**Where:** {', '.join(x for x in [r['City'], r['Country']] if x) or 'not published yet'}")
            for col, kind in KEY_DATES.items():
                if col != "Start date":
                    st.markdown(f"**{kind}:** {fmt(r[col])}")
        with right:
            st.markdown(f"**Status:** {r['Status'] or 'not checked yet'}  \n**Confidence:** {r['Confidence'] or 'n/a'}  \n"
                        f"**Read by:** {r['Method'] or 'n/a'}  \n**Last checked:** {fmt(r['Last checked'])}")
            if r["Evidence"]:
                st.markdown(f"> {r['Evidence']}")
            if r["Source URL"]:
                st.markdown(f"[Open the source page]({r['Source URL']})")
            if r["Review flag"]:
                st.warning(r["Review flag"])

# ---------------------------------------------------------------- data quality
with tab_quality:
    active = conf_all[conf_all["Active"].str.lower() != "no"]
    checked7 = (active["Last checked"] >= today - pd.Timedelta(days=7)).sum()
    m1, m2, m3, m4 = st.columns(4)
    m1.metric("Have meeting dates", f"{conf_all['Start date'].notna().sum()} / {len(conf_all)}")
    m2.metric("Checked in the last 7 days", f"{checked7} / {len(active)}")
    m3.metric("Need a person to check", int((conf_all["Review flag"] != "").sum()))
    m4.metric("Handled manually", int((conf_all["Active"].str.lower() == "no").sum()))

    def reason(r):
        flag, note = r["Review flag"], r["Notes"]
        if str(r["Active"]).lower() == "no":
            return "Manual", "Site blocks automated access. Keep dates up to date by hand."
        if "HTTP 403" in flag or "Could not read" in flag:
            return "Page unreadable", "Put a more specific dates or abstracts page in 'Page to watch'."
        if "did not finish" in flag:
            return "Check timed out", "Will retry automatically; if it keeps happening, try another page."
        if r.get("Page fingerprint") == "d41d8cd98f00b204e9800998ecf8427e" and pd.isna(r["Start date"]):
            return "No dates found on page", "The page loads but shows no dates (often built with JavaScript). Try its 'key dates' or abstracts page."
        if str(r["Confidence"]).lower() == "low":
            return "Low confidence", "Compare the dates with the source page and correct if needed."
        if r["Status"] == "TBA":
            return "Next edition not announced", "Nothing to do; it will update when dates are published."
        if not r["Page to watch"] and pd.isna(r["Start date"]) and ("No working page" in note or "no upcoming dates" in note):
            return "No page to watch", "Paste the official dates page into 'Page to watch'."
        if pd.isna(r["Last checked"]):
            return "Not checked yet", "Will be checked on an upcoming scheduled run."
        return None, None

    issues = conf_all.apply(lambda r: pd.Series(reason(r), index=["Issue", "What to do"]), axis=1)
    attention = pd.concat([conf_all[["Label", "Conference"]], issues], axis=1).dropna(subset=["Issue"])
    st.markdown(f"**{len(attention)}** conference(s) need attention or are still waiting for their first check.")
    if not attention.empty:
        counts = attention["Issue"].value_counts()
        st.bar_chart(counts, horizontal=True, height=min(60 + 32 * len(counts), 320))
        st.dataframe(attention.rename(columns={"Label": "Acronym"}).sort_values("Issue"),
                     hide_index=True, width="stretch")
