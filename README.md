https://ebfantasyfootball.streamlit.app/

https://share.streamlit.io/?utm_source=streamlit&utm_medium=referral&utm_campaign=main&utm_content=-ss-streamlit-io-topright
# Fantasy Football Weekly Starter Optimizer 🏈

A Streamlit dashboard that connects to your **ESPN Fantasy Football** league to:
- Optimize your **weekly starting lineup**
- Analyze **team-to-team trades** (This Week + ROS)
- View **advanced player stats** with charts
- Log weekly performance
- Use **ESPN projections** or **FantasyPros projections** (fallback or FP-only)

---

## ✨ Features

- **Optimizer:** Best lineup by projections with FLEX logic (RB/WR/TE)
- **Trade Analyzer:** Multi-player, two-team trades with **This Week** and **ROS** net gains
- **Advanced Stats:** Table + grouped bar chart comparing **This Week** vs **ROS**
- **Projection Sources:**
  - `ESPN only`
  - `FantasyPros fallback` (default)
  - `FantasyPros only`
- **Logs:** Save weekly totals to `performance_log.csv`

---

## 🧱 Requirements

`requirements.txt` should include:
streamlit
espn-api
pandas
altair
requests
beautifulsoup4
