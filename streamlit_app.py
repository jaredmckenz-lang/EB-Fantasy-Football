import os
import pandas as pd
import streamlit as st
import altair as alt
import requests
from bs4 import BeautifulSoup
from espn_api.football import League

st.set_page_config(page_title="Fantasy Starter Optimizer", page_icon="🏈", layout="wide")

# ---------- helpers ----------
def safe_proj(val):
    try:
        return float(val or 0)
    except Exception:
        return 0.0

# ---------- FantasyPros scrapers (BeautifulSoup, no lxml) ----------
@st.cache_data(ttl=6*60*60)
def _fetch_fp_table(url: str) -> pd.DataFrame:
    """Generic fetcher for FantasyPros tables with id='data'."""
    res = requests.get(url, headers={"User-Agent": "Mozilla/5.0"})
    res.raise_for_status()
    soup = BeautifulSoup(res.text, "html.parser")
    table = soup.find("table", {"id": "data"})
    if not table:
        return pd.DataFrame()
    thead = table.find("thead")
    tbody = table.find("tbody")
    headers = [th.get_text(strip=True) for th in thead.find_all("th")]
    rows = []
    for tr in tbody.find_all("tr"):
        cols = [td.get_text(strip=True) for td in tr.find_all("td")]
        if cols:
            rows.append(cols)
    df = pd.DataFrame(rows, columns=headers)
    if "Player" in df.columns:
        # Drop team in parentheses; normalize spacing
        df["Player"] = df["Player"].str.replace(r"\s+\(.*\)", "", regex=True)
    return df

@st.cache_data(ttl=6*60*60)
def fetch_all_fantasypros_weekly(scoring="ppr") -> dict:
    """Weekly projections for all positions (qb, rb, wr, te, k, dst)."""
    pos_list = ["qb", "rb", "wr", "te", "k", "dst"]
    data = {}
    for pos in pos_list:
        url = f"https://www.fantasypros.com/nfl/projections/{pos}.php?scoring={scoring}"
        try:
            data[pos] = _fetch_fp_table(url)
        except Exception as e:
            st.warning(f"FantasyPros weekly fetch failed for {pos}: {e}")
            data[pos] = pd.DataFrame()
    return data

@st.cache_data(ttl=6*60*60)
def fetch_all_fantasypros_season(scoring="ppr") -> dict:
    """
    Season-long (draft/ROS) projections for all positions.
    FantasyPros uses week=draft for season totals.
    """
    pos_list = ["qb", "rb", "wr", "te", "k", "dst"]
    data = {}
    for pos in pos_list:
        url = f"https://www.fantasypros.com/nfl/projections/{pos}.php?week=draft&scoring={scoring}"
        try:
            data[pos] = _fetch_fp_table(url)
        except Exception as e:
            st.warning(f"FantasyPros season fetch failed for {pos}: {e}")
            data[pos] = pd.DataFrame()
    return data

# ---------- Projection functions ----------
def _pos_key(player):
    return {"QB":"qb","RB":"rb","WR":"wr","TE":"te","K":"k","D/ST":"dst"}.get(getattr(player,"position","").upper())

def _match_fp_row(df: pd.DataFrame, name: str):
    if df.empty or "Player" not in df.columns:
        return None
    first = name.split()[0]
    hits = df[df["Player"].str.contains(first, case=False, na=False)]
    return hits.iloc[0] if not hits.empty else None

def get_proj_week(player, week=None):
    """Weekly projection (uses ESPN only / FP fallback / FP only based on toggle)."""
    if week is None:
        week = league.current_week

    # ESPN first (if selected)
    if proj_source in ["ESPN only", "FantasyPros fallback"]:
        try:
            if hasattr(player, "stats") and week in player.stats:
                val = player.stats[week].get("projected", 0) or 0
                if val:
                    return val
            if getattr(player, "projected_points", None):
                return player.projected_points
        except Exception:
            pass
        if proj_source == "ESPN only":
            return 0.0

    # FantasyPros weekly
    if proj_source in ["FantasyPros fallback", "FantasyPros only"]:
        key = _pos_key(player)
        if not key:
            return 0.0
        df = fp_weekly.get(key, pd.DataFrame())
        row = _match_fp_row(df, player.name)
        if row is not None:
            # FP weekly tables have 'FPTS' total
            val = row.get("FPTS", 0)
            return safe_proj(val)
    return 0.0

