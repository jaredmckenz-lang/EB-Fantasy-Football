import os
import pandas as pd
import streamlit as st
import altair as alt
import requests
from bs4 import BeautifulSoup
from espn_api.football import League

st.set_page_config(
    page_title="Fantasy Starter Optimizer",
    page_icon="🏈",
    layout="wide",
)
# ============== Utilities & Helpers ==============
def safe_proj(val):
    try:
        return float(val or 0)
    except Exception:
        return 0.0
@st.cache_data(ttl=6 * 60 * 60)
def _fetch_fp_table(url: str) -> pd.DataFrame:
    """Fetch FantasyPros table id='data' and extract Player, FP_Team, FP_Bye."""
    import re

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
        df["_Player_raw"] = df["Player"]

        def extract_team(raw):
            import re as _re

            m = _re.search(r"\(([^)]+)\)", raw or "")
            return m.group(1) if m else "N/A"

        df["FP_Team"] = df["_Player_raw"].apply(extract_team)
        df["Player"] = df["_Player_raw"].str.replace(
            r"\s\([^)]+\)", "", regex=True
        )

    df["FP_Bye"] = df["Bye"] if "Bye" in df.columns else "N/A"
    return df


@st.cache_data(ttl=6 * 60 * 60)
def fetch_all_fantasypros_weekly(scoring="ppr") -> dict:
    """Weekly projections for all positions."""
    pos_list = ["qb", "rb", "wr", "te", "k", "dst"]
    data = {}
    for pos in pos_list:
        url = (
            f"https://www.fantasypros.com/nfl/projections/{pos}.php?"
            f"scoring={scoring}"
        )
        try:
            data[pos] = _fetch_fp_table(url)
        except Exception as e:
            st.warning(f"FantasyPros weekly fetch failed for {pos}: {e}")
            data[pos] = pd.DataFrame()
    return data


@st.cache_data(ttl=6 * 60 * 60)
def fetch_all_fantasypros_season(scoring="ppr") -> dict:
    """Season totals for all positions (week=draft page)."""
    pos_list = ["qb", "rb", "wr", "te", "k", "dst"]
    data = {}
    for pos in pos_list:
        url = (
            f"https://www.fantasypros.com/nfl/projections/{pos}.php?"
            f"week=draft&scoring={scoring}"
        )
        try:
            data[pos] = _fetch_fp_table(url)
        except Exception as e:
            st.warning(f"FantasyPros season fetch failed for {pos}: {e}")
            data[pos] = pd.DataFrame()
    return data
def _pos_key(player):
    return {
        "QB": "qb",
        "RB": "rb",
        "WR": "wr",
        "TE": "te",
        "K": "k",
        "D/ST": "dst",
    }.get(getattr(player, "position", "").upper())


def _match_fp_row(df: pd.DataFrame, name: str):
    if df.empty or "Player" not in df.columns:
        return None
    first = name.split()[0]
    hits = df[df["Player"].str.contains(first, case=False, na=False)]
    return hits.iloc[0] if not hits.empty else None


def get_proj_week(player, week=None):
    """Weekly projection based on sidebar source (ESPN/FP)."""
    if week is None:
        week = league.current_week

    # ESPN first if allowed
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
            return safe_proj(row.get("FPTS", 0))
    return 0.0


def get_ros_espn(player, start_week=None):
    """ESPN ROS: sum future weekly projected stats."""
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
    """FantasyPros season-long total (FPTS)."""
    key = _pos_key(player)
    if not key:
        return 0.0
    df = fp_season.get(key, pd.DataFrame())
    row = _match_fp_row(df, player.name)
    if row is not None:
        return safe_proj(row.get("FPTS", 0))
    return 0.0


def format_injury(player):
    status = getattr(player, "injuryStatus", None)
    wk = get_proj_week(player)
    text = f"{player.name} — {wk:.1f} (This Week)"
    return f"⚠️ {text} ({status})" if status else text


def build_optimizer(roster, starting_slots):
    """Sort by weekly proj per slot, fill, avoid duplicates, return lineup & bench."""
    groups = {k: [] for k in ["QB", "RB", "WR", "TE", "D/ST", "K", "FLEX"]}
    for p in roster:
        pos = getattr(p, "position", "")
        if pos in groups:
            groups[pos].append(p)
    for pos in ["RB", "WR", "TE"]:
        groups["FLEX"].extend(groups[pos])
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
        league_id = st.number_input(
            "League ID",
            value=league_id,
            step=1,
            key="sb_league_id",
        )
        team_id = st.number_input(
            "Team ID",
            value=team_id,
            min_value=1,
            step=1,
            key="sb_team_id",
        )
        year = st.number_input(
            "Season",
            value=year,
            min_value=2018,
            step=1,
            key="sb_year",
        )
        st.caption(
            "Using Streamlit secrets for credentials. Override IDs above "
            "if needed."
        )

    if not espn_s2 or not swid:
        st.error("Missing ESPN credentials in Streamlit Secrets.")
        st.stop()

    league = League(
        league_id=int(league_id),
        year=int(year),
        espn_s2=espn_s2,
        swid=swid,
    )
    team = league.teams[int(team_id) - 1]
    return league, team


def _get_setting(league, *names, default=None):
    settings = getattr(league, "settings", None)
    for n in names:
        if settings is not None and hasattr(settings, n):
            return getattr(settings, n)
        if hasattr(league, n):
            return getattr(league, n)
    return default


