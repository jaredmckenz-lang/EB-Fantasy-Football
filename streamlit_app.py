import os
import pandas as pd
import streamlit as st
import altair as alt
from espn_api.football import League

st.set_page_config(page_title="Fantasy Starter Optimizer", page_icon="🏈", layout="wide")

# ---------- helpers ----------
def safe_proj(val):
    try:
        return float(val or 0)
    except Exception:
        return 0.0

def format_injury(player):
    status = getattr(player, "injuryStatus", None)
    pts = safe_proj(getattr(player, "projected_points", 0))
    text = f"{player.name} — {pts:.1f} pts"
    return f"⚠️ {text} ({status})" if status else text

def build_optimizer(roster, starting_slots):
    groups = {k: [] for k in ["QB", "RB", "WR", "TE", "D/ST", "K", "FLEX"]}
    for p in roster:
        pos = getattr(p, "position", "")
        if pos in groups:
            groups[pos].append(p)
    # FLEX pool: RB/WR/TE
    for pos in ["RB", "WR", "TE"]:
        groups["FLEX"].extend(groups[pos])
    # sort by projection
    for pos in groups:
        groups[pos].sort(key=lambda p: safe_proj(getattr(p, "projected_points", 0)), reverse=True)
    # choose starters (no duplicates)
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
    team = league.teams[int(team_id) - 1]  # ESPN teamId is 1-based
    return league, team

# ---------- app ----------
st.title("🏈 Fantasy Football Weekly Starter Optimizer")

league, my_team = connect_league()
st.subheader(f"Team: **{my_team.team_name}** ({my_team.team_abbrev})")

# lineup slots (edit to match your league)
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

# Define ALL tabs ONCE
tabs = st.tabs([
    "✅ Optimizer",
    "🔍 Matchups",
    "🔄 Trade Analyzer (Beta)",
    "📈 Logs",
    "📊 Advanced Stats"
])

# ----- Optimizer -----
with tabs[0]:
    roster = my_team.roster
    lineup, bench = build_optimizer(roster, starting_slots)

    st.markdown("### Optimized Starting Lineup")
    for slot, players in lineup.items():
        for p in players:
            st.write(f"**{slot}**: {format_injury(p)}")

    st.markdown("### Bench")
    for p in bench:
        st.write(format_injury(p))

# ----- Matchups -----
with tabs[1]:
    st.markdown("### This Week's Matchups & Projections")
    try:
        st.caption(f"Week {league.current_week}")
        for m in league.box_scores():
            home, away = m.home_team, m.away_team
            hp = safe_proj(getattr(home, "projected_total", 0))
            ap = safe_proj(getattr(away, "projected_total", 0))
            st.write(f"**{home.team_name}** vs **{away.team_name}**")
            st.progress(min(int(hp * 2), 100), text=f"{home.team_abbrev}: {hp:.1f} pts")
            st.progress(min(int(ap * 2), 100), text=f"{away.team_abbrev}: {ap:.1f} pts")
            st.divider()
    except Exception as e:
        st.info("Matchup data not available yet.")
        st.caption(str(e))

