import os
import pandas as pd
import streamlit as st
import altair as altair
from types import ModuleType
if not isinstance(alt, ModuleType) or not hasattr(alt, "Chart"):
    import altair as alt
import requests
from bs4 import BeautifulSoup
from espn_api.football import League

st.set_page_config(page_title="Fantasy Starter Optimizer", page_icon="🏈", layout="wide")

# =========================================
# Utilities
# =========================================
def safe_float(x, default=0.0) -> float:
    try:
        return float(x)
    except Exception:
        return default

# --------------- FantasyPros Scrape (no lxml) ---------------
@st.cache_data(ttl=6 * 60 * 60)
def _fp_fetch_table(url: str) -> pd.DataFrame:
    """Scrape FantasyPros projection table with id='data'. Parse Player, team, bye."""
    import re

    r = requests.get(url, headers={"User-Agent": "Mozilla/5.0"})
    r.raise_for_status()

    soup = BeautifulSoup(r.text, "html.parser")
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

    # Extract team/bye from Player column when possible
    if "Player" in df.columns:
        df["_raw"] = df["Player"]

        def extract_team(s):
            m = re.search(r"\(([^)]+)\)", s or "")
            return m.group(1) if m else "N/A"

        df["FP_Team"] = df["_raw"].apply(extract_team)
        df["Player"] = df["_raw"].str.replace(r"\s+\(.*\)", "", regex=True)

    if "Bye" in df.columns:
        df["FP_Bye"] = df["Bye"]
    else:
        df["FP_Bye"] = "N/A"
    return df


@st.cache_data(ttl=6 * 60 * 60)
def fp_weekly_all(scoring="ppr") -> dict:
    pos = ["qb", "rb", "wr", "te", "k", "dst"]
    out = {}
    for p in pos:
        url = f"https://www.fantasypros.com/nfl/projections/{p}.php?scoring={scoring}"
        try:
            out[p] = _fp_fetch_table(url)
        except Exception as e:
            st.warning(f"FantasyPros weekly fetch failed for {p}: {e}")
            out[p] = pd.DataFrame()
    return out


@st.cache_data(ttl=6 * 60 * 60)
def fp_season_all(scoring="ppr") -> dict:
    pos = ["qb", "rb", "wr", "te", "k", "dst"]
    out = {}
    for p in pos:
        url = f"https://www.fantasypros.com/nfl/projections/{p}.php?week=draft&scoring={scoring}"
        try:
            out[p] = _fp_fetch_table(url)
        except Exception as e:
            st.warning(f"FantasyPros season fetch failed for {p}: {e}")
            out[p] = pd.DataFrame()
    return out


def _pos_key(player) -> str:
    mp = {"QB": "qb", "RB": "rb", "WR": "wr", "TE": "te", "K": "k", "D/ST": "dst"}
    return mp.get(getattr(player, "position", "").upper(), "")


def _fp_match_row(df: pd.DataFrame, name: str):
    if df.empty or "Player" not in df.columns:
        return None
    # Loose match on first name segment to be resilient to variants
    first = name.split()[0]
    hit = df[df["Player"].str.contains(first, case=False, na=False)]
    return hit.iloc[0] if not hit.empty else None


# --------------- ESPN / FP projection helpers ---------------
def get_proj_week(player, week=None) -> float:
    """Weekly projection based on sidebar source: ESPN only, FP fallback, FP only."""
    if week is None:
        week = league.current_week

    # ESPN first (if toggled)
    if proj_source in ["ESPN only", "FantasyPros fallback"]:
        try:
            if hasattr(player, "stats") and week in player.stats:
                v = player.stats[week].get("projected", 0) or 0
                if v:
                    return safe_float(v)
            if getattr(player, "projected_points", None):
                return safe_float(player.projected_points)
        except Exception:
            pass
        if proj_source == "ESPN only":
            return 0.0

    # FantasyPros weekly
    if proj_source in ["FantasyPros fallback", "FantasyPros only"]:
        key = _pos_key(player)
        if key:
            df = FP_WEEKLY.get(key, pd.DataFrame())
            row = _fp_match_row(df, player.name)
            if row is not None:
                return safe_float(row.get("FPTS", 0))
    return 0.0