def get_league_waiver_info(league):
    return {
        "waiver_type": _get_setting(
            league, "waiverType", "waiver_type", default="N/A"
        ),
        "process_day": _get_setting(
            league, "waiverProcessDay", "waiver_day_of_week", default="N/A"
        ),
        "process_hour": _get_setting(
            league, "waiverProcessHour", "waiver_hour", default="N/A"
        ),
        "waiver_hours": _get_setting(
            league, "waiverHours", "waiver_hours", default="N/A"
        ),
        "trade_deadline": _get_setting(
            league, "tradeDeadlineDate", "trade_deadline",
            "tradeDeadline", default="N/A"
        ),
        "timezone": _get_setting(league, "timezone", default="N/A"),
    }
def trade_summary_verdict(team_gain_wk, team_gain_rosE, team_gain_rosF):
    ros_gain = max(team_gain_rosE, team_gain_rosF)
    score = (team_gain_wk / 5.0) + (ros_gain / 50.0)
    if score >= 1.0:
        return "🟢 Strong Accept", score
    if score >= 0.5:
        return "🟡 Lean Accept", score
    if score > -0.3:
        return "⚪ Neutral / Even", score
    if score > -0.8:
        return "🟠 Lean Decline", score
    return "🔴 Strong Decline", score


def get_all_rostered_names(league):
    names = set()
    for t in league.teams:
        for p in t.roster:
            names.add(p.name.strip())
    return names


class FPPlayer:
    def __init__(self, name, position, team="N/A", bye="N/A"):
        self.name = name
        self.position = position
        self.proTeam = team
        self.bye_week = bye
# ============== App ==============
st.title("🏈 Fantasy Football Weekly Starter Optimizer")

league, my_team = connect_league()
st.subheader(f"Team: **{my_team.team_name}** ({my_team.team_abbrev})")

with st.sidebar:
    st.header("Projection Source")
    proj_source = st.radio(
        "Choose weekly projections",
        ["ESPN only", "FantasyPros fallback", "FantasyPros only"],
        index=1,
        key="sb_proj_source",
    )

fp_weekly = fetch_all_fantasypros_weekly()
fp_season = fetch_all_fantasypros_season()

with st.expander("Lineup Slots", expanded=True):
    c1, c2, c3, c4 = st.columns(4)
    QB = c1.number_input("QB", 1, 3, 1, key="ls_qb")
    RB = c2.number_input("RB", 1, 5, 2, key="ls_rb")
    WR = c3.number_input("WR", 1, 5, 2, key="ls_wr")
    TE = c4.number_input("TE", 1, 3, 1, key="ls_te")
    c5, c6, c7 = st.columns(3)
    FLEX = c5.number_input("FLEX (RB/WR/TE)", 0, 3, 1, key="ls_flex")
    DST = c6.number_input("D/ST", 0, 2, 1, key="ls_dst")
    K = c7.number_input("K", 0, 2, 1, key="ls_k")

starting_slots = {
    "QB": QB,
    "RB": RB,
    "WR": WR,
    "TE": TE,
    "FLEX": FLEX,
    "D/ST": DST,
    "K": K,
}

tabs = st.tabs(
    [
        "✅ Optimizer",
        "🔍 Matchups",
        "🔄 Trade Analyzer",
        "🛒 Free Agents",
        "📈 Logs",
        "📊 Advanced Stats",
        "🧾 Waiver Tracker",
        "🧪 What-If Lineup",
    ]
)
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
            f"{get_ros_espn(p):.1f} (ROS ESPN) | "
            f"{get_ros_fp(p):.1f} (ROS FP)"
        )

    st.markdown("#### 🧠 How this lineup was chosen")
    st.caption(
        "We sort each position (and a combined FLEX pool) by **This Week** "
        "projection, then fill required slots top-down, avoiding duplicates. "
        "FLEX compares RB/WR/TE together."
    )

    def _best_alternative(slot):
        used = sum(lineup.values(), [])
        if slot in ["QB", "RB", "WR", "TE", "K", "D/ST"]:
            elig = [
                p for p in roster
                if getattr(p, "position", "") == slot and p not in used
            ]
        else:
            elig = [
                p for p in roster
                if getattr(p, "position", "") in ["RB", "WR", "TE"]
                and p not in used
            ]
        if not elig:
            return None, 0.0
        alt = max(elig, key=lambda p: get_proj_week(p))
        return alt, get_proj_week(alt)

    rows = []
    for slot, players in lineup.items():
        for p in players:
            p_w = get_proj_week(p)
            alt, alt_w = _best_alternative(slot)
            margin = p_w - (alt_w if alt else 0.0)
            rows.append(
                {
                    "Slot": slot,
                    "Starter": p.name,
                    "This Week": round(p_w, 1),
                    "Next Best (unused)": getattr(alt, "name", "—"),
                    "Next Best (This Week)": round(alt_w, 1) if alt else "—",
                    "Margin": round(margin, 1),
                }
            )

    if rows:
        df_why = pd.DataFrame(rows).sort_values(
            ["Slot", "Margin"], ascending=[True, False]
        )
        st.dataframe(df_why, use_container_width=True)
