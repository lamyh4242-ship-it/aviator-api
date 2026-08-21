from fastapi import FastAPI, Query, HTTPException
from fastapi.responses import HTMLResponse
import httpx, asyncio, random, sqlite3, math
from datetime import datetime, timedelta
from typing import Optional, List, Dict, Any, Tuple
try:
    from zoneinfo import ZoneInfo
except ImportError:
    import pytz
    ZoneInfo = pytz.timezone

app = FastAPI()

# ------------------- CONFIGURATION DES LIGUES -------------------
LEAGUES = {
    "English League": {"event_id": "161860", "parent_id": 8035},
    "Champions League": {"event_id": "161771", "parent_id": 8056},
    "CAN": {"event_id": "161778", "parent_id": 8060},
    "Coupe du Monde": {"event_id": "161758", "parent_id": 8065},
    "Spanish League": {"event_id": "161775", "parent_id": 8037},
    "Italie League": {"event_id": "161776", "parent_id": 8036},
    "French League": {"event_id": "161782", "parent_id": 8042},
    "German League": {"event_id": "161780", "parent_id": 8043},
    "Portugal League": {"event_id": "161781", "parent_id": 8044},
}

TEAM_NAME_MAP = {}

# ------------------- CONSTANTES -------------------
USER_AGENTS = [
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/151.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.4 Safari/605.1.15",
    "Mozilla/5.0 (iPhone; CPU iPhone OS 17_4 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.4 Mobile/15E148 Safari/604.1",
]

BASE_URL = "https://hg-event-api-prod.sporty-tech.net/api/instantleagues"
DB_FILENAME = "history.db"
POISSON_OVER_25_THRESHOLD = 0.70
RARE_SCORES = [(4,0), (0,4), (3,3), (4,1), (1,4), (5,0), (0,5), (4,2), (2,4), (3,4), (4,3)]
GAP_THRESHOLD = 40
STREAK_NO_GOALS_AGAINST = 4
STREAK_NO_DRAW = 5
STREAK_WINS = 5