def get_ros_espn(player, start_week=None) -> float:
    """Sum ESPN weekly projected stats from current week forward."""
    try:
        if start_week is None:
            start_week = league.current_week
        total = 0.0
        if hasattr(player, "stats"):
            for wk, vals in player.stats.items():
                if isinstance(wk, int) and wk >= start_week:
                    total += safe_float(vals.get("projected", 0))
        return total
    except Exception:
        return 0.0


def get_ros_fp(player) -> float:
    """FantasyPros season total (FPTS)."""
    key = _pos_key(player)
    if not key:
        return 0.0
    df = FP_SEASON.get(key, pd.DataFrame())
    row = _fp_match_row(df, player.name)
    if row is not None:
        return safe_float(row.get("FPTS", 0))
    return 0.0


def ros_estimate(player) -> float:
    """
    Best-effort ROS:
      1) ESPN ROS (weekly-sum) if present
      2) FP season projection (FPTS)
      3) fallback = weeks_remaining * this-week projection
    """
    espn = get_ros_espn(player)
    if espn > 0:
        return espn
    fp = get_ros_fp(player)
    if fp > 0:
        return fp
    wk = get_proj_week(player)
    weeks_rem = max(0, 18 - int(getattr(league, "current_week", 1)))
    return weeks_rem * wk


# --------------- Optimizer ---------------
def build_optimizer(roster, starting_slots: dict):
    groups = {k: [] for k in ["QB", "RB", "WR", "TE", "D/ST", "K", "FLEX"]}
    for p in roster:
        pos = getattr(p, "position", "")
        if pos in groups:
            groups[pos].append(p)
    for pos in ["RB", "WR", "TE"]:
        groups["FLEX"].extend(groups[pos])

    for pos in groups:
        groups[pos].sort(key=lambda pl: get_proj_week(pl), reverse=True)

    used = set()
    lineup = {slot: [] for slot in starting_slots}
    for slot, cnt in starting_slots.items():
        for p in groups.get(slot, []):
            if p not in used and len(lineup[slot]) < cnt:
                lineup[slot].append(p)
                used.add(p)
    bench = [p for p in roster if p not in used]
    return lineup, bench


# --------------- League connect ---------------
def connect_league():
    # from secrets
    espn_s2 = st.secrets.get("espn_s2", "")
    swid = st.secrets.get("swid", "")
    league_id = int(str(st.secrets.get("league_id", 0)))
    team_id = int(str(st.secrets.get("team_id", 1)))
    year = int(str(st.secrets.get("year", 2025)))

    with st.sidebar:
        st.header("Settings")
        league_id = st.number_input("League ID", value=league_id, step=1)
        team_id = st.number_input("Team ID", value=team_id, min_value=1, step=1)
        year = st.number_input("Season", value=year, min_value=2018, step=1)
        st.caption("Using Streamlit secrets by default; you can override values above.")

    if not espn_s2 or not swid:
        st.error("Missing ESPN cookies. Set `espn_s2` and `swid` in .streamlit/secrets.toml")
        st.stop()

    l = League(league_id=int(league_id), year=int(year), espn_s2=espn_s2, swid=swid)
    t = l.teams[int(team_id) - 1]
    return l, t


def get_all_rostered_names(lg: League) -> set:
    names = set()
    for tm in lg.teams:
        for p in tm.roster:
            names.add(p.name.strip())
    return names


# =========================================
# App
# =========================================
st.title("🏈 Fantasy Football Weekly Starter Optimizer")

league, my_team = connect_league()
st.subheader(f"Team: **{my_team.team_name}** ({my_team.team_abbrev})")

# projection source (weekly)
with st.sidebar:
    st.header("Projection Source")
    proj_source = st.radio(
        "Choose weekly projections",
        ["ESPN only", "FantasyPros fallback", "FantasyPros only"],
        index=1
    )

# fetch FP data
FP_WEEKLY = fp_weekly_all()
FP_SEASON = fp_season_all()

# lineup config
with st.expander("Lineup Slots", expanded=True):
    c1, c2, c3, c4 = st.columns(4)
    QB = c1.number_input("QB", 1, 3, 1)
    RB = c2.number_input("RB", 1, 5, 2)
    WR = c3.number_input("WR", 1, 5, 2)
    TE = c4.number_input("TE", 1, 3, 1)
    c5, c6, c7 = st.columns(3)
    FLEX = c5.number_input("FLEX (RB/WR/TE)", 0, 3, 1)
    DST = c6.number_input("D/ST", 0, 2, 1)
    K = c7.number_input("K", 0, 2, 1)

