import streamlit as st
from espn_api.football import League

# Load secrets
espn_s2 = st.secrets["espn_s2"]
swid = st.secrets["swid"]
league_id = st.secrets["league_id"]
team_id = st.secrets["team_id"]
year = st.secrets["year"]

# Connect to your league
league = League(league_id=league_id, year=year, espn_s2=espn_s2, swid=swid)
my_team = league.teams[team_id - 1]  # index starts at 0

st.title("🏈 Weekly Starter Optimizer")
st.subheader(f"Team: {my_team.team_name}")

# Position settings (customize for your league)
starting_slots = {
    'QB': 1,
    'RB': 2,
    'WR': 2,
    'TE': 1,
    'FLEX': 1,
    'D/ST': 1,
    'K': 1
}

# Prepare player pools
roster = my_team.roster
position_groups = {
    'QB': [],
    'RB': [],
    'WR': [],
    'TE': [],
    'FLEX': [],
    'D/ST': [],
    'K': [],
    'Bench': []
}

for player in roster:
    pos = player.position
    if pos in position_groups:
        position_groups[pos].append(player)

# FLEX = RB/WR/TE
for pos in ['RB', 'WR', 'TE']:
    position_groups['FLEX'].extend(position_groups[pos])

# Sort by projected points
for pos in position_groups:
    position_groups[pos].sort(key=lambda x: x.projected_points or 0, reverse=True)

# Build lineup
optimized_lineup = {}
used_players = set()

for pos, count in starting_slots.items():
    optimized_lineup[pos] = []
    eligible = position_groups[pos]
    selected = 0
    for player in eligible:
        if player not in used_players and selected < count:
            optimized_lineup[pos].append(player)
            used_players.add(player)
            selected += 1

# Display optimized lineup
st.markdown("### ✅ Optimized Starting Lineup")
for pos, players in optimized_lineup.items():
    for player in players:
        st.write(f"**{pos}**: {player.name} — {player.projected_points} pts")

# Show bench players
st.markdown("### 🪑 Bench Recommendations")
for player in roster:
    if player not in used_players:
        st.write(f"{player.name} — {player.projected_points} pts")

# Optional: Add matchup or injury info here
