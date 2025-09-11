import os
import pandas as pd
import streamlit as st
import altair as alt
import requests
from espn_api.football import League

st.set_page_config(page_title="Fantasy Starter Optimizer", page_icon="🏈", layout="wide")

# ---------- helpers ----------
def safe_proj(val):
    try:
        return float(val or 0)
    except Exception:
        return 0.0

@st.cache_data(ttl=6*60*60)
def fetch_fantasypros_projections(position="qb", scoring="ppr"):
    """
    Fetch FantasyPros projections for a given position.
    position: qb, rb, wr, te, k, dst
    scoring: standard, half, ppr
    """
    url = f"https://www.fantasypros.com/nfl/projections/{position}.php?scoring={scoring}"
    try:
        tables = pd.read_html(url)
        if not tables:
            return pd.DataFrame()
        df = tables[0]
        df["Player"] = df["Player"].str.replace(r"\s+\(.*\)", "", regex=True)  # remove team info
        return df
    except Exception as e:
        st.warning(f"FantasyPros fetch failed for {position}: {e}")
        return pd.DataFrame()

def get_proj(player, week=None):
    """Return projected points based on selected projection source."""
    if week is None:
        week = league.current_week
    pos_map = {"QB": "qb", "RB": "rb", "WR": "wr", "TE": "te", "K": "k", "D/ST": "dst"}
    pos = getattr(player, "position", "").upper()

    # ESPN first
    if proj_source in ["ESPN only", "FantasyPros fallback"]:
        try:
            if hasattr(player, "stats") and week in player.stats:
                val = player.stats[week].get("projected", 0) or 0
                if val:
                    return val
            if player.projected_points:
                return player.projected_points
        except Exception:
            pass
        if proj_source == "ESPN only":
            return 0

    # FantasyPros
    if proj_source in ["FantasyPros fallback", "FantasyPros only"]:
        if pos not in pos_map:
            return 0
        df = fetch_fantasypros_projections(pos_map[pos])
        if df.empty:
            return 0
        row = df[df["Player"].str.contains(player.name.split()[0], case=False)]
        if not row.empty:
            try:
                return float(row["FPTS"].values[0])
            except Exception:
                return 0
    return 0

def get_proj_ros(player, start_week=None):
    """Return Rest-of-Season projections (ESPN only)."""
    try:
        if start_week is None:
            start_week = league.current_week
        total = 0
        if hasattr(player, "stats"):
            for wk, vals in player.stats.items():
                if isinstance(wk, int) and wk >= start_week:
                    total += vals.get("projected", 0) or 0
        return total
    except Exception:
        return 0

def format_injury(player):
    status = getattr(player, "injuryStatus", None)
    pts = get_proj(player)
    text = f"{player.name} — {pts:.1f} pts (This Week)"
    return f"⚠️ {text} ({status})" if status else text

def build_optimizer(roster, starting_slots):
    groups = {k: [] for k in ["QB", "RB", "WR", "TE", "D/ST", "K", "FLEX"]}
    for p in roster:
        pos = getattr(p, "position", "")
        if pos in groups:
            groups[pos].append(p)
    for pos in ["RB", "WR", "TE"]:
        groups["FLEX"].extend(groups[pos])
    for pos in groups:
        groups[pos].sort(key=lambda p: get_proj(p), reverse=True)
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

# projection source toggle
with st.sidebar:
    st.header("Projection Source")
    proj_source = st.radio("Choose projections", ["ESPN only", "FantasyPros fallback", "FantasyPros only"], index=1)

# lineup slots
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

tabs = st.tabs(["✅ Optimizer", "🔍 Matchups", "🔄 Trade Analyzer", "📈 Logs", "📊 Advanced Stats"])

# ----- Optimizer -----
with tabs[0]:
    roster = my_team.roster
    lineup, bench = build_optimizer(roster, starting_slots)

    st.markdown(f"### Optimized Starting Lineup ({proj_source})")
    for slot, players in lineup.items():
        for p in players:
            st.write(f"**{slot}**: {p.name} — {get_proj(p):.1f} (This Week) | {get_proj_ros(p):.1f} (ROS)")

    st.markdown("### Bench")
    for p in bench:
        st.write(f"{p.name} — {get_proj(p):.1f} (This Week) | {get_proj_ros(p):.1f} (ROS)")