# ----- Matchups -----
with tabs[1]:
    st.markdown("### 📊 This Week's Matchups & Projections")
    st.caption(f"Week {league.current_week}")

    cards = []
    my_game = None

    for m in league.box_scores():
        home, away = m.home_team, m.away_team
        hp = safe_proj(getattr(home, "projected_total", 0))
        ap = safe_proj(getattr(away, "projected_total", 0))
        cards.append((home, hp, away, ap))
        if my_team.team_id in (home.team_id, away.team_id):
            my_game = (home, hp, away, ap)

    if cards:
        avg_proj = sum(hp + ap for _, hp, _, ap in cards) / (2 * len(cards))
        st.markdown(
            f"**League avg projected points (per team):** {avg_proj:.1f}"
        )
        st.divider()

    for home, hp, away, ap in cards:
        st.write(
            f"**{home.team_name}** ({home.team_abbrev}) vs "
            f"**{away.team_name}** ({away.team_abbrev})"
        )
        st.progress(min(int(hp * 2), 100), text=f"{home.team_abbrev}: {hp:.1f} pts")
        st.progress(min(int(ap * 2), 100), text=f"{away.team_abbrev}: {ap:.1f} pts")
        fav = home.team_abbrev if (hp - ap) >= 0 else away.team_abbrev
        st.caption(f"Projected margin: {fav} {abs(hp - ap):.1f}")
        st.divider()

    if my_game:
        home, hp, away, ap = my_game
        margin = hp - ap if home.team_id == my_team.team_id else ap - hp
        tilt = "favored" if margin >= 0 else "underdog"
        st.info(
            f"**Your game:** {home.team_abbrev} vs {away.team_abbrev} — "
            f"You are **{tilt}** by {abs(margin):.1f} (by projections)."
        )


# ----- Trade Analyzer -----
with tabs[2]:
    st.markdown("### 🔄 Team-to-Team Trade Analyzer")
    st.caption(
        f"Weekly uses **{proj_source}**. ROS shows **both** ESPN and FantasyPros "
        "season totals."
    )

    team_options = [f"{t.team_name} ({t.team_abbrev})" for t in league.teams]
    team_lookup = {f"{t.team_name} ({t.team_abbrev})": t for t in league.teams}
    default_idx = next(
        (i for i, label in enumerate(team_options)
         if team_lookup[label].team_id == my_team.team_id),
        0,
    )

    colA, colB = st.columns(2)
    with colA:
        teamA_label = st.selectbox(
            "Team A", team_options, index=default_idx, key="ta_team_a"
        )
    with colB:
        alt_idx = 1 if default_idx == 0 and len(team_options) > 1 else 0
        teamB_label = st.selectbox(
            "Team B", team_options, index=alt_idx, key="ta_team_b"
        )

    teamA = team_lookup[teamA_label]
    teamB = team_lookup[teamB_label]

    if teamA.team_id != teamB.team_id:
        # trade analyzer code continues here...


        def table(players, title):
            rows = [
                {
                    "Player": p.name,
                    "Pos": getattr(p, "position", ""),
                    f"Weekly ({proj_source})": get_proj_week(p),
                    "ROS ESPN": get_ros_espn(p),
                    "ROS FP": get_ros_fp(p),
                }
                for p in players
            ]
            st.markdown(f"**{title}**")
            if rows:
                st.dataframe(pd.DataFrame(rows), use_container_width=True)
            else:
                st.caption("None selected.")

        def roster_names(team):
            return [
                f"{p.name} — {p.position} "
                f"({get_proj_week(p):.1f} wk / {get_ros_fp(p):.1f} ROS-FP)"
                for p in team.roster
            ]

        def string_to_player(name_str, team):
            pname = name_str.split(" — ")[0]
            return next((p for p in team.roster if p.name == pname), None)

        col1, col2 = st.columns(2)
        with col1:
            send_A_labels = st.multiselect(
                f"{teamA_label} sends", options=roster_names(teamA),
                key="ta_send_a",
            )
        with col2:
            send_B_labels = st.multiselect(
                f"{teamB_label} sends", options=roster_names(teamB),
                key="ta_send_b",
            )

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
        st.write(
            f"**This Week ({proj_source})** → "
            f"{teamA.team_abbrev} net: {B_wk - A_wk:+.1f}, "
            f"{teamB.team_abbrev} net: {A_wk - B_wk:+.1f}"
        )
        st.write(
            f"**ROS ESPN** → {teamA.team_abbrev} net: {B_rosE - A_rosE:+.1f}, "
            f"{teamB.team_abbrev} net: {A_rosE - B_rosE:+.1f}"
        )
        st.write(
            f"**ROS FP** → {teamA.team_abbrev} net: {B_rosF - A_rosF:+.1f}, "
            f"{teamB.team_abbrev} net: {A_rosF - B_rosF:+.1f}"
        )

        A_gain_wk = B_wk - A_wk
        B_gain_wk = A_wk - B_wk
        A_gain_rosE = B_rosE - A_rosE
        B_gain_rosE = A_rosE - B_rosE
        A_gain_rosF = B_rosF - A_rosF
        B_gain_rosF = A_rosF - B_rosF

        a_verdict, a_score = trade_summary_verdict(
            A_gain_wk, A_gain_rosE, A_gain_rosF
        )
        b_verdict, b_score = trade_summary_verdict(
            B_gain_wk, B_gain_rosE, B_gain_rosF
        )

        st.markdown("#### 🧠 Trade Verdicts")
        colV1, colV2 = st.columns(2)
        with colV1:
            st.write(f"**{teamA.team_name} ({teamA.team_abbrev})**: {a_verdict}")
            st.caption(
                "Net gains — Weekly: "
                f"{A_gain_wk:+.1f} | ROS ESPN: {A_gain_rosE:+.1f} | "
                f"ROS FP: {A_gain_rosF:+.1f} (score: {a_score:.2f})"
            )
        with colV2:
            st.write(f"**{teamB.team_name} ({teamB.team_abbrev})**: {b_verdict}")
            st.caption(
                "Net gains — Weekly: "
                f"{B_gain_wk:+.1f} | ROS ESPN: {B_gain_rosE:+.1f} | "
                f"ROS FP: {B_gain_rosF:+.1f} (score: {b_score:.2f})"
            )

        if a_score > b_score + 0.2:
            st.success(f"👍 {teamA.team_abbrev} benefits by our model.")
        elif b_score > a_score + 0.2:
            st.success(f"👍 {teamB.team_abbrev} benefits by our model.")
        else:
            st.info("Even trade by our model — league context matters.")

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
        st.markdown("#### 💡 Example trade ideas (1-for-1)")
    ideas = []
    cur_lineup, _ = build_optimizer(my_team.roster, starting_slots)
    your_bench = [
        p for p in my_team.roster if p not in sum(cur_lineup.values(), [])
    ]
    for opp in league.teams:
        if opp.team_id == my_team.team_id:
            continue
        for yp in your_bench:
            pos = getattr(yp, "position", "")
            for tp in opp.roster:
                if getattr(tp, "position", "") != pos:
                    continue
                gain_w = get_proj_week(tp) - get_proj_week(yp)
                gain_ros = max(
                    get_ros_espn(tp) - get_ros_espn(yp),
                    get_ros_fp(tp) - get_ros_fp(yp),
                )
                score = (gain_w / 5.0) + (gain_ros / 50.0)
                if gain_w > 0 or gain_ros > 0:
                    ideas.append(
                        {
                            "You give": f"{yp.name} ({pos})",
                            "You get": f"{tp.name} ({opp.team_abbrev})",
                            "Δ Weekly": round(gain_w, 1),
                            "Δ ROS (best of ESPN/FP)": round(gain_ros, 1),
                            "Score": round(score, 3),
                        }
                    )
    if ideas:
        df_ideas = pd.DataFrame(ideas).sort_values(
            ["Score", "Δ Weekly", "Δ ROS (best of ESPN/FP)"], ascending=False
        ).head(10)
        st.dataframe(df_ideas, use_container_width=True)
    else:
        st.caption("No obvious 1-for-1 upgrades found.")
        # ----- Free Agents -----