starting_slots = {"QB": QB, "RB": RB, "WR": WR, "TE": TE, "FLEX": FLEX, "D/ST": DST, "K": K}

tabs = st.tabs([
    "✅ Optimizer",
    "🔍 Matchups",
    "🔄 Trade Analyzer",
    "🛒 Free Agents",
    "📈 Logs",
    "📊 Advanced Stats",  # <— Tab 6
    "🧾 Waiver Tracker",
    "🧪 What-If Lineup",
])



# =========================================
# Tab 0: Optimizer
# =========================================
with tabs[0]:
    roster = my_team.roster
    lineup, bench = build_optimizer(roster, starting_slots)

    st.markdown(f"### Optimized Starting Lineup ({proj_source} weekly)")
    for slot, players in lineup.items():
        for p in players:
            st.write(
                f"**{slot}**: {p.name} — {get_proj_week(p):.1f} wk | "
                f"{ros_estimate(p):.1f} ROS"
            )

    st.markdown("### Bench")
    for p in bench:
        st.write(f"{p.name} — {get_proj_week(p):.1f} wk | {ros_estimate(p):.1f} ROS")

    st.markdown("#### 🧠 How this lineup was chosen")
    st.caption(
        "We sort each position (and a combined FLEX pool) by **This Week** projection, "
        "then fill required slots top-down, avoiding duplicates. FLEX compares RB/WR/TE together."
    )

# =========================================
# Tab 1: Matchups
# =========================================
with tabs[1]:
    st.markdown("### This Week's Matchups & Projections")
    try:
        st.caption(f"Week {league.current_week}")

        games = []
        my_game = None
        for bs in league.box_scores():
            home, away = bs.home_team, bs.away_team
            hp = safe_float(getattr(home, "projected_total", 0))
            ap = safe_float(getattr(away, "projected_total", 0))
            games.append((home, hp, away, ap))
            if my_team.team_id in [home.team_id, away.team_id]:
                my_game = (home, hp, away, ap)

        if games:
            avg_proj = sum(h + a for _, h, _, a in games) / (2 * len(games))
            st.markdown(f"**League avg projected points (per team):** {avg_proj:.1f}")
            st.divider()

        for home, hp, away, ap in games:
            st.write(f"**{home.team_name}** ({home.team_abbrev}) vs **{away.team_name}** ({away.team_abbrev})")
            st.progress(min(int(hp * 2), 100), text=f"{home.team_abbrev}: {hp:.1f} pts")
            st.progress(min(int(ap * 2), 100), text=f"{away.team_abbrev}: {ap:.1f} pts")
            margin = hp - ap
            fav = home.team_abbrev if margin >= 0 else away.team_abbrev
            st.caption(f"Projected margin: {fav} {abs(margin):.1f}")
            st.divider()

        if my_game:
            home, hp, away, ap = my_game
            margin = hp - ap if home.team_id == my_team.team_id else ap - hp
            tilt = "favored" if margin >= 0 else "underdog"
            st.info(
                f"**Your game:** {home.team_abbrev} vs {away.team_abbrev} — "
                f"You are **{tilt}** by {abs(margin):.1f}."
            )

    except Exception as e:
        st.info("Matchup data not available yet.")
        st.caption(str(e))