def get_ros_espn(player, start_week=None):
    """ESPN Rest-of-Season: sum of remaining weekly projected stats."""
    try:
        if start_week is None:
            start_week = league.current_week
        total = 0.0
        if hasattr(player, "stats"):
            for wk, vals in player.stats.items():
                if isinstance(wk, int) and wk >= start_week:
                    total += safe_proj(vals.get("projected", 0))
        return total
    except Exception:
        return 0.0

def get_ros_fp(player):
    """FantasyPros season-long projection total (FPTS)."""
    key = _pos_key(player)
    if not key:
        return 0.0
    df = fp_season.get(key, pd.DataFrame())
    row = _match_fp_row(df, player.name)
    if row is not None:
        # FP season tables also have 'FPTS' for season total
        return safe_proj(row.get("FPTS", 0))
    return 0.0

# Build a set of all rostered player names in the league (for FP fallback)
def get_all_rostered_names(league):
    names = set()
    for t in league.teams:
        for p in t.roster:
            names.add(p.name.strip())
    return names

# Tiny shim so FP fallback rows can be treated like ESPN player objects
class FPPlayer:
    def __init__(self, name, position):
        self.name = name
        self.position = position
        self.proTeam = "N/A"
        self.bye_week = "N/A"

        # properties used elsewhere remain absent (and safely ignored)

def format_injury(player):
    status = getattr(player, "injuryStatus", None)
    wk = get_proj_week(player)
    text = f"{player.name} — {wk:.1f} (This Week)"
    return f"⚠️ {text} ({status})" if status else text

def build_optimizer(roster, starting_slots):
    # Group by slot; include FLEX pool
    groups = {k: [] for k in ["QB","RB","WR","TE","D/ST","K","FLEX"]}
    for p in roster:
        pos = getattr(p, "position", "")
        if pos in groups:
            groups[pos].append(p)
    for pos in ["RB","WR","TE"]:
        groups["FLEX"].extend(groups[pos])
    # Sort by weekly projection
    for pos in groups:
        groups[pos].sort(key=lambda p: get_proj_week(p), reverse=True)
    used = set()
    lineup = {slot: [] for slot in starting_slots}
    for slot, count in starting_slots.items():
        for p in groups.get(slot, []):
            if p not in used and len(lineup[slot]) < count:
                lineup[slot].append(p)
                used.add(p)
    bench = [p for p in roster if p not in used]
    return lineup, bench

def connect_league():
    espn_s2 = st.secrets.get("espn_s2", "")
    swid = st.secrets.get("swid", "")
    league_id = int(st.secrets.get("league_id", 0) or 0)
    team_id = int(st.secrets.get("team_id", 1) or 1)
    year = int(st.secrets.get("year", 2025) or 2025)

    with st.sidebar:
        st.header("Settings")
        league_id = st.number_input("League ID", value=league_id, step=1)
        team_id = st.number_input("Team ID", value=team_id, min_value=1, step=1)
        year = st.number_input("Season", value=year, min_value=2018, step=1)
        st.caption("Using Streamlit secrets for credentials. You can override IDs above.")

    if not espn_s2 or not swid:
        st.error("Missing ESPN credentials. Add `espn_s2` and `swid` in Streamlit Secrets.")
        st.stop()

    league = League(league_id=int(league_id), year=int(year), espn_s2=espn_s2, swid=swid)
    team = league.teams[int(team_id) - 1]
    return league, team

# ---------- app ----------
st.title("🏈 Fantasy Football Weekly Starter Optimizer")

league, my_team = connect_league()
st.subheader(f"Team: **{my_team.team_name}** ({my_team.team_abbrev})")

# Projection source (weekly only)
with st.sidebar:
    st.header("Projection Source")
    proj_source = st.radio(
        "Choose weekly projections",
        ["ESPN only", "FantasyPros fallback", "FantasyPros only"],
        index=1
    )

# Fetch FantasyPros weekly + season once
fp_weekly = fetch_all_fantasypros_weekly()
fp_season = fetch_all_fantasypros_season()

