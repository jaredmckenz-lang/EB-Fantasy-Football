import os
import pandas as pd
import streamlit as st
from espn_api.football import League

st.set_page_config(page_title="Fantasy Starter Optimizer", page_icon="🏈", layout="wide")

# -------- Helpers --------
def safe_proj(x):
    try:
        return float(x or 0)
    except Exception:
        return 0.0

def format_injury(player):
    status = getattr(player, "injuryStatus", None)
    base = f"{player.name} — {safe_proj(getattr(player, 'projected_points', 0)):.1f} pts"
    return f"⚠️ {base} ({status})" if status else base

def build_optimizer(roster, starting_slots):
    # bucket players
    groups = {k: [] for k in ["QB","RB","WR","TE","D/ST","K","FLEX"]}
    for p in roster:
        pos = getattr(p, "position", "")
        if pos in groups:
            groups[pos].append(p)
    # FLEX eligible
    for pos in ["RB","WR","TE"]:
        groups["FLEX"].extend(groups[pos])
    # sort by projections
    for pos in groups:
        groups[pos].sort(key=lambda p: safe_proj(getattr(p, "projected_points", 0)), reverse=True)
    # choose starters greedily, avoiding duplicates
    used = set()
    lineup = {slot: [] for slot in starting_slots}
    for slot, count in starting_slots.items():
        chosen = 0
        for p in groups[slot]:
            if p not in used:
                lineup[slot].append(p)
                used.add(p)
                chosen += 1
                if chosen == count:
                    break
    bench = [p for p in roster if p not in used]
    return lineup, bench

def connect_league():
    # Try secrets first, allow sidebar overrides
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
        st.caption("You can override secrets here (useful for testing).")

    if not espn_s2 or not swid:
        st.error("Missing ESPN credentials. Add `espn_s2` and `swid` in Streamlit Secrets.")
        st.stop()

    try:
        league = League(league_id=int(league_id), year=int(year), espn_s2=espn_s2, swid=swid)
        team = league.teams[int(team_id) - 1]  # teamId is 1-based
        return league, team
    except Exception as e:
        st.exception(e)
        st.stop()

# -------- App --------
st.title("🏈 Fantasy Football Weekly Starter Optimizer")

league, my_team = connect_league()
st.subheader(f"Team: **{my_team.team_name}** ({my_team.team_abbrev})")

# Editable lineup slots (match to your league)
with st.expander("Lineup Slots", expanded=True):
    col1, col2, col3, col4 = st.columns(4)
    QB  = col1.number_input("QB", 1, 3, 1)
    RB  = col2.number_input("RB", 1, 5, 2)
    WR  = col3.number_input("WR", 1, 5, 2)
    TE  = col4.number_input("TE", 1, 3, 1)
    col5, col6, col7 = st.columns(3)
    FLEX = col5.number_input("FLEX (RB/WR/TE)", 0, 3, 1)
    DST  = col6.number_input("D/ST", 0, 2, 1)
    K    = col7.number_input("K", 0, 2, 1)

starting_slots = {"QB": QB, "RB": RB, "WR": WR, "TE": TE, "FLEX": FLEX, "D/ST": DST, "K": K}

tabs = st.tabs(["✅ Optimizer", "🔍 Matchups", "🔄 Trade Analyzer (Beta)", "📈 Logs"])

# ------- Optimizer -------
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

# ------- Matchups -------
with tabs[1]:
    st.markdown("### This Week's Matchups & Projections")
    try:
        week = league.current_week
        st.caption(f"Week {week}")
    except Exception:
        pass

    try:
        bs = league.box_scores()
        for m in bs:
            home, away = m.home_team, m.away_team
            hp = getattr(home, "projected_total", 0) or 0
            ap = getattr(away, "projected_total", 0) or 0
            st.write(f"**{home.team_name}** vs **{away.team_name}**")
            st.progress(min(int(hp * 2), 100), text=f"{home.team_abbrev}: {hp:.1f} pts")
            st.progress(min(int(ap * 2), 100), text=f"{away.team_abbrev}: {ap:.1f} pts")
            st.divider()
    except Exception as e:
        st.info("Matchup data not available yet.")
        st.caption(str(e))

# ------- Trade Analyzer (Beta) -------
with tabs[2]:
    st.markdown("Compare your player to another player (name match).")
    my_names = [p.name for p in roster]
    your_pick = st.selectbox("Your Player", my_names, index=0 if my_names else None)
    other_name = st.text_input("Other Player (type full name)")

    if your_pick and other_name:
        your_player = next((p for p in roster if p.name == your_pick), None)
        # Look across free agents by that position for a quick projection comparison
        try:
            fa = league.free_agents(position=your_player.position, size=100)
        except Exception:
            fa = []
        target = next((p for p in fa if p.name.lower() == other_name.lower()), None)

        yp = safe_proj(getattr(your_player, "projected_points", 0))
        if target:
            tp = safe_proj(getattr(target, "projected_points", 0))
            st.write(f"**{your_player.name}**: {yp:.1f} pts vs **{target.name}**: {tp:.1f} pts")
            diff = tp - yp
            if diff > 0:
                st.success(f"👍 +{diff:.1f} projected pts (favorable).")
            elif diff < 0:
                st.warning(f"⚠️ {diff:.1f} projected pts (unfavorable).")
            else:
                st.info("Even trade by projections.")
        else:
            st.warning("Couldn’t find that other player among free agents for comparison. Try exact spelling.")

# ------- Logs -------
with tabs[3]:
    st.markdown("### Weekly Performance Logger")
    log_file = "performance_log.csv"
    week = getattr(league, "current_week", None)
    colA, colB = st.columns(2)
    colA.metric("Projected Total", f"{safe_proj(getattr(my_team, 'projected_total', 0)):.1f}")
    colB.metric("Points Scored (if played)", f"{safe_proj(getattr(my_team, 'points', 0)):.1f}")

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
