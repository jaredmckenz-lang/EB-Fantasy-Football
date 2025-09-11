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
            st.