# Lineup slots
with st.expander("Lineup Slots", expanded=True):
    c1, c2, c3, c4 = st.columns(4)
    QB  = c1.number_input("QB", 1, 3, 1)
    RB  = c2.number_input("RB", 1, 5, 2)
    WR  = c3.number_input("WR", 1, 5, 2)
    TE  = c4.number_input("TE", 1, 3, 1)
    c5, c6, c7 = st.columns(3)
    FLEX = c5.number_input("FLEX (RB/WR/TE)", 0, 3, 1)
    DST  = c6.number_input("D/ST", 0, 2, 1)
    K    = c7.number_input("K", 0, 2, 1)

starting_slots = {"QB": QB, "RB": RB, "WR": WR, "TE": TE, "FLEX": FLEX, "D/ST": DST, "K": K}

# Tabs (added 🛒 Free Agents)
tabs = st.tabs(["✅ Optimizer", "🔍 Matchups", "🔄 Trade Analyzer", "🛒 Free Agents", "📈 Logs", "📊 Advanced Stats"])

# ----- Optimizer -----
with tabs[0]:
    roster = my_team.roster
    lineup, bench = build_optimizer(roster, starting_slots)

    st.markdown(f"### Optimized Starting Lineup ({proj_source} weekly)")
    for slot, players in lineup.items():
        for p in players:
            st.write(
                f"**{slot}**: {p.name} — "
                f"{get_proj_week(p):.1f} (This Week) | "
                f"{get_ros_espn(p):.1f} (ROS ESPN) | "
                f"{get_ros_fp(p):.1f} (ROS FP)"
            )

    st.markdown("### Bench")
    for p in bench:
        st.write(
            f"{p.name} — {get_proj_week(p):.1f} (This Week) | "
            f"{get_ros_espn(p):.1f} (ROS ESPN) | {get_ros_fp(p):.1f} (ROS FP)"
        )

# ----- Matchups -----
with tabs[1]:
    st.markdown("### This Week's Matchups & Projections")
    try:
        st.caption(f"Week {league.current_week}")
        for m in league.box_scores():
            home, away = m.home_team, m.away_team
            st.write(f"**{home.team_name}** vs **{away.team_name}**")
            st.caption(
                f"{home.team_abbrev}: {safe_proj(getattr(home,'projected_total',0)):.1f} pts | "
                f"{away.team_abbrev}: {safe_proj(getattr(away,'projected_total',0)):.1f} pts"
            )
            st.divider()
    except Exception as e:
        st.info("Matchup data not available yet.")
        st.caption(str(e))