# =========================================
# Tab 2: Trade Analyzer (lean version)
# =========================================
with tabs[2]:
    st.markdown("### 🔄 Team-to-Team Trade Analyzer")
    st.caption("Weekly uses your chosen source. ROS uses a best-effort estimate (ESPN/FP/fallback).")

    team_opts = [f"{t.team_name} ({t.team_abbrev})" for t in league.teams]
    look = {f"{t.team_name} ({t.team_abbrev})": t for t in league.teams}
    default_idx = next((i for i, lb in enumerate(team_opts) if look[lb].team_id == my_team.team_id), 0)

    cA, cB = st.columns(2)
    with cA:
        teamA_lb = st.selectbox("Team A", team_opts, index=default_idx, key="ta_a")
    with cB:
        alt_idx = 1 if default_idx == 0 and len(team_opts) > 1 else 0
        teamB_lb = st.selectbox("Team B", team_opts, index=alt_idx, key="ta_b")

    teamA = look[teamA_lb]
    teamB = look[teamB_lb]

    if teamA.team_id == teamB.team_id:
        st.warning("Pick two different teams to evaluate a trade.")
    else:
        def roster_labels(team):
            return [f"{p.name} — {p.position} ({get_proj_week(p):.1f} wk / {ros_estimate(p):.1f} ROS)" for p in team.roster]

        def str_to_player(lbl, team):
            nm = lbl.split(" — ")[0]
            return next((p for p in team.roster if p.name == nm), None)

        col1, col2 = st.columns(2)
        with col1:
            sendA_lb = st.multiselect(f"{teamA_lb} sends", options=roster_labels(teamA), key="ta_send_a")
        with col2:
            sendB_lb = st.multiselect(f"{teamB_lb} sends", options=roster_labels(teamB), key="ta_send_b")

        sendA = [str_to_player(x, teamA) for x in sendA_lb]
        sendB = [str_to_player(x, teamB) for x in sendB_lb]

        def totals(lst):
            wk = sum(get_proj_week(p) for p in lst)
            rs = sum(ros_estimate(p) for p in lst)
            return wk, rs

        A_wk, A_ros = totals(sendA)
        B_wk, B_ros = totals(sendB)

        st.markdown("#### 📈 Trade Summary")
        st.write(f"**This Week** → {teamA.team_abbrev} net: {B_wk - A_wk:+.1f}, {teamB.team_abbrev} net: {A_wk - B_wk:+.1f}")
        st.write(f"**ROS (est.)** → {teamA.team_abbrev} net: {B_ros - A_ros:+.1f}, {teamB.team_abbrev} net: {A_ros - B_ros:+.1f}")

# =========================================
# Tab 3: Free Agents (with full ESPN pool + ROS est.)
# =========================================
with tabs[3]:
    st.markdown("### 🛒 Free Agents — Add/Drop Recommendations")
    st.caption("Pulls a **large** ESPN FA pool. ROS shows an estimate (ESPN/FP/fallback).")

    fa_size = st.slider("Rows to show (per table)", 10, 200, 80, step=10)
    weekly_threshold = st.number_input("Worth-it threshold (Δ Weekly)", 0.0, 20.0, 2.0, step=0.5)
    ros_threshold = st.number_input("Worth-it threshold (Δ ROS est.)", 0.0, 300.0, 18.0, step=1.0)

    FA_FETCH_MAX = 500
    positions = ["QB", "RB", "WR", "TE", "K", "D/ST"]
    rostered_names = get_all_rostered_names(league)

    lineup, bench = build_optimizer(my_team.roster, starting_slots)
    starters_by_pos = {k: lineup.get(k, []) for k in ["QB", "RB", "WR", "TE", "K", "D/ST"]}

    def _would_start(pl):
        pos = getattr(pl, "position", "")
        val = get_proj_week(pl)
        slot = starters_by_pos.get(pos, [])
        if slot:
            worst = min(slot, key=lambda x: get_proj_week(x))
            if val > get_proj_week(worst):
                return True
        if pos in ["RB", "WR", "TE"] and lineup.get("FLEX"):
            worst_flex = min(lineup["FLEX"], key=lambda x: get_proj_week(x))
            return val > get_proj_week(worst_flex)
        return False

    def _best_drop(pos):
        # among bench at same pos else flex pool
        pool = [p for p in bench if getattr(p, "position", "") == pos]
        if not pool and pos in ["RB", "WR", "TE"]:
            pool = [p for p in bench if getattr(p, "position", "") in ["RB", "WR", "TE"]]
        if not pool:
            return None
        return sorted(pool, key=lambda p: (ros_estimate(p), get_proj_week(p)))[0]

    rows = []
    for pos in positions:
        source_used = "ESPN"
        # BIG ESPN pull
        try:
            try:
                fas = league.free_agents(position=pos, size=FA_FETCH_MAX)
            except Exception:
                fas = league.free_agents(position="DST", size=FA_FETCH_MAX) if pos == "D/ST" else []
        except Exception:
            fas = []

        # FP fallback if truly nothing
        if not fas:
            key = {"QB": "qb", "RB": "rb", "WR": "wr", "TE": "te", "K": "k", "D/ST": "dst"}[pos]
            df = FP_WEEKLY.get(key, pd.DataFrame())
            if not df.empty and "FPTS" in df.columns:
                df = df[~df["Player"].isin(rostered_names)].copy()
                df["FPTS_num"] = pd.to_numeric(df["FPTS"], errors="coerce").fillna(0.0)
                df.sort_values("FPTS_num", ascending=False, inplace=True)
                df = df.head(FA_FETCH_MAX)
                # create light fake player objects
                class FPPlayer:
                    def __init__(self, name):
                        self.name = name
                        self.position = pos
                        self.proTeam = "N/A"
                        self.bye_week = "N/A"
                fas = [FPPlayer(r["Player"]) for _, r in df.iterrows()]
            source_used = "FantasyPros"

        for fa in fas:
            fa_w = get_proj_week(fa)
            fa_ros = ros_estimate(fa)
            drop = _best_drop(pos)
            if drop:
                d_w = get_proj_week(drop)
                d_ros = ros_estimate(drop)
                delta_w = fa_w - d_w
                delta_ros = fa_ros - d_ros
                worth = (delta_w >= weekly_threshold) or (delta_ros >= ros_threshold)
                verdict = "✅ Add (starts)" if worth and _would_start(fa) else ("✅ Add" if worth else "❌ Pass")
                drop_name = f"{drop.name} ({getattr(drop,'position','')})"
            else:
                delta_w = delta_ros = 0.0
                verdict = "✅ Add" if fa_w >= weekly_threshold or fa_ros >= ros_threshold else "❌ Pass"
                drop_name = "-"

            rows.append({
                "Player": fa.name,
                "Pos": pos,
                "Team": getattr(fa, "proTeam", "N/A"),
                "Bye": getattr(fa, "bye_week", "N/A"),
                "Source": source_used,
                f"Weekly ({proj_source})": round(fa_w, 1),
                "ROS (est.)": round(fa_ros, 1),
                "Drop": drop_name,
                "Δ Weekly": round(delta_w, 1),
                "Δ ROS (est.)": round(delta_ros, 1),
                "Would Start?": "Yes" if _would_start(fa) else "No",
                "Verdict": verdict,
            })

    if not rows:
        st.info("No free agents found via ESPN or FP fallback.")
    else:
        df_fa = pd.DataFrame(rows)
        df_fa.sort_values(by=["Verdict", "Δ Weekly", "Δ ROS (est.)"], ascending=[False, False, False], inplace=True)
        st.dataframe(df_fa.head(fa_size), use_container_width=True)