with tabs[3]:
    st.markdown("### 🛒 Free Agents — Add/Drop Recommendations")
    st.caption(
        "Pulls ESPN free agents when available. If none (offseason/draft), "
        "falls back to FantasyPros weekly projections minus all rostered players "
        "in your league."
    )

    fa_size = st.slider(
        "How many free agents per position to scan",
        10,
        200,
        60,
        step=10,
        key="fa_size",
    )
    weekly_threshold = st.number_input(
        "Worth-it threshold (Δ Weekly points)",
        0.0,
        20.0,
        2.0,
        step=0.5,
        key="fa_weekly_thr",
    )
    ros_threshold = st.number_input(
        "Worth-it threshold (Δ ROS points)",
        0.0,
        300.0,
        18.0,
        step=1.0,
        key="fa_ros_thr",
    )

    my_roster = my_team.roster
    lineup, bench = build_optimizer(my_roster, starting_slots)

    starters_by_pos = {
        k: lineup.get(k, []) for k in ["QB", "RB", "WR", "TE", "K", "D/ST"]
    }
    flex_eligible = {"RB", "WR", "TE"}

    def _is_flex_eligible(pos):
        return pos in flex_eligible

    def _lowest_bench_candidate(position):
        same_pos = [p for p in bench if getattr(p, "position", "") == position]
        pool = (
            same_pos
            or (
                [
                    p
                    for p in bench
                    if getattr(p, "position", "") in flex_eligible
                ]
                if _is_flex_eligible(position)
                else []
            )
        )
        if not pool:
            return None, 0.0, 0.0, 0.0
        pool_sorted = sorted(
            pool, key=lambda p: (get_ros_fp(p), get_proj_week(p))
        )
        cand = pool_sorted[0]
        return cand, get_proj_week(cand), get_ros_espn(cand), get_ros_fp(cand)

    def _would_start(fa_player):
        pos = getattr(fa_player, "position", "")
        fa_w = get_proj_week(fa_player)
        slot_starters = starters_by_pos.get(pos, [])
        if slot_starters:
            worst = min(slot_starters, key=lambda p: get_proj_week(p))
            if fa_w > get_proj_week(worst):
                return True
        if _is_flex_eligible(pos) and lineup.get("FLEX"):
            worst_flex = min(lineup["FLEX"], key=lambda p: get_proj_week(p))
            if fa_w > get_proj_week(worst_flex):
                return True
        return False

    rows = []
    positions_to_scan = ["QB", "RB", "WR", "TE", "K", "D/ST"]
    rostered_names = get_all_rostered_names(league)
    espn_counts, fp_counts = {}, {}

    for pos in positions_to_scan:
        fa_list = []
        source_used = "ESPN"
        try:
            try:
                fa_list = league.free_agents(position=pos, size=fa_size)
            except Exception:
                if pos == "D/ST":
                    fa_list = league.free_agents(position="DST", size=fa_size)
        except Exception as e:
            st.warning(f"Could not fetch ESPN free agents for {pos}: {e}")
            fa_list = []

        espn_counts[pos] = len(fa_list)

        if len(fa_list) == 0:
            source_used = "FantasyPros"
            key = {"QB": "qb", "RB": "rb", "WR": "wr", "TE": "te", "K": "k",
                   "D/ST": "dst"}[pos]
            df_fp = fp_weekly.get(key, pd.DataFrame())
            if not df_fp.empty and "FPTS" in df_fp.columns:
                df_fp = df_fp[~df_fp["Player"].isin(rostered_names)].copy()
                df_fp["FPTS_num"] = pd.to_numeric(
                    df_fp["FPTS"], errors="coerce"
                ).fillna(0.0)
                df_fp.sort_values("FPTS_num", ascending=False, inplace=True)
                df_fp = df_fp.head(fa_size)
                fa_list = [
                    FPPlayer(
                        row["Player"],
                        pos,
                        team=row.get("FP_Team", "N/A"),
                        bye=row.get("FP_Bye", "N/A"),
                    )
                    for _, row in df_fp.iterrows()
                ]
            fp_counts[pos] = len(fa_list)
        else:
            fp_counts[pos] = 0

        for fa in fa_list:
            fa_w = get_proj_week(fa)
            fa_re = get_ros_espn(fa)
            fa_rf = get_ros_fp(fa)
            drop_cand, drop_w, drop_re, drop_rf = _lowest_bench_candidate(pos)

            if drop_cand is None:
                verdict = "No roster spot to drop"
                delta_w = delta_re = delta_rf = 0.0
                drop_name = "-"
            else:
                delta_w = fa_w - drop_w
                delta_re = fa_re - drop_re
                delta_rf = fa_rf - drop_rf
                worth = (
                    (delta_w >= weekly_threshold)
                    or (delta_re >= ros_threshold)
                    or (delta_rf >= ros_threshold)
                )
                verdict = (
                    "✅ Add (starts)"
                    if worth and _would_start(fa)
                    else ("✅ Add" if worth else "❌ Pass")
                )
                drop_name = f"{drop_cand.name} ({getattr(drop_cand, 'position', '')})"

            rows.append(
                {
                    "Player": fa.name,
                    "Pos": pos,
                    "Team": getattr(fa, "proTeam", "N/A"),
                    "Bye": getattr(fa, "bye_week", "N/A"),
                    "Source": source_used,
                    f"Weekly ({proj_source})": round(fa_w, 1),
                    "ROS ESPN": round(fa_re, 1),
                    "ROS FP": round(fa_rf, 1),
                    "Drop": drop_name,
                    "Δ Weekly": round(delta_w, 1),
                    "Δ ROS ESPN": round(delta_re, 1),
                    "Δ ROS FP": round(delta_rf, 1),
                    "Would Start?": "Yes" if _would_start(fa) else "No",
                    "Verdict": verdict,
                }
            )

    with st.expander("Debug: FA source counts per position"):
        dbg = pd.DataFrame(
            {
                "Position": positions_to_scan,
                "ESPN FAs": [espn_counts.get(p, 0) for p in positions_to_scan],
                "FP Fallback Used": [
                    fp_counts.get(p, 0) for p in positions_to_scan
                ],
            }
        )
        st.dataframe(dbg, use_container_width=True)

    if rows:
        df_fa = pd.DataFrame(rows)
        df_fa["_score"] = df_fa[
            ["Δ Weekly", "Δ ROS ESPN", "Δ ROS FP"]
        ].max(axis=1)
        df_fa["Recommended"] = df_fa["Verdict"].str.startswith("✅")
        df_fa.sort_values(
            by=["Recommended", "_score"],
            ascending=[False, False],
            inplace=True,
        )
        df_fa.drop(columns=["_score"], inplace=True)

        c1, c2, c3, c4 = st.columns(4)
        with c1:
            pos_filter = st.multiselect(
                "Filter positions",
                positions_to_scan,
                default=positions_to_scan,
                key="fa_pos_filter",
            )
        with c2:
            only_rec = st.checkbox(
                "Show only recommended adds (✅)",
                value=False,
                key="fa_only_rec",
            )
        with c3:
            only_starters = st.checkbox(
                "Show only FAs who would start",
                value=False,
                key="fa_only_starters",
            )
        with c4:
            source_filter = st.multiselect(
                "Source",
                ["ESPN", "FantasyPros"],
                default=["ESPN", "FantasyPros"],
                key="fa_source_filter",
            )

        view = df_fa[df_fa["Pos"].isin(pos_filter)]
        view = view[view["Source"].isin(source_filter)]
        if only_rec:
            view = view[view["Recommended"]]
        if only_starters:
            view = view[view["Would Start?"] == "Yes"]

        st.dataframe(
            view.drop(columns=["Recommended"]),
            use_container_width=True,
        )
        st.caption(
            "Verdict rules: Add if Δ Weekly ≥ threshold OR Δ ROS (ESPN/FP) ≥ "
            "threshold. ‘Starts’ if FA outprojects your worst starter at that "
            "position (or FLEX)."
        )
    else:
        st.info(
            "No free agents found (even with FantasyPros fallback). Try "
            "increasing the 'How many free agents' slider."
        )