# ----- Trade Analyzer -----
with tabs[2]:
    st.markdown("### 🔄 Team-to-Team Trade Analyzer")
    st.caption(
        f"Weekly uses **{proj_source}**. ROS shows **both** ESPN and FantasyPros season totals."
    )

    team_options = [f"{t.team_name} ({t.team_abbrev})" for t in league.teams]
    team_lookup = {f"{t.team_name} ({t.team_abbrev})": t for t in league.teams}

    default_idx = next((i for i, label in enumerate(team_options)
                        if team_lookup[label].team_id == my_team.team_id), 0)

    colA, colB = st.columns(2)
    with colA:
        teamA_label = st.selectbox("Team A", team_options, index=default_idx)
    with colB:
        alt_idx = 1 if default_idx == 0 and len(team_options) > 1 else 0
        teamB_label = st.selectbox("Team B", team_options, index=alt_idx)

    teamA = team_lookup[teamA_label]
    teamB = team_lookup[teamB_label]

    if teamA.team_id != teamB.team_id:
        def roster_names(team):
            return [f"{p.name} — {p.position} ({get_proj_week(p):.1f} wk / {get_ros_fp(p):.1f} ROS-FP)" for p in team.roster]

        def string_to_player(name_str, team):
            pname = name_str.split(" — ")[0]
            return next((p for p in team.roster if p.name == pname), None)

        col1, col2 = st.columns(2)
        with col1:
            send_A_labels = st.multiselect(f"{teamA_label} sends", options=roster_names(teamA))
        with col2:
            send_B_labels = st.multiselect(f"{teamB_label} sends", options=roster_names(teamB))

        send_A = [string_to_player(lbl, teamA) for lbl in send_A_labels]
        send_B = [string_to_player(lbl, teamB) for lbl in send_B_labels]

        def totals(players):
            wk = sum(get_proj_week(p) for p in players)
            ros_espn = sum(get_ros_espn(p) for p in players)
            ros_fp = sum(get_ros_fp(p) for p in players)
            return wk, ros_espn, ros_fp

        A_wk, A_rosE, A_rosF = totals(send_A)
        B_wk, B_rosE, B_rosF = totals(send_B)

        st.markdown("#### 📈 Trade Summary")
        st.write(f"**This Week ({proj_source})** → {teamA.team_abbrev} net: {B_wk - A_wk:+.1f}, "
                 f"{teamB.team_abbrev} net: {A_wk - B_wk:+.1f}")
        st.write(f"**ROS ESPN** → {teamA.team_abbrev} net: {B_rosE - A_rosE:+.1f}, "
                 f"{teamB.team_abbrev} net: {A_rosE - B_rosE:+.1f}")
        st.write(f"**ROS FP** → {teamA.team_abbrev} net: {B_rosF - A_rosF:+.1f}, "
                 f"{teamB.team_abbrev} net: {A_rosF - B_rosF:+.1f}")

        def table(players, title):
            rows = [{
                "Player": p.name,
                "Pos": getattr(p, "position", ""),
                f"Weekly ({proj_source})": get_proj_week(p),
                "ROS ESPN": get_ros_espn(p),
                "ROS FP": get_ros_fp(p),
            } for p in players]
            st.markdown(f"**{title}**")
            if rows:
                st.dataframe(pd.DataFrame(rows), use_container_width=True)
            else:
                st.caption("None selected.")

        st.markdown("#### 📋 Players Involved")
        cL, cR = st.columns(2)
        with cL:
            table(send_A, f"{teamA_label} sends")
            table(send_B, f"{teamA_label} receives")
        with cR:
            table(send_B, f"{teamB_label} sends")
            table(send_A, f"{teamB_label} receives")
    else:
        st.warning("Pick two different teams to evaluate a trade.")