# =========================================
# Tab 6: Waiver Tracker (uses same logic; shorter view)
# =========================================
with tabs[6]:
    st.markdown("### 🧾 Waiver Wire Tracker")
    st.caption("Ranks FAs by Δ Weekly and Δ ROS (est.) vs best drop.")

    wt_fa_size = st.slider("Rows to show", 10, 200, 60, step=10, key="wt_rows")
    view_cols = [
        "Player", "Pos", "Team", "Bye", "Source",
        f"Weekly ({proj_source})", "ROS (est.)",
        "Drop", "Δ Weekly", "Δ ROS (est.)", "Would Start?", "Verdict"
    ]
    # Reuse the FA table if already computed in this run
    if "df_fa" in locals() and not df_fa.empty:
        view = df_fa.sort_values(by=["Δ Weekly", "Δ ROS (est.)"], ascending=False)
        st.dataframe(view[view_cols].head(wt_fa_size), use_container_width=True)
    else:
        st.info("Open the Free Agents tab first (or refresh).")

# =========================================
# Tab 7: What-If Lineup (simulate adding FA)
# =========================================
with tabs[7]:

    st.markdown("### 🧪 What-If: If I picked up a free agent, my starting lineup would be…")
    size = st.slider("FA pool per position to consider", 10, 200, 50, step=10)
    rostered_names = get_all_rostered_names(league)
    FA_FETCH_MAX = 300

    pool = []
    for pos in ["QB", "RB", "WR", "TE", "K", "D/ST"]:
        f = []
        try:
            try:
                f = league.free_agents(position=pos, size=FA_FETCH_MAX)
            except Exception:
                if pos == "D/ST":
                    f = league.free_agents(position="DST", size=FA_FETCH_MAX)
        except Exception:
            f = []

        if not f:
            key = {"QB": "qb", "RB": "rb", "WR": "wr", "TE": "te", "K": "k", "D/ST": "dst"}[pos]
            df = FP_WEEKLY.get(key, pd.DataFrame())
            if not df.empty and "FPTS" in df.columns:
                df = df[~df["Player"].isin(rostered_names)].copy()
                df["FPTS_num"] = pd.to_numeric(df["FPTS"], errors="coerce").fillna(0.0)
                df.sort_values("FPTS_num", ascending=False, inplace=True)
                df = df.head(FA_FETCH_MAX)
                class FPPlayer:
                    def __init__(self, name):
                        self.name = name
                        self.position = pos
                        self.proTeam = "N/A"
                        self.bye_week = "N/A"
                f = [FPPlayer(r["Player"]) for _, r in df.iterrows()]
        pool.extend(f)

    names = [f"{p.name} — {getattr(p,'position','')} ({get_proj_week(p):.1f} wk / {ros_estimate(p):.1f} ROS)" for p in pool]
    pick = st.selectbox("Free agent to add", options=["— pick a player —"] + names)
    drop_opts = ["(auto choose best drop)"] + [f"{p.name} — {p.position}" for p in my_team.roster]
    drop_sel = st.selectbox("Who would you drop?", options=drop_opts)

    if pick and pick != "— pick a player —":
        fa = pool[names.index(pick) - 1]  # minus 1 for the placeholder
        lineup, bench = build_optimizer(my_team.roster, starting_slots)
        if drop_sel == "(auto choose best drop)":
            # choose among bench by lowest ROS then weekly
            candidate_pool = bench or my_team.roster
            drop = sorted(candidate_pool, key=lambda p: (ros_estimate(p), get_proj_week(p)))[0]
        else:
            drop_name = drop_sel.split(" — ")[0]
            drop = next((p for p in my_team.roster if p.name == drop_name), None)

        hypo = [p for p in my_team.roster if p != drop] + [fa]
        cur_lineup, _ = build_optimizer(my_team.roster, starting_slots)
        new_lineup, _ = build_optimizer(hypo, starting_slots)

        def total(lp):
            return sum(get_proj_week(p) for L in lp.values() for p in L), \
                   sum(ros_estimate(p) for L in lp.values() for p in L)

        cur_w, cur_ros = total(cur_lineup)
        new_w, new_ros = total(new_lineup)

        st.markdown("#### Result")
        st.write(f"**Weekly**: {new_w:.1f} ({new_w - cur_w:+.1f}) | **ROS (est.)**: {new_ros:.1f} ({new_ros - cur_ros:+.1f})")
        st.caption(f"Drop: **{getattr(drop,'name','N/A')}** → Add: **{fa.name} ({fa.position})**")