# ------------------- BASE DE DONNÉES -------------------
def init_db():
    with sqlite3.connect(DB_FILENAME) as conn:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS matches (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                match_id TEXT NOT NULL,
                league_name TEXT NOT NULL,
                round_num INTEGER NOT NULL,
                home_team TEXT,
                away_team TEXT,
                home_score INTEGER NOT NULL,
                away_score INTEGER NOT NULL,
                timestamp TEXT NOT NULL,
                UNIQUE(match_id, league_name)
            )
        """)
        conn.execute("""
            CREATE TABLE IF NOT EXISTS last_rounds (
                league_name TEXT PRIMARY KEY,
                last_round INTEGER NOT NULL
            )
        """)
        conn.commit()

def save_matches(league_name: str, round_num: int, match_data: List[Dict[str, Any]]):
    timestamp = datetime.utcnow().isoformat()
    with sqlite3.connect(DB_FILENAME) as conn:
        for m in match_data:
            conn.execute(
                """
                INSERT INTO matches (match_id, league_name, round_num, home_team, away_team,
                                     home_score, away_score, timestamp)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(match_id, league_name) DO UPDATE SET
                    home_team = COALESCE(excluded.home_team, matches.home_team),
                    away_team = COALESCE(excluded.away_team, matches.away_team),
                    home_score = excluded.home_score,
                    away_score = excluded.away_score,
                    timestamp = excluded.timestamp
                """,
                (
                    str(m["match_id"]), league_name, round_num,
                    m.get("home_team"), m.get("away_team"),
                    m["home_score"], m["away_score"], timestamp
                )
            )
        conn.execute(
            "INSERT INTO last_rounds (league_name, last_round) VALUES (?, ?) "
            "ON CONFLICT(league_name) DO UPDATE SET last_round = excluded.last_round",
            (league_name, round_num)
        )
        conn.commit()

def get_last_round(league_name: str) -> Optional[int]:
    with sqlite3.connect(DB_FILENAME) as conn:
        row = conn.execute(
            "SELECT last_round FROM last_rounds WHERE league_name = ?",
            (league_name,)
        ).fetchone()
        return row[0] if row else None

def get_history(league_name: str, limit: int = 200) -> List[Dict[str, Any]]:
    with sqlite3.connect(DB_FILENAME) as conn:
        rows = conn.execute(
            """
            SELECT match_id, round_num, home_team, away_team, home_score, away_score, timestamp
            FROM matches
            WHERE league_name = ?
            ORDER BY timestamp DESC
            LIMIT ?
            """,
            (league_name, limit)
        ).fetchall()
        return [
            {
                "match_id": r[0],
                "round_num": r[1],
                "home_team": r[2],
                "away_team": r[3],
                "home_score": r[4],
                "away_score": r[5],
                "timestamp": r[6]
            }
            for r in rows
        ]

# ------------------- FONCTIONS UTILITAIRES -------------------
def build_headers():
    return {
        "User-Agent": random.choice(USER_AGENTS),
        "App-Version": "34727",
        "Origin": "https://bet261.mg",
        "Referer": "https://bet261.mg/",
        "Accept": "application/json, text/plain, */*",
        "Accept-Language": "fr-FR,fr;q=0.9,en;q=0.8",
        "Sec-Fetch-Site": "same-origin",
        "Sec-Fetch-Mode": "cors",
        "Sec-Fetch-Dest": "empty",
    }

async def fetch_json(client, url, params=None, retries=3):
    for attempt in range(retries):
        try:
            r = await client.get(url, params=params, headers=build_headers(), timeout=15.0)
            if r.status_code in (429, 500, 502, 503):
                await asyncio.sleep(2 ** attempt)
                continue
            try:
                data = r.json()
            except Exception:
                data = r.text[:2000]
            return {"status_http": r.status_code, "data": data, "url": str(r.url)}
        except Exception as e:
            if attempt == retries - 1:
                return {"error": str(e)}
            await asyncio.sleep(1)
    return {"error": "Échec après plusieurs tentatives"}

def extract_team_names_from_obj(obj: Dict[str, Any]) -> Tuple[Optional[str], Optional[str]]:
    home = away = None
    if isinstance(obj.get("homeTeam"), dict):
        home = obj["homeTeam"].get("name") or obj["homeTeam"].get("teamName")
    if isinstance(obj.get("awayTeam"), dict):
        away = obj["awayTeam"].get("name") or obj["awayTeam"].get("teamName")
    if not home and isinstance(obj.get("home"), dict):
        home = obj["home"].get("name") or obj["home"].get("teamName")
    if not away and isinstance(obj.get("away"), dict):
        away = obj["away"].get("name") or obj["away"].get("teamName")
    if "teams" in obj and isinstance(obj["teams"], list) and len(obj["teams"]) >= 2:
        t1, t2 = obj["teams"][0], obj["teams"][1]
        home = t1.get("name") or t1.get("teamName") or home
        away = t2.get("name") or t2.get("teamName") or away
    if "competitors" in obj and isinstance(obj["competitors"], list):
        for comp in obj["competitors"]:
            if isinstance(comp, dict):
                name = comp.get("name") or comp.get("teamName")
                if name and "home" in comp:
                    if comp["home"]: home = name
                    else: away = name
                elif name:
                    if home is None: home = name
                    elif away is None: away = name
    if not home:
        home = obj.get("homeTeamName") or obj.get("home_name") or obj.get("homeName") or obj.get("home_team")
    if not away:
        away = obj.get("awayTeamName") or obj.get("away_name") or obj.get("awayName") or obj.get("away_team")
    return home, away

def extract_start_time(obj: Dict[str, Any]) -> Optional[str]:
    for key in ["startTime", "date", "scheduledStart", "eventStart", "expectedStart", "start"]:
        val = obj.get(key)
        if val and str(val) != "0001-01-01T00:00:00Z":
            return val
    return None

def parse_scores(data) -> List[Dict[str, Any]]:
    matches = data.get("matches", [])
    result = []
    for match in matches:
        goals = match.get("goals", [])
        if goals:
            last_goal = goals[-1]
            home_score = int(last_goal["homeScore"])
            away_score = int(last_goal["awayScore"])
        else:
            home_score = 0
            away_score = 0
        result.append({
            "match_id": match.get("id"),
            "home_score": home_score,
            "away_score": away_score,
            "home_team": None,
            "away_team": None,
            "start_time": None,
        })
    return result

async def fetch_schedule_mapping(client, event_id: str, parent_id: int, round_num: int) -> Dict[str, Dict[str, Any]]:
    url = f"{BASE_URL}/round/{round_num}"
    params = {"eventCategoryId": event_id, "getNext": "false"}
    result = await fetch_json(client, url, params)

    if result.get("status_http") != 200 or not result.get("data"):
        return {}

    data = result["data"]
    if isinstance(data, dict) and "data" in data:
        data = data["data"]

    matches = data if isinstance(data, list) else data.get("events") or data.get("matches") or data.get("fixtures") or []
    if not isinstance(matches, list):
        return {}

    mapping = {}
    for m in matches:
        mid = m.get("id") or m.get("matchId") or m.get("eventId")
        if not mid:
            continue
        home, away = extract_team_names_from_obj(m)
        start = extract_start_time(m)
        mapping[str(mid)] = {"home_team": home, "away_team": away, "start_time": start}
    return mapping

async def get_future_matches(client, event_id: str, parent_id: int, round_num: int) -> List[Dict[str, Any]]:
    mapping = await fetch_schedule_mapping(client, event_id, parent_id, round_num)
    matches = []
    for mid, info in mapping.items():
        matches.append({
            "match_id": mid,
            "home_team": info["home_team"],
            "away_team": info["away_team"],
            "home_score": 0,
            "away_score": 0,
            "start_time": info["start_time"]
        })
    return matches

async def find_active_round(client, event_id: str, parent_id: int, start_round: int = 1, max_round: int = 200):
    for round_num in range(start_round, max_round + 1):
        url = f"{BASE_URL}/round/{round_num}/playout"
        params = {"eventCategoryId": event_id, "parentEventCategoryId": parent_id}
        result = await fetch_json(client, url, params)
        if result.get("status_http") == 200 and result.get("data"):
            return round_num
        if result.get("status_http") == 403:
            break
        await asyncio.sleep(0.1)
    return None

async def get_scores_for_round(client, event_id: str, parent_id: int, round_num: int) -> Optional[List[Dict[str, Any]]]:
    mapping = await fetch_schedule_mapping(client, event_id, parent_id, round_num)
    url = f"{BASE_URL}/round/{round_num}/playout"
    params = {"eventCategoryId": event_id, "parentEventCategoryId": parent_id}
    result = await fetch_json(client, url, params)

    if result.get("status_http") != 200 or not result.get("data"):
        return None

    scores = parse_scores(result["data"])
    for match in scores:
        mid = str(match["match_id"])
        if mid in mapping:
            match["home_team"] = mapping[mid].get("home_team") or match["home_team"]
            match["away_team"] = mapping[mid].get("away_team") or match["away_team"]
            match["start_time"] = mapping[mid].get("start_time") or match["start_time"]

    return scores
    # ------------------- STATISTIQUES & PREDICTIONS -------------------
def compute_team_stats(history: List[Dict[str, Any]]) -> Dict[str, Dict[str, float]]:
    stats = {}
    for match in history:
        if not match.get("home_team") or not match.get("away_team"):
            continue
        home_team, away_team = match["home_team"], match["away_team"]
        home_goals, away_goals = match["home_score"], match["away_score"]

        if home_team not in stats:
            stats[home_team] = {"scored_home": [], "conceded_home": [], "scored_away": [], "conceded_away": []}
        stats[home_team]["scored_home"].append(home_goals)
        stats[home_team]["conceded_home"].append(away_goals)

        if away_team not in stats:
            stats[away_team] = {"scored_home": [], "conceded_home": [], "scored_away": [], "conceded_away": []}
        stats[away_team]["scored_away"].append(away_goals)
        stats[away_team]["conceded_away"].append(home_goals)

    for team, s in stats.items():
        s["avg_scored_home"] = sum(s["scored_home"]) / len(s["scored_home"]) if s["scored_home"] else 0
        s["avg_conceded_home"] = sum(s["conceded_home"]) / len(s["conceded_home"]) if s["conceded_home"] else 0
        s["avg_scored_away"] = sum(s["scored_away"]) / len(s["scored_away"]) if s["scored_away"] else 0
        s["avg_conceded_away"] = sum(s["conceded_away"]) / len(s["conceded_away"]) if s["conceded_away"] else 0
    return stats

def poisson_prob(lam: float, k: int) -> float:
    return math.exp(-lam) * (lam ** k) / math.factorial(k)

def over_25_probability(lambda_total: float) -> float:
    return 1 - (poisson_prob(lambda_total, 0) + poisson_prob(lambda_total, 1) + poisson_prob(lambda_total, 2))

def exact_score_probability(lambda_home: float, lambda_away: float, h: int, a: int) -> float:
    return poisson_prob(lambda_home, h) * poisson_prob(lambda_away, a)

def check_team_streaks(history: List[Dict[str, Any]]) -> List[Dict[str, str]]:
    if not history or not history[0].get("home_team"): return []
    chronological = list(reversed(history))
    alerts, teams = [], {}

    for match in chronological:
        home_team, away_team = match["home_team"], match["away_team"]
        home_goals, away_goals = match["home_score"], match["away_score"]

        for team, scored, conceded in [(home_team, home_goals, away_goals), (away_team, away_goals, home_goals)]:
            if team not in teams:
                teams[team] = {"wins": 0, "losses": 0, "draws": 0, "current_streak_win": 0, "current_streak_draw": 0, "current_streak_no_goal_against": 0}

            res = "win" if scored > conceded else "loss" if scored < conceded else "draw"

            if res == "win":
                teams[team]["current_streak_win"] += 1
                teams[team]["current_streak_draw"] = 0
            elif res == "draw":
                teams[team]["current_streak_draw"] += 1
                teams[team]["current_streak_win"] = 0
            else:
                teams[team]["current_streak_win"] = 0
                teams[team]["current_streak_draw"] = 0

            if conceded == 0: teams[team]["current_streak_no_goal_against"] += 1
            else: teams[team]["current_streak_no_goal_against"] = 0

            if teams[team]["current_streak_win"] >= STREAK_WINS:
                alerts.append({"type": "win_streak", "message": f"🚨 Rupture : {team} sur {teams[team]['current_streak_win']} victoires consécutives. Envisager défaite/but encaissé."})
            if teams[team]["current_streak_no_goal_against"] >= STREAK_NO_GOALS_AGAINST:
                alerts.append({"type": "no_goals_against", "message": f"🛡️ {team} clean-sheet depuis {teams[team]['current_streak_no_goal_against']} matchs. Risque de but encaissé élevé."})
            if teams[team]["current_streak_draw"] >= STREAK_NO_DRAW:
                alerts.append({"type": "no_draw_streak", "message": f"⚖️ {team} sans nul depuis {teams[team]['current_streak_draw']} matchs. Reconstitution de série vers X."})

    seen, unique_alerts = set(), []
    for alert in alerts:
        if alert["message"] not in seen:
            seen.add(alert["message"])
            unique_alerts.append(alert)
    return unique_alerts

def check_rare_scores_gap(history: List[Dict[str, Any]]) -> List[Dict[str, str]]:
    if not history: return []
    last_seen = {score: None for score in RARE_SCORES}
    for i, match in enumerate(history):
        score = (match["home_score"], match["away_score"])
        if score in last_seen and last_seen[score] is None:
            last_seen[score] = i
    alerts = []
    for score, gap in last_seen.items():
        if gap is not None and gap >= GAP_THRESHOLD:
            h, a = score
            alerts.append({"type": "rare_score_gap", "message": f"🔥 Absence de score {h}-{a} depuis {gap} matchs. Tenter score exact {h}-{a} / {a}-{h}."})
    return alerts

def generate_predictions(league: str, current_matches: List[Dict[str, Any]]) -> Dict[str, Any]:
    history = get_history(league, limit=200)
    if not history: return {"global": [], "per_match": []}

    team_stats = compute_team_stats(history)
    total_goals = sum(m["home_score"] + m["away_score"] for m in history)
    avg_goals = total_goals / len(history) if history else 2.5

    global_alerts = []
    if avg_goals < 2.0: global_alerts.append({"type": "under", "message": f"Tendance défensive ({avg_goals:.1f} buts/m) → Privilégier Under 2.5."})
    elif avg_goals > 3.5: global_alerts.append({"type": "over", "message": f"Tendance offensive ({avg_goals:.1f} buts/m) → Privilégier Over 2.5."})

    global_alerts.extend(check_team_streaks(history))
    global_alerts.extend(check_rare_scores_gap(history))

    per_match = []
    for match in current_matches:
        match_predictions = []
        home_team, away_team = match.get("home_team"), match.get("away_team")

        if home_team and away_team and home_team in team_stats and away_team in team_stats:
            home_s, away_s = team_stats[home_team], team_stats[away_team]
            lambda_home = (home_s["avg_scored_home"] + away_s["avg_conceded_away"]) / 2
            lambda_away = (away_s["avg_scored_away"] + home_s["avg_conceded_home"]) / 2
            lambda_total = lambda_home + lambda_away
        else:
            lambda_total = avg_goals
            lambda_home = lambda_away = lambda_total / 2

        prob_over = over_25_probability(lambda_total)
        if prob_over > POISSON_OVER_25_THRESHOLD:
            match_predictions.append({"type": "open_match", "message": f"Prédiction : Match Ouvert (+2.5 buts) à {prob_over*100:.0f}%"})
        else:
            match_predictions.append({"type": "closed_match", "message": f"Prédiction : Match Fermé (-2.5 buts) à {(1-prob_over)*100:.0f}%"})

        for h, a in RARE_SCORES:
            prob = exact_score_probability(lambda_home, lambda_away, h, a)
            if prob > 0.02:
                match_predictions.append({"type": "exact_score", "message": f"🎯 Opportunité cote : Score exact {h}-{a} (Prob {prob*100:.1f}%)"})

        per_match.append({"match_id": match["match_id"], "predictions": match_predictions})

    return {"global": global_alerts, "per_match": per_match}
    # ------------------- INITIALISATION & ENDPOINTS -------------------
@app.on_event("startup")
async def startup_event():
    init_db()

@app.get("/")
def root():
    return {"message": "API Dashboard Bet261 V3.1 opérationnelle"}

@app.get("/api/leagues")
async def get_leagues():
    return [{"name": name} for name in LEAGUES.keys()]

@app.get("/api/dashboard")
async def api_dashboard(
    league: str = Query(..., description="Nom de la ligue"),
    start_round: Optional[int] = Query(None, description="Ronde de départ")
):
    if league not in LEAGUES:
        raise HTTPException(status_code=404, detail=f"Ligue '{league}' inconnue")

    league_info = LEAGUES[league]
    event_id, parent_id = league_info["event_id"], league_info["parent_id"]

    last_round = get_last_round(league)
    if start_round is None:
        start_round = max(1, (last_round - 1)) if last_round else 1

    async with httpx.AsyncClient(follow_redirects=True) as client:
        active_round = await find_active_round(client, event_id, parent_id, start_round, max_round=200)
        if active_round is None:
            active_round = await find_active_round(client, event_id, parent_id, 1, max_round=200)
            if active_round is None:
                return {"league": league, "error": "Aucune ronde active détectée", "status": "error"}

        current_match_data = await get_scores_for_round(client, event_id, parent_id, active_round)
        if current_match_data:
            save_matches(league, active_round, current_match_data)

        future_round = active_round + 1
        future_match_data = await get_future_matches(client, event_id, parent_id, future_round)

        if not future_match_data:
            return {"league": league, "round": future_round, "error": "Chargement du calendrier futur...", "status": "error"}

        predictions = generate_predictions(league, future_match_data)

        tz = ZoneInfo("Indian/Antananarivo")
        for m in future_match_data:
            if m.get("start_time"):
                try:
                    dt = datetime.fromisoformat(m["start_time"].replace("Z", "+00:00"))
                    m["display_time"] = dt.astimezone(tz).strftime("%H:%M")
                except:
                    m["display_time"] = str(m["start_time"])
            else:
                m["display_time"] = "À venir"

        return {
            "league": league,
            "round": future_round,
            "matches": future_match_data,
            "global_predictions": predictions["global"],
            "per_match_predictions": predictions["per_match"],
            "timestamp": datetime.utcnow().isoformat(),
            "status": "ok"
        }

# ------------------- INTERFACE WEB -------------------
@app.get("/dashboard")
async def web_dashboard():
    html = """
    <!DOCTYPE html>
    <html>
    <head>
        <meta charset="UTF-8">
        <meta name="viewport" content="width=device-width, initial-scale=1.0, user-scalable=no">
        <title>Dashboard V3.1 - Bet261</title>
        <style>
            body { font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif; background: #f0f2f5; margin: 0; padding: 15px; color: #1a1a1a; }
            .container { max-width: 500px; margin: 0 auto; }
            h1 { text-align: center; font-size: 24px; margin-bottom: 5px; }
            .subtitle { text-align: center; color: #666; font-size: 14px; margin-bottom: 15px; }
            select { width: 100%; padding: 12px; font-size: 16px; border-radius: 8px; border: 1px solid #ccc; margin-bottom: 15px; background: white; }
            .status { text-align: center; padding: 10px; border-radius: 8px; margin-bottom: 15px; font-weight: bold; }
            .status-ok { background: #d4edda; color: #155724; }
            .status-error { background: #f8d7da; color: #721c24; }
            .global-alerts { background: #f8f9fa; border-radius: 12px; padding: 15px; margin-bottom: 20px; }
            .global-alerts h2 { font-size: 18px; margin-top: 0; color: #333; }
            .alert-item { margin-bottom: 10px; padding: 10px; border-radius: 8px; background: white; box-shadow: 0 1px 3px rgba(0,0,0,0.05); font-size: 14px; border-left: 4px solid #ffc107; }
            .alert-item.danger { border-left-color: #dc3545; }
            .match-card { background: white; border-radius: 12px; padding: 15px; box-shadow: 0 2px 5px rgba(0,0,0,0.05); margin-bottom: 10px; }
            .match-header { display: flex; justify-content: space-between; align-items: center; margin-bottom: 8px; font-size: 14px; color: #555; }
            .team-names { font-size: 18px; font-weight: bold; text-align: center; margin: 8px 0; }
            .score { font-size: 22px; font-weight: bold; text-align: center; margin: 5px 0; color: #007bff; }
            .prediction-badge { background: #fff3cd; color: #856404; border-radius: 6px; padding: 5px 10px; margin-top: 8px; font-size: 13px; border-left: 3px solid #ffc107; }
            .prediction-badge.danger { background: #f8d7da; color: #721c24; border-left-color: #dc3545; }
            .refresh-info { text-align: center; font-size: 12px; color: #999; margin-top: 10px; }
        </style>
    </head>
    <body>
        <div class="container">
            <h1>⚽ Dashboard V3.1</h1>
            <div class="subtitle">Prédictions & Cotes d'Avant-Match</div>

            <select id="leagueSelect" onchange="loadDashboard()">
                <option value="">-- Choisir une ligue --</option>
            </select>

            <div id="statusMessage" class="status" style="display:none;"></div>
            <div id="globalAlertsContainer"></div>
            <div id="matchesContainer"></div>

            <div class="refresh-info">Mise à jour automatique (10s)</div>
        </div>

        <script>
            let refreshInterval = null;

            async function loadLeagues() {
                const response = await fetch('/api/leagues');
                const data = await response.json();
                const select = document.getElementById('leagueSelect');
                data.forEach(league => {
                    const option = document.createElement('option');
                    option.value = league.name;
                    option.textContent = league.name;
                    select.appendChild(option);
                });
            }

            async function loadDashboard() {
                const league = document.getElementById('leagueSelect').value;
                if (!league) return;

                if (refreshInterval) clearInterval(refreshInterval);
                refreshInterval = setInterval(loadDashboard, 10000);

                const statusEl = document.getElementById('statusMessage');
                statusEl.style.display = 'none';

                try {
                    const response = await fetch(`/api/dashboard?league=${encodeURIComponent(league)}`);
                    const data = await response.json();

                    if (data.status === 'ok') {
                        statusEl.style.display = 'block';
                        statusEl.className = 'status status-ok';
                        statusEl.textContent = `✅ Ronde Prochaine #${data.round} - ${data.matches.length} matchs à venir`;

                        let globalHtml = '';
                        if (data.global_predictions.length > 0) {
                            globalHtml = '<div class="global-alerts"><h2>🚨 Alertes Globales</h2>';
                            data.global_predictions.forEach(alert => {
                                globalHtml += `<div class="alert-item ${alert.type.includes('rare') || alert.type.includes('streak') ? 'danger' : ''}">${alert.message}</div>`;
                            });
                            globalHtml += '</div>';
                        }
                        document.getElementById('globalAlertsContainer').innerHTML = globalHtml;

                        const matchesHtml = data.matches.map(match => {
                            const teamDisplay = match.home_team && match.away_team
                                ? `${match.home_team} vs ${match.away_team}`
                                : `Match #${match.match_id}`;
                            const time = match.display_time || '';

                            const matchPreds = data.per_match_predictions.find(p => p.match_id === match.match_id);
                            let predsHtml = '';
                            if (matchPreds) {
                                matchPreds.predictions.forEach(pred => {
                                    predsHtml += `<div class="prediction-badge ${pred.type === 'exact_score' || pred.type === 'rare_score_gap' ? 'danger' : ''}">${pred.message}</div>`;
                                });
                            }

                            return `
                                <div class="match-card">
                                    <div class="match-header">
                                        <span>⏰ ${time}</span>
                                        <span>#${match.match_id}</span>
                                    </div>
                                    <div class="team-names">${teamDisplay}</div>
                                    <div class="score">VS</div>
                                    ${predsHtml}
                                </div>
                            `;
                        }).join('');
                        document.getElementById('matchesContainer').innerHTML = matchesHtml;
                    } else {
                        statusEl.style.display = 'block';
                        statusEl.className = 'status status-error';
                        statusEl.textContent = "❌ " + (data.error || "Erreur d'accès à la grille");
                        document.getElementById('globalAlertsContainer').innerHTML = '';
                        document.getElementById('matchesContainer').innerHTML = '';
                    }
                } catch (err) {
                    statusEl.style.display = 'block';
                    statusEl.className = 'status status-error';
                    statusEl.textContent = "❌ Erreur réseau : " + err.message;
                }
            }

            loadLeagues();
        </script>
    </body>
    </html>
    """
    return HTMLResponse(html)