# ----- Free Agents -----
with tabs[3]:
    st.markdown("### 🛒 Free Agents — Add/Drop Recommendations")
    st.caption(
        "Pulls ESPN free agents when available. If ESPN returns none (offseason/draft), "
        "falls back to FantasyPros weekly projections minus all rostered players in your league."
    )

    # Tuning knobs
    fa_size = st.slider("How many free agents per position to scan", 10, 200, 60, step=10)
    weekly_threshold = st.number_input("Worth-it threshold (Δ Weekly points)", 0.0, 20.0, 2.0, step=0.5)
    ros_threshold = st.number_input("Worth-it threshold (Δ ROS points)", 0.0, 300.0, 18.0, step=1.0)

    # Build your current best lineup
    my_roster = my_team.roster
    lineup, bench = build_optimizer(my_roster, starting_slots)

    starters_by_pos = {k: lineup.get(k, []) for k in ["QB", "RB", "WR", "TE", "K", "D/ST"]}
    flex_eligible = {"RB", "WR", "TE"}

    def is_flex_eligible(pos): return pos in flex_eligible

    def lowest_bench_candidate(position):
        same_pos = [p for p in bench if getattr(p, "position", "") == position]
        pool = same_pos or ([p for p in bench if getattr(p, "position", "") in flex_eligible] if is_flex_eligible(position) else [])
        if not pool:
            return None, 0.0, 0.0, 0.0
        pool_sorted = sorted(pool, key=lambda p: (get_ros_fp(p), get_proj_week(p)))
        cand = pool_sorted[0]
        return cand, get_proj_week(cand), get_ros_espn(cand), get_ros_fp(cand)

    def would_start(fa_player):
        pos = getattr(fa_player, "position", "")
        fa_w = get_proj_week(fa_player)
        slot_starters = starters_by_pos.get(pos, [])
        if slot_starters:
            worst_starter = min(slot_starters, key=lambda p: get_proj_week(p))
            if fa_w > get_proj_week(worst_starter):
                return True
        if is_flex_eligible(pos) and lineup.get("FLEX"):
            worst_flex = min(lineup["FLEX"], key=lambda p: get_proj_week(p))
            if fa_w > get_proj_week(worst_flex):
                return True
        return False

    # Build FA table with ESPN first, FP fallback
    rows = []
    positions_to_scan = ["QB", "RB", "WR", "TE", "K", "D/ST"]
    rostered_names = get_all_rostered_names(league)

    espn_counts, fp_counts = {}, {}

    for pos in positions_to_scan:
        # Try ESPN free agents
        fa_list = []
        source_used = "ESPN"
        try:
            try_pos = pos
            try:
                fa_list = league.free_agents(position=try_pos, size=fa_size)
            except Exception:
                if pos == "D/ST":
                    fa_list = league.free_agents(position="DST", size=fa_size)
                else:
                    raise
        except Exception as e:
            st.warning(f"Could not fetch ESPN free agents for {pos}: {e}")
            fa_list = []

        espn_counts[pos] = len(fa_list)

        # FP fallback if ESPN returned zero
        if len(fa_list) == 0:
            source_used = "FantasyPros"
            key = {"QB":"qb","RB":"rb","WR":"wr","TE":"te","K":"k","D/ST":"dst"}[pos]
            df_fp = fp_weekly.get(key, pd.DataFrame())
            if not df_fp.empty and "FPTS" in df_fp.columns:
                df_fp = df_fp[~df_fp["Player"].isin(rostered_names)].copy()
                df_fp["FPTS_num"] = pd.to_numeric(df_fp["FPTS"], errors="coerce").fillna(0.0)
                df_fp.sort_values("FPTS_num", ascending=False, inplace=True)
                df_fp = df_fp.head(fa_size)
                fa_list = [FPPlayer(row["Player"], pos) for _, row in df_fp.iterrows()]
            fp_counts[pos] = len(fa_list)
        else:
            fp_counts[pos] = 0  # not used

        for fa in fa_list:
            fa_w = get_proj_week(fa)                # weekly uses chosen source toggle
            fa_re = get_ros_espn(fa)                # ROS ESPN
            fa_rf = get_ros_fp(fa)                  # ROS FP (season)

            drop_cand, drop_w, drop_re, drop_rf = lowest_bench_candidate(pos)

            if drop_cand is None:
                verdict = "No roster spot to drop"
                delta_w = delta_re = delta_rf = 0.0
                drop_name = "-"
            else:
                delta_w = fa_w - drop_w
                delta_re = fa_re - drop_re
                delta_rf = fa_rf - drop_rf
                worth = (delta_w >= weekly_threshold) or (delta_re >= ros_threshold) or (delta_rf >= ros_threshold)
                verdict = "✅ Add (starts)" if worth and would_start(fa) else ("✅ Add" if worth else "❌ Pass")
                drop_name = f"{drop_cand.name} ({getattr(drop_cand,'position','')})"

            rows.append({
                "Player": fa.name,
                "Pos": pos,
                "Source": source_used,
                f"Weekly ({proj_source})": round(fa_w, 1),
                "ROS ESPN": round(fa_re, 1),
                "ROS FP": round(fa_rf, 1),
                "Drop": drop_name,
                "Δ Weekly": round(delta_w, 1),
                "Δ ROS ESPN": round(delta_re, 1),
                "Δ ROS FP": round(delta_rf, 1),
                "Would Start?": "Yes" if would_start(fa) else "No",
                "Verdict": verdict
            })



    # Summary/debug
    with st.expander("Debug: FA source counts per position"):
        dbg = pd.DataFrame({
            "Position": positions_to_scan,
            "ESPN FAs": [espn_counts.get(p, 0) for p in positions_to_scan],
            "FP Fallback Used": [fp_counts.get(p, 0) for p in positions_to_scan],
        })
        st.dataframe(dbg, use_container_width=True)

    if rows:
        df_fa = pd.DataFrame(rows)
        df_fa["_score"] = df_fa[["Δ Weekly", "Δ ROS ESPN", "Δ ROS FP"]].max(axis=1)
        df_fa["Recommended"] = df_fa["Verdict"].str.startswith("✅")
        df_fa.sort_values(by=["Recommended", "_score"], ascending=[False, False], inplace=True)
        df_fa.drop(columns=["_score"], inplace=True)

        c1, c2, c3, c4 = st.columns(4)
        with c1:
            pos_filter = st.multiselect("Filter positions", positions_to_scan, default=positions_to_scan)
        with c2:
            only_recommended = st.checkbox("Show only recommended adds (✅)", value=False)
        with c3:
            only_starters = st.checkbox("Show only FAs who would start", value=False)
        with c4:
            source_filter = st.multiselect("Source", ["ESPN", "FantasyPros"], default=["ESPN","FantasyPros"])

        view = df_fa[df_fa["Pos"].isin(pos_filter)]
        view = view[view["Source"].isin(source_filter)]
        if only_recommended:
            view = view[view["Recommended"]]
        if only_starters:
            view = view[view["Would Start?"] == "Yes"]

        st.dataframe(view.drop(columns=["Recommended"]), use_container_width=True)
        st.caption(
            "Verdict rules: Add if Δ Weekly ≥ threshold OR Δ ROS (ESPN/FP) ≥ threshold. "
            "‘Starts’ if FA outprojects your worst starter at that position (or FLEX). "
            "Source shows where the FA list came from (ESPN or FantasyPros fallback)."
        )
    else:
        st.info("No free agents found (even with FantasyPros fallback). Try increasing the 'How many free agents' slider.")