# ----- Trade Analyzer (Beta) -----
# ----- Trade Analyzer (Team-to-Team) -----
with tabs[2]:
    st.markdown("### 🔄 Team-to-Team Trade Analyzer")
    st.caption("Select two teams and the players each side would SEND away. Projections are this week’s.")

    # Map teams for selection
    team_options = [f"{t.team_name} ({t.team_abbrev})" for t in league.teams]
    team_lookup = {f"{t.team_name} ({t.team_abbrev})": t for t in league.teams}

    # Preselect Team A as your team
    default_idx = next((i for i, label in enumerate(team_options)
                        if team_lookup[label].team_id == my_team.team_id), 0)

    colA, colB = st.columns(2)
    with colA:
        teamA_label = st.selectbox("Team A (your perspective)", team_options, index=default_idx)
    with colB:
        # choose a different default for Team B
        alt_idx = 1 if default_idx == 0 and len(team_options) > 1 else 0
        teamB_label = st.selectbox("Team B", team_options, index=alt_idx)

    teamA = team_lookup[teamA_label]
    teamB = team_lookup[teamB_label]

    if teamA.team_id == teamB.team_id:
        st.warning("Pick two different teams to evaluate a trade.")
    else:
        # Build multiselects from each roster
        def roster_names(team):
            return [f"{p.name} — {p.position} ({(safe_proj(getattr(p,'projected_points',0))):.1f})" for p in team.roster]

        def string_to_player(name_str, team):
            # name_str format: "Player — POS (x.x)"
            pname = name_str.split(" — ")[0]
            return next((p for p in team.roster if p.name == pname), None)

        col1, col2 = st.columns(2)
        with col1:
            send_A_labels = st.multiselect(
                f"{teamA_label} sends",
                options=roster_names(teamA)
            )
        with col2:
            send_B_labels = st.multiselect(
                f"{teamB_label} sends",
                options=roster_names(teamB)
            )

        send_A = [string_to_player(lbl, teamA) for lbl in send_A_labels]
        send_B = [string_to_player(lbl, teamB) for lbl in send_B_labels]

        # Totals
        def total_proj(players):
            return sum(safe_proj(getattr(p, "projected_points", 0)) for p in players)

        total_A_out = total_proj(send_A)
        total_B_out = total_proj(send_B)

        # Gains = players received minus players sent
        teamA_gain = total_B_out - total_A_out
        teamB_gain = total_A_out - total_B_out

        st.markdown("#### 📈 Summary (this week projections)")
        m1, m2, m3 = st.columns(3)
        m1.metric(f"{teamA.team_abbrev} gets", f"{total_B_out:.1f} pts")
        m2.metric(f"{teamA.team_abbrev} gives", f"{total_A_out:.1f} pts")
        m3.metric(f"Net for {teamA.team_abbrev}", f"{teamA_gain:+.1f} pts")

        n1, n2, n3 = st.columns(3)
        n1.metric(f"{teamB.team_abbrev} gets", f"{total_A_out:.1f} pts")
        n2.metric(f"{teamB.team_abbrev} gives", f"{total_B_out:.1f} pts")
        n3.metric(f"Net for {teamB.team_abbrev}", f"{teamB_gain:+.1f} pts")

        # Tables of players involved
        def to_df(players, title):
            rows = [{
                "Player": p.name,
                "Pos": getattr(p, "position", ""),
                "Proj (wk)": safe_proj(getattr(p, "projected_points", 0))
            } for p in players]
            st.markdown(f"**{title}**")
            if rows:
                st.dataframe(pd.DataFrame(rows).sort_values("Proj (wk)", ascending=False), use_container_width=True)
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

        # Perspective toggle (optional)
        view_as = st.radio("Perspective", [teamA.team_abbrev, teamB.team_abbrev], horizontal=True)
        if view_as == teamA.team_abbrev:
            if teamA_gain > 0:
                st.success(f"{teamA_label} gains **{teamA_gain:.1f}** projected points.")
            elif teamA_gain < 0:
                st.warning(f"{teamA_label} loses **{-teamA_gain:.1f}** projected points.")
            else:
                st.info("Even trade by this week's projections.")
        else:
            if teamB_gain > 0:
                st.success(f"{teamB_label} gains **{teamB_gain:.1f}** projected points.")
            elif teamB_gain < 0:
                st.warning(f"{teamB_label} loses **{-teamB_gain:.1f}** projected points.")
            else:
                st.info("Even trade by this week's projections.")

        st.caption("Note: uses ESPN weekly projections available via espn_api. Consider schedule, injuries, and ROS value before accepting.")

# ----- Logs -----
with tabs[3]:
    st.markdown("### Weekly Performance Logger")
    week = getattr(league, "current_week", None)
    colA, colB = st.columns(2)
    colA.metric("Projected Total", f"{safe_proj(getattr(my_team, 'projected_total', 0)):.1f}")
    colB.metric("Points Scored", f"{safe_proj(getattr(my_team, 'points', 0)):.1f}")

    log_file = "performance_log.csv"
    if st.button("📊 Log This Week"):
        row = {
            "Week": week,
            "Team": my_team.team_name,
            "Projected": safe_proj(getattr(my_team, "projected_total", 0)),
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
    st.markdown("### 📊 Advanced Player Stats")
    roster = my_team.roster
    rows = []
    for p in roster:
        rows.append({
            "Player": p.name,
            "Pos": getattr(p, "position", "N/A"),
            "Projection": safe_proj(getattr(p, "projected_points", 0)),
            "Last Week": safe_proj(getattr(p, "points", 0)),
            "Opponent": getattr(p, "pro_opponent", "N/A"),
        })
    df = pd.DataFrame(rows)
    if df.empty:
        st.info("No player data available yet.")
    else:
        st.dataframe(df)
        chart = (
            alt.Chart(df)
            .mark_circle(size=100)
            .encode(
                x="Last Week:Q",
                y="Projection:Q",
                color="Pos:N",
                tooltip=["Player", "Pos", "Opponent", "Projection", "Last Week"]
            )
            .interactive()
        )
        st.altair_chart(chart, use_container_width=True)