# ----- Logs -----
with tabs[4]:
    st.markdown("### Weekly Performance Logger")
    week = getattr(league, "current_week", None)
    colA, colB, colC = st.columns(3)
    colA.metric(
        "Projected Total (This Week)",
        f"{sum(get_proj_week(p) for p in my_team.roster):.1f}",
    )
    colB.metric(
        "Projected Total (ROS ESPN)",
        f"{sum(get_ros_espn(p) for p in my_team.roster):.1f}",
    )
    colC.metric(
        "Projected Total (ROS FP)",
        f"{sum(get_ros_fp(p) for p in my_team.roster):.1f}",
    )

    log_file = "performance_log.csv"
    if st.button("📊 Log This Week", key="logs_log_button"):
        row = {
            "Week": week,
            "Team": my_team.team_name,
            f"Projected (Weekly: {proj_source})": sum(
                get_proj_week(p) for p in my_team.roster
            ),
            "Projected (ROS ESPN)": sum(
                get_ros_espn(p) for p in my_team.roster
            ),
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
    st.markdown(
        f"### 📊 Advanced Player Stats ({proj_source} weekly; ROS=ESPN & FP)"
    )
    roster = my_team.roster
    rows = []
    for p in roster:
        rows.append(
            {
                "Player": p.name,
                "Pos": getattr(p, "position", "N/A"),
                f"Weekly ({proj_source})": get_proj_week(p),
                "ROS ESPN": get_ros_espn(p),
                "ROS FP": get_ros_fp(p),
                "Last Week": safe_proj(getattr(p, "points", 0)),
                "Opponent": getattr(p, "pro_opponent", "N/A"),
            }
        )
    df = pd.DataFrame(rows)

    if df.empty:
        st.info("No player data available yet.")
    else:
        st.dataframe(df)

        df_melt = df.melt(
            id_vars=["Player", "Pos"],
            value_vars=[f"Weekly ({proj_source})", "ROS ESPN", "ROS FP"],
            var_name="Type",
            value_name="Points",
        )
        chart = (
            alt.Chart(df_melt)
            .mark_bar()
            .encode(
                x=alt.X("Player:N", sort="-y"),
                y="Points:Q",
                color="Type:N",
                column="Pos:N",
                tooltip=["Player", "Pos", "Type", "Points"],
            )
            .properties(width=120, height=250)
        )
        st.altair_chart(chart, use_container_width=True)

    if st.checkbox("🔍 Show raw ESPN projection debug", key="adv_debug_checkbox"):
        debug = []
        for p in roster:
            debug.append(
                {
                    "Player": p.name,
                    "projected_points": getattr(p, "projected_points", None),
                    "stats_proj(cur wk)": (
                        p.stats.get(league.current_week, {}).get("projected", None)
                        if hasattr(p, "stats")
                        else None
                    ),
                }
            )
        st.dataframe(pd.DataFrame(debug))
# ----- Waiver Tracker -----
with tabs[6]:
    st.markdown("### 🧾 Waiver Wire Tracker")
    st.caption(
        "Ranks free agents by expected gain vs your best drop and suggests "
        "a FAAB bid. Weekly projections use your sidebar source; ROS uses "
        "both ESPN and FP (season)."
    )

    info = get_league_waiver_info(league)
    st.markdown("#### 🗓️ League Waivers & Deadlines")
    cwa, cwb, cwc, cwd = st.columns(4)
    cwa.metric("Waiver Type", str(info["waiver_type"]))
    cwb.metric("Process Day", str(info["process_day"]))
    cwc.metric(
        "Process Hour",
        f'{info["process_hour"]}:00' if info["process_hour"] != "N/A" else "N/A",
    )
    cwd.metric("Waiver Period (hrs)", str(info["waiver_hours"]))
    st.caption(
        f'Trade deadline: **{info["trade_deadline"]}**  |  '
        f'Timezone: **{info["timezone"]}**'
    )
    st.divider()

    cA, cB, cC, cD = st.columns(4)
    with cA:
        wt_fa_size = st.slider(
            "FA pool per position",
            10,
            200,
            60,
            step=10,
            key="wt_pool_size",
        )
    with cB:
        budget_remaining = st.number_input(
            "Your remaining FAAB ($)",
            min_value=0,
            value=100,
            step=5,
            key="wt_budget",
        )
    with cC:
        max_bid_pct = st.slider(
            "Max bid % of budget",
            5,
            80,
            30,
            step=5,
            key="wt_max_bid_pct",
        )
    with cD:
        target_positions = st.multiselect(
            "Positions to consider",
            ["RB", "WR", "QB", "TE", "D/ST", "K"],
            default=["RB", "WR", "TE", "QB"],
            key="wt_target_positions",
        )

    st.markdown("**Positional weights** (scarcity/impact)")
    d1, d2, d3, d4, d5, d6 = st.columns(6)
    w_rb = d1.number_input("RB w", 0.5, 2.0, 1.5, 0.1, key="wt_w_rb")
    w_wr = d2.number_input("WR w", 0.5, 2.0, 1.3, 0.1, key="wt_w_wr")
    w_te = d3.number_input("TE w", 0.5, 2.0, 1.2, 0.1, key="wt_w_te")
    w_qb = d4.number_input("QB w", 0.5, 2.0, 1.0, 0.1, key="wt_w_qb")
    w_dst = d5.number_input("D/ST w", 0.5, 2.0, 0.7, 0.1, key="wt_w_dst")
    w_k = d6.number_input("K w", 0.5, 2.0, 0.6, 0.1, key="wt_w_k")
    pos_w = {"RB": w_rb, "WR": w_wr, "TE": w_te, "QB": w_qb, "D/ST": w_dst,
             "K": w_k}

    my_roster = my_team.roster
    lineup, bench = build_optimizer(my_roster, starting_slots)
    starters_by_pos = {
        k: lineup.get(k, []) for k in ["QB", "RB", "WR", "TE", "K", "D/ST"]
    }
    flex_eligible = {"RB", "WR", "TE"}

    def _is_flex_eligible(pos):
        return pos in flex_eligible

    def _lowest_drop_candidate(position):
        same_pos = [p for p in bench if getattr(p, "position", "") == position]
        pool = (
            same_pos
            or (
                [
                    p
                    for p in bench
                    if getattr(p, "position", "") in flex_eligible
                ]
                if _is_flex_eligible(position)
                else []
            )
        )
        if not pool:
            return None
        return sorted(
            pool, key=lambda p: (get_ros_fp(p), get_proj_week(p))
        )[0]

    def _would_start(player):
        pos = getattr(player, "position", "")
        w = get_proj_week(player)
        slot_starters = starters_by_pos.get(pos, [])
        if slot_starters:
            worst = min(slot_starters, key=lambda p: get_proj_week(p))
            if w > get_proj_week(worst):
                return True
        if _is_flex_eligible(pos) and lineup.get("FLEX"):
            worst_flex = min(lineup["FLEX"], key=lambda p: get_proj_week(p))
            if w > get_proj_week(worst_flex):
                return True
        return False

    rostered_names = get_all_rostered_names(league)
    rows = []

    for pos in target_positions:
        fa_list = []
        source_used = "ESPN"
        try:
            try:
                fa_list = league.free_agents(position=pos, size=wt_fa_size)
            except Exception:
                if pos == "D/ST":
                    fa_list = league.free_agents(position="DST", size=wt_fa_size)
        except Exception:
            fa_list = []

        if len(fa_list) == 0:
            source_used = "FantasyPros"
            key = {"QB": "qb", "RB": "rb", "WR": "wr", "TE": "te", "K": "k",
                   "D/ST": "dst"}[pos]
            df_fp = fp_weekly.get(key, pd.DataFrame())
            if not df_fp.empty and "FPTS" in df_fp.columns:
                df_fp = df_fp[~df_fp["Player"].isin(rostered_names)].copy()
                df_fp["FPTS_num"] = pd.to_numeric(
                    df_fp["FPTS"], errors="coerce"
                ).fillna(0.0)
                df_fp.sort_values("FPTS_num", ascending=False, inplace=True)
                df_fp = df_fp.head(wt_fa_size)
                fa_list = [
                    FPPlayer(
                        row["Player"],
                        pos,
                        team=row.get("FP_Team", "N/A"),
                        bye=row.get("FP_Bye", "N/A"),
                    )
                    for _, row in df_fp.iterrows()
                ]

        for fa in fa_list:
            drop = _lowest_drop_candidate(pos)
            if not drop:
                fa_w = get_proj_week(fa)
                rows.append(
                    {
                        "Player": fa.name,
                        "Pos": pos,
                        "Team": getattr(fa, "proTeam", "N/A"),
                        "Bye": getattr(fa, "bye_week", "N/A"),
                        "Source": source_used,
                        "Weekly": round(fa_w, 1),
                        "ROS ESPN": round(get_ros_espn(fa), 1),
                        "ROS FP": round(get_ros_fp(fa), 1),
                        "Δ Weekly": 0.0,
                        "Δ ROS": 0.0,
                        "Starts?": "Unknown (no drop)",
                        "Score": 0.0,
                        "Suggested Bid ($)": 0,
                    }
                )
                continue

            fa_w = get_proj_week(fa)
            fa_re = get_ros_espn(fa)
            fa_rf = get_ros_fp(fa)
            drop_w = get_proj_week(drop)
            drop_re = get_ros_espn(drop)
            drop_rf = get_ros_fp(drop)

            d_w = max(0.0, fa_w - drop_w)
            d_ros = max(0.0, max(fa_re - drop_re, fa_rf - drop_rf))
            start_bonus = 0.2 if _would_start(fa) else 0.0
            score = (d_w / 5.0) + (d_ros / 50.0) + start_bonus
            score *= pos_w.get(pos, 1.0)

            pct = min(max_bid_pct / 100.0, 0.05 + 0.35 * min(1.0, score))
            bid = int(round(pct * budget_remaining, 0))

            rows.append(
                {
                    "Player": fa.name,
                    "Pos": pos,
                    "Team": getattr(fa, "proTeam", "N/A"),
                    "Bye": getattr(fa, "bye_week", "N/A"),
                    "Source": source_used,
                    "Weekly": round(fa_w, 1),
                    "ROS ESPN": round(fa_re, 1),
                    "ROS FP": round(fa_rf, 1),
                    "Δ Weekly": round(d_w, 1),
                    "Δ ROS": round(d_ros, 1),
                    "Starts?": "Yes" if _would_start(fa) else "No",
                    "Score": round(score, 3),
                    "Suggested Bid ($)": bid,
                }
            )

    if not rows:
        st.info(
            "No free agents available from ESPN or FP fallback for the "
            "selected positions."
        )
    else:
        dfw = pd.DataFrame(rows)
        f1, f2, f3 = st.columns(3)
        with f1:
            pos_filter = st.multiselect(
                "Filter positions",
                target_positions,
                default=target_positions,
                key="waiver_pos_filter",
            )
        with f2:
            starters_only = st.checkbox(
                "Only players who would start",
                value=False,
                key="waiver_starters_only",
            )
        with f3:
            source_filter = st.multiselect(
                "Source",
                ["ESPN", "FantasyPros"],
                default=["ESPN", "FantasyPros"],
                key="waiver_source_filter",
            )

        view = dfw[dfw["Pos"].isin(pos_filter)]
        view = view[view["Source"].isin(source_filter)]
        if starters_only:
            view = view[view["Starts?"] == "Yes"]

        view = view.sort_values(
            by=["Score", "Δ Weekly", "Δ ROS"],
            ascending=False,
        )

        st.dataframe(
            view[
                [
                    "Player",
                    "Pos",
                    "Team",
                    "Bye",
                    "Source",
                    "Weekly",
                    "ROS ESPN",
                    "ROS FP",
                    "Δ Weekly",
                    "Δ ROS",
                    "Starts?",
                    "Score",
                    "Suggested Bid ($)",
                ]
            ],
            use_container_width=True,
        )
        csv = view.to_csv(index=False).encode("utf-8")
        st.download_button(
            "⬇️ Download Waiver Recommendations (CSV)",
            data=csv,
            file_name="waiver_tracker.csv",
            mime="text/csv",
        )
# ----- What-If Lineup (simulate adding a FA) -----
with tabs[7]:
    st.markdown("### 🧪 What-If: If I picked up a FA, my lineup would be…")
    st.caption(
        "Choose a free agent and (optionally) a drop. We recompute your "
        "optimized lineup and show the deltas."
    )

    positions_to_scan = ["QB", "RB", "WR", "TE", "K", "D/ST"]
    rostered_names = get_all_rostered_names(league)
    fa_pool = []
    size = st.slider(
        "FA pool per position to consider",
        10,
        150,
        40,
        step=10,
        key="whatif_pool_size",
    )

    for pos in positions_to_scan:
        f = []
        try:
            try:
                f = league.free_agents(position=pos, size=size)
            except Exception:
                if pos == "D/ST":
                    f = league.free_agents(position="DST", size=size)
        except Exception:
            f = []
        if not f:
            key = {"QB": "qb", "RB": "rb", "WR": "wr", "TE": "te", "K": "k",
                   "D/ST": "dst"}[pos]
            df = fp_weekly.get(key, pd.DataFrame())
            if not df.empty and "FPTS" in df.columns:
                df = df[~df["Player"].isin(rostered_names)].copy()
                df["FPTS_num"] = pd.to_numeric(
                    df["FPTS"], errors="coerce"
                ).fillna(0.0)
                df.sort_values("FPTS_num", ascending=False, inplace=True)
                df = df.head(size)
                f = [
                    FPPlayer(
                        r["Player"],
                        pos,
                        team=r.get("FP_Team", "N/A"),
                        bye=r.get("FP_Bye", "N/A"),
                    )
                    for _, r in df.iterrows()
                ]
        fa_pool.extend(f)

    names = [
        f"{p.name} — {getattr(p, 'position', '')} "
        f"({get_proj_week(p):.1f} wk / {get_ros_fp(p):.1f} ROS-FP)"
        for p in fa_pool
    ]
    pick = st.selectbox(
        "Free agent to add", options=["— pick a player —"] + names, key="whatif_pick"
    )
    drop_opts = ["(auto choose best drop)"] + [
        f"{p.name} — {p.position}" for p in my_team.roster
    ]
    drop_sel = st.selectbox(
        "Who would you drop?", options=drop_opts, key="whatif_drop"
    )

    if pick and pick != "— pick a player —":
        fa = fa_pool[names.index(pick)]
        if drop_sel == "(auto choose best drop)":
            current_lineup, current_bench = build_optimizer(
                my_team.roster, starting_slots
            )
            pool = current_bench or my_team.roster
            drop = sorted(
                pool, key=lambda p: (get_ros_fp(p), get_proj_week(p))
            )[0]
        else:
            drop_name = drop_sel.split(" — ")[0]
            drop = next((p for p in my_team.roster if p.name == drop_name), None)

        hypo_roster = [p for p in my_team.roster if p != drop] + [fa]

        cur_lineup, _ = build_optimizer(my_team.roster, starting_slots)
        new_lineup, _ = build_optimizer(hypo_roster, starting_slots)

        def _total(lineup):
            w = sum(get_proj_week(p) for plist in lineup.values() for p in plist)
            re = sum(get_ros_espn(p) for plist in lineup.values() for p in plist)
            rf = sum(get_ros_fp(p) for plist in lineup.values() for p in plist)
            return w, re, rf

        cur_w, cur_re, cur_rf = _total(cur_lineup)
        new_w, new_re, new_rf = _total(new_lineup)

        st.markdown("#### Result")
        st.write(
            f"**Weekly**: {new_w:.1f} ({new_w - cur_w:+.1f}) | "
            f"**ROS ESPN**: {new_re:.1f} ({new_re - cur_re:+.1f}) | "
            f"**ROS FP**: {new_rf:.1f} ({new_rf - cur_rf:+.1f})"
        )
        st.caption(f"Drop: **{getattr(drop, 'name', 'N/A')}** → Add: **{fa.name}**")

        col1, col2 = st.columns(2)
        with col1:
            st.markdown("**Current lineup**")
            for slot, plist in cur_lineup.items():
                for p in plist:
                    st.write(f"{slot}: {p.name} — {get_proj_week(p):.1f}")
        with col2:
            st.markdown("**What-if lineup**")
            for slot, plist in new_lineup.items():
                for p in plist:
                    st.write(f"{slot}: {p.name} — {get_proj_week(p):.1f}")