# ----- Logs -----
with tabs[4]:
    st.markdown("### Weekly Performance Logger")
    week = getattr(league, "current_week", None)
    colA, colB, colC = st.columns(3)
    colA.metric("Projected Total (This Week)", f"{sum(get_proj_week(p) for p in my_team.roster):.1f}")
    colB.metric("Projected Total (ROS ESPN)", f"{sum(get_ros_espn(p) for p in my_team.roster):.1f}")
    colC.metric("Projected Total (ROS FP)", f"{sum(get_ros_fp(p) for p in my_team.roster):.1f}")

    log_file = "performance_log.csv"
    if st.button("📊 Log This Week"):
        row = {
            "Week": week,
            "Team": my_team.team_name,
            f"Projected (Weekly: {proj_source})": sum(get_proj_week(p) for p in my_team.roster),
            "Projected (ROS ESPN)": sum(get_ros_espn(p) for p in my_team.roster),
            "Projected (ROS FP)": sum(get_ros_fp(p) for p in my_team.roster),
            "Points": safe_proj(getattr(my_team, "points", 0)),
        }
        df = pd.DataFrame([row])
        if os.path.exists(log_file):
            old = pd.read_csv(log_file)
            df = pd.concat([old, df], ignore_index=True)
        df.to_csv(log_file, index=False)
        st.success(f"Saved to {log_file}")

    if os.path.exists(log_file):
        st.dataframe(pd.read_csv(log_file))

# ----- Advanced Stats -----
with tabs[5]:
    st.markdown(f"### 📊 Advanced Player Stats ({proj_source} weekly; ROS=ESPN & FP)")
    roster = my_team.roster
    rows = []
    for p in roster:
        rows.append({
            "Player": p.name,
            "Pos": getattr(p, "position", "N/A"),
            f"Weekly ({proj_source})": get_proj_week(p),
            "ROS ESPN": get_ros_espn(p),
            "ROS FP": get_ros_fp(p),
            "Last Week": safe_proj(getattr(p, "points", 0)),
            "Opponent": getattr(p, "pro_opponent", "N/A"),
        })
    df = pd.DataFrame(rows)

    if df.empty:
        st.info("No player data available yet.")
    else:
        st.dataframe(df)

        # Grouped bar chart: Weekly vs ROS ESPN vs ROS FP
        df_melt = df.melt(
            id_vars=["Player", "Pos"],
            value_vars=[f"Weekly ({proj_source})", "ROS ESPN", "ROS FP"],
            var_name="Type",
            value_name="Points"
        )
        chart = (
            alt.Chart(df_melt)
            .mark_bar()
            .encode(
                x=alt.X("Player:N", sort="-y"),
                y="Points:Q",
                color="Type:N",
                column="Pos:N",
                tooltip=["Player", "Pos", "Type", "Points"]
            )
            .properties(width=120, height=250)
        )
        st.altair_chart(chart, use_container_width=True)

    if st.checkbox("🔍 Show raw ESPN projection debug"):
        debug = []
        for p in roster:
            debug.append({
                "Player": p.name,
                "projected_points": getattr(p, "projected_points", None),
                "stats_proj(cur wk)": p.stats.get(league.current_week, {}).get("projected", None) if hasattr(p, "stats") else None
            })
        st.dataframe(pd.DataFrame(debug))