# ----- Advanced Stats -----
with tabs[5]:
    st.markdown("### 📊 Advanced Player Stats")

    try:
        adv_rows = []
        for p in my_team.roster:
            adv_rows.append({
                "Player": getattr(p, "name", "N/A"),
                "Pos": getattr(p, "position", "N/A"),
                f"Weekly ({proj_source})": get_proj_week(p),
                "ROS ESPN": get_ros_espn(p),
                "ROS FP": get_ros_fp(p),
                "Last Week": getattr(p, "points", 0),
                "Opponent": getattr(p, "pro_opponent", "N/A"),
            })
        df_adv = pd.DataFrame(adv_rows)

        st.dataframe(df_adv, use_container_width=True)

        if not df_adv.empty:
            df_melt = df_adv.melt(
                id_vars=["Player", "Pos"],
                value_vars=[f"Weekly ({proj_source})", "ROS ESPN", "ROS FP"],
                var_name="Type",
                value_name="Points",
            )
            df_melt["Points"] = pd.to_numeric(df_melt["Points"], errors="coerce").fillna(0)

            chart = (
                altair.Chart(df_melt)
                .mark_bar()
                .encode(
                    x=altair.X("Player:N", sort="-y"),
                    y=altair.Y("Points:Q"),
                    color="Type:N",
                    column="Pos:N",
                    tooltip=["Player", "Pos", "Type", "Points"],
                )
                .properties(width=140, height=260)
            )
            st.altair_chart(chart, use_container_width=True)
        else:
            st.info("No data available yet for advanced stats.")

    except Exception as e:
        st.warning("Could not load advanced stats.")
        st.caption(str(e))