# ----- Matchups -----
with tabs[1]:
    st.markdown("### This Week's Matchups & Projections")
    try:
        st.caption(f"Week {league.current_week}")
        for m in league.box_scores():
            home, away = m.home_team, m.away_team
            st.write(f"**{home.team_name}** vs **{away.team_name}**")
            st.caption(f"{home.team_abbrev}: {safe_proj(getattr(home, 'projected_total', 0)):.1f} pts | "
                       f"{away.team_abbrev}: {safe_proj(getattr(away, 'projected_total', 0)):.1f} pts")
            st.divider()
    except Exception as e:
        st.info("Matchup data not available yet.")
        st.caption(str(e))

# ----- Trade Analyzer -----
with tabs[2]:
    st.markdown("### 🔄 Team-to-Team Trade Analyzer")
    st.caption(f"Evaluates trades using {proj_source} for This Week and ESPN for ROS.")

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
            return [f"{p.name} — {p.position} ({get_proj(p):.1f} / {get_proj_ros(p):.1f})" for p in team.roster]

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

        def total_proj(players):
            return sum(get_proj(p) for p in players), sum(get_proj_ros(p) for p in players)

        total_A_wk, total_A_ros = total_proj(send_A)
        total_B_wk, total_B_ros = total_proj(send_B)

        teamA_gain_wk = total_B_wk - total_A_wk
        teamB_gain_wk = total_A_wk - total_B_wk
        teamA_gain_ros = total_B_ros - total_A_ros
        teamB_gain_ros = total_A_ros - total_B_ros

        st.markdown("#### 📈 Trade Summary")
        st.write(f"**This Week ({proj_source})** → {teamA.team_abbrev} net: {teamA_gain_wk:+.1f}, "
                 f"{teamB.team_abbrev} net: {teamB_gain_wk:+.1f}")
        st.write(f"**ROS (ESPN only)** → {teamA.team_abbrev} net: {teamA_gain_ros:+.1f}, "
                 f"{teamB.team_abbrev} net: {teamB_gain_ros:+.1f}")

        def to_df(players, title):
            rows = [{
                "Player": p.name,
                "Pos": getattr(p, "position", ""),
                f"Proj (This Week: {proj_source})": get_proj(p),
                "Proj (ROS: ESPN)": get_proj_ros(p),
            } for p in players]
            st.markdown(f"**{title}**")
            if rows:
                st.dataframe(pd.DataFrame(rows), use_container_width=True)
            else:
                st.caption("None selected.")

        st.markdown("#### 📋 Players Involved")
        cL, cR = st.columns(2)
        with cL:
            to_df(send_A, f"{teamA_label} sends")
            to_df(send_B, f"{teamA_label} receives")
        with cR:
            to_df(send_B, f"{teamB_label} sends")
            to_df(send_A, f"{teamB_label} receives")
    else:
        st.warning("Pick two different teams to evaluate a trade.")

# ----- Logs -----
with tabs[3]:
    st.markdown("### Weekly Performance Logger")
    week = getattr(league, "current_week", None)
    colA, colB = st.columns(2)
    colA.metric("Projected Total (This Week)", f"{sum(get_proj(p) for p in my_team.roster):.1f}")
    colB.metric("Projected Total (ROS)", f"{sum(get_proj_ros(p) for p in my_team.roster):.1f}")

    log_file = "performance_log.csv"
    if st.button("📊 Log This Week"):
        row = {
            "Week": week,
            "Team": my_team.team_name,
            f"Projected (This Week: {proj_source})": sum(get_proj(p) for p in my_team.roster),
            "Projected (ROS: ESPN)": sum(get_proj_ros(p) for p in my_team.roster),
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
with tabs[4]:
    st.markdown(f"### 📊 Advanced Player Stats ({proj_source})")
    roster = my_team.roster
    rows = []
    for p in roster:
        rows.append({
            "Player": p.name,
            "Pos": getattr(p, "position", "N/A"),
            f"Projection (This Week: {proj_source})": get_proj(p),
            "Projection (ROS: ESPN)": get_proj_ros(p),
            "Last Week": safe_proj(getattr(p, "points", 0)),
            "Opponent": getattr(p, "pro_opponent", "N/A"),
        })
    df = pd.DataFrame(rows)

    if df.empty:
        st.info("No player data available yet.")
    else:
        st.dataframe(df)

        # Grouped bar chart
        df_melt = df.melt(
            id_vars=["Player", "Pos"],
            value_vars=[f"Projection (This Week: {proj_source})", "Projection (ROS: ESPN)"],
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

    if st.checkbox("🔍 Show raw projection debug"):
        debug = []
        for p in roster:
            debug.append({
                "Player": p.name,
                "projected_points": getattr(p, "projected_points", None),
                "stats_proj": p.stats.get(league.current_week, {}).get("projected", None) if hasattr(p, "stats") else None
            })
        st.dataframe(pd.DataFrame(debug))
