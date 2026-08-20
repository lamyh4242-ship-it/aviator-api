from fastapi import FastAPI, Query, HTTPException
from fastapi.responses import HTMLResponse
import httpx, asyncio, random, sqlite3, math
from datetime import datetime, timedelta
from typing import Optional, List, Dict, Any

app = FastAPI()

# ------------------- CONFIGURATION DES LIGUES -------------------
LEAGUES = {
    "English League": {"event_id": "161777", "parent_id": 8035},
    "Champions League": {"event_id": "161771", "parent_id": 8056},
    "CAN": {"event_id": "161778", "parent_id": 8060},
    "Coupe du Monde": {"event_id": "161758", "parent_id": 8065},
    "Spanish League": {"event_id": "161775", "parent_id": 8037},
    "Italie League": {"event_id": "161776", "parent_id": 8036},
    "French League": {"event_id": "161782", "parent_id": 8042},
    "German League": {"event_id": "161780", "parent_id": 8043},
    "Portugal League": {"event_id": "161781", "parent_id": 8044},
}

# ------------------- CONSTANTES -------------------
USER_AGENTS = [
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/151.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.4 Safari/605.1.15",
    "Mozilla/5.0 (iPhone; CPU iPhone OS 17_4 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.4 Mobile/15E148 Safari/604.1",
]

BASE_URL = "https://hg-event-api-prod.sporty-tech.net/api/instantleagues"
DB_FILENAME = "history.db"
GAP_THRESHOLD = 40 # Nombre de matchs sans un score rare avant alerte
STREAK_THRESHOLD = 5 # Victoires consécutives avant alerte de rupture
POISSON_OVER_25_THRESHOLD = 0.75 # Probabilité seuil pour afficher "Match ouvert"

# Mapping statique des noms d'équipes (fallback si le JSON ne fournit pas les noms)
# Format : "match_id" ou identifiant d'équipe -> nom
TEAM_NAME_MAP = {
    # Exemple : "123456": "Leeds United"
}

# ------------------- BASE DE DONNÉES -------------------
def init_db():
    with sqlite3.connect(DB_FILENAME) as conn:
        # Table des matchs (avec équipes)
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
        # Table pour mémoriser la dernière ronde active par ligue
        conn.execute("""
            CREATE TABLE IF NOT EXISTS last_rounds (
                league_name TEXT PRIMARY KEY,
                last_round INTEGER NOT NULL
            )
        """)
        conn.commit()

def save_matches(league_name: str, round_num: int, match_data: List[Dict[str, Any]]):
    """Enregistre les matchs d'une ronde avec noms d'équipes."""
    timestamp = datetime.utcnow().isoformat()
    with sqlite3.connect(DB_FILENAME) as conn:
        for m in match_data:
            conn.execute(
                """
                INSERT INTO matches (match_id, league_name, round_num, home_team, away_team,
                                     home_score, away_score, timestamp)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(match_id, league_name) DO UPDATE SET
                    home_team = excluded.home_team,
                    away_team = excluded.away_team,
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
        # Met à jour la dernière ronde
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

def get_history(league_name: str, limit: int = 50) -> List[Dict[str, Any]]:
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
            return {"status_http": r.status_code, "data": data, "url": str(r.url), "headers": r.headers}
        except Exception as e:
            if attempt == retries - 1:
                return {"error": str(e)}
            await asyncio.sleep(1)
    return {"error": "Échec après plusieurs tentatives"}

def extract_team_names(match: Dict[str, Any]) -> tuple[Optional[str], Optional[str]]:
    """
    Essaie d'extraire les noms d'équipes depuis le JSON.
    Cherche dans plusieurs structures possibles.
    """
    home = away = None
    # Cas 1 : homeTeam / awayTeam
    if "homeTeam" in match and isinstance(match["homeTeam"], dict):
        home = match["homeTeam"].get("name")
    if "awayTeam" in match and isinstance(match["awayTeam"], dict):
        away = match["awayTeam"].get("name")
    # Cas 2 : homeCompetitor / awayCompetitor
    if not home and "homeCompetitor" in match and isinstance(match["homeCompetitor"], dict):
        home = match["homeCompetitor"].get("name")
    if not away and "awayCompetitor" in match and isinstance(match["awayCompetitor"], dict):
        away = match["awayCompetitor"].get("name")
    # Cas 3 : home / away (objets)
    if not home and "home" in match and isinstance(match["home"], dict):
        home = match["home"].get("name")
    if not away and "away" in match and isinstance(match["away"], dict):
        away = match["away"].get("name")
    # Cas 4 : team1 / team2
    if not home and "team1" in match and isinstance(match["team1"], dict):
        home = match["team1"].get("name")
    if not away and "team2" in match and isinstance(match["team2"], dict):
        away = match["team2"].get("name")
    # Fallback : dictionnaire statique
    match_id = str(match.get("id", ""))
    if not home and match_id in TEAM_NAME_MAP:
        home = TEAM_NAME_MAP[match_id]
    if not away and match_id in TEAM_NAME_MAP:
        away = TEAM_NAME_MAP[match_id] # à ajuster si mapping différent
    return home, away

def parse_scores(data) -> List[Dict[str, Any]]:
    """Transforme le JSON de /playout en liste de matchs avec scores et noms."""
    matches = data.get("matches", [])
    result = []
    for match in matches:
        # Score final = dernier élément des goals
        goals = match.get("goals", [])
        if goals:
            last_goal = goals[-1]
            home_score = int(last_goal["homeScore"])
            away_score = int(last_goal["awayScore"])
        else:
            home_score = 0
            away_score = 0

        home_team, away_team = extract_team_names(match)

        result.append({
            "match_id": match.get("id"),
            "home_team": home_team,
            "away_team": away_team,
            "home_score": home_score,
            "away_score": away_score,
            "expected_start": match.get("expectedStart")
        })
    return result

async def find_active_round(client, event_id: str, parent_id: int, start_round: int = 1, max_round: int = 200):
    """
    Cherche la ronde active en partant de start_round.
    Optimisation : si start_round est None, on utilise la dernière ronde connue.
    """
    for round_num in range(start_round, max_round + 1):
        url = f"{BASE_URL}/round/{round_num}/playout"
        params = {"eventCategoryId": event_id, "parentEventCategoryId": parent_id}
        result = await fetch_json(client, url, params)
        if result.get("status_http") == 200 and result.get("data"):
            return round_num
        if result.get("status_http") == 403:
            break
        await asyncio.sleep(0.2)
    return None

async def get_scores_for_round(client, event_id: str, parent_id: int, round_num: int) -> Optional[List[Dict[str, Any]]]:
    url = f"{BASE_URL}/round/{round_num}/playout"
    params = {"eventCategoryId": event_id, "parentEventCategoryId": parent_id}
    result = await fetch_json(client, url, params)
    if result.get("status_http") == 200 and result.get("data"):
        return parse_scores(result["data"])
    return None

# ------------------- FONCTIONS DE PRÉDICTION -------------------
def poisson_over_25_probability(avg_goals: float) -> float:
    """Calcule la probabilité P(X >= 3) pour une loi de Poisson de paramètre lambda."""
    if avg_goals <= 0:
        return 0.0
    # P(X < 3) = P(0) + P(1) + P(2)
    p0 = math.exp(-avg_goals)
    p1 = avg_goals * p0
    p2 = (avg_goals ** 2 / 2) * p0
    return 1 - (p0 + p1 + p2)

def check_rare_scores_gap(history: List[Dict[str, Any]], current_scores: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """
    Détecte les scores rares (ex: 4-0, 3-3, 4-1, etc.) non apparus depuis longtemps.
    Retourne une liste d'alertes globales.
    """
    rare_scores = [
        (4, 0), (0, 4), (3, 3), (4, 1), (1, 4),
        (5, 0), (0, 5), (4, 2), (2, 4), (3, 4), (4, 3)
    ]
    # Compteur du nombre de matchs depuis la dernière apparition de chaque score rare
    last_seen = {score: None for score in rare_scores}
    # On parcourt l'historique du plus récent au plus ancien
    for i, match in enumerate(history):
        score = (match["home_score"], match["away_score"])
        if score in last_seen and last_seen[score] is None:
            last_seen[score] = i
    alerts = []
    for score, gap in last_seen.items():
        if gap is not None and gap >= GAP_THRESHOLD:
            home_goals, away_goals = score
            alerts.append({
                "type": "rare_score_gap",
                "message": f"🔥 Aucun score exact {home_goals}-{away_goals} depuis {gap} matchs. Tentez le score exact {home_goals}-{away_goals} ou {away_goals}-{home_goals}."
            })
    return alerts

def check_team_streaks(history: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """
    Détecte les séries de victoires consécutives par équipe.
    Retourne des alertes si une équipe a gagné STREAK_THRESHOLD matchs d'affilée.
    """
    if not history:
        return []
    # On a besoin des noms d'équipes
    if not history[0].get("home_team") or not history[0].get("away_team"):
        return []
    streaks = {}
    last_team = None
    count = 0
    # Parcours dans l'ordre chronologique (du plus ancien au plus récent)
    for match in reversed(history):
        if match["home_score"] > match["away_score"]:
            winner = match["home_team"]
        elif match["home_score"] < match["away_score"]:
            winner = match["away_team"]
        else:
            winner = None
        if winner == last_team:
            count += 1
        else:
            last_team = winner
            count = 1 if winner else 0
        if winner and count >= STREAK_THRESHOLD:
            streaks[winner] = count
    alerts = []
    for team, streak in streaks.items():
        alerts.append({
            "type": "team_streak",
            "message": f"🚨 Rupture statistique possible : {team} a gagné {streak} matchs d'affilée. Pariez sur une défaite surprise ou qu'ils encaissent le premier but (Cote 15+)."
        })
    return alerts

def generate_predictions(league: str, current_scores: List[Dict[str, Any]]) -> Dict[str, Any]:
    """
    Calcule toutes les prédictions pour la ronde en cours.
    Retourne un dictionnaire avec des alertes globales et par match.
    """
    history = get_history(league, limit=200) # Historique plus large
    if not history:
        return {"global": [], "per_match": []}

    # Moyennes globales
    total_goals = sum(m["home_score"] + m["away_score"] for m in history)
    avg_goals = total_goals / len(history)
    recent_5 = history[:5]
    recent_avg = sum(m["home_score"] + m["away_score"] for m in recent_5) / len(recent_5)

    global_alerts = []

    # Alerte Under/Over basée sur moyenne
    if avg_goals < 2.0:
        global_alerts.append({
            "type": "under",
            "message": f"Moyenne faible sur {len(history)} matchs ({avg_goals:.1f} buts/match) → Pensez aux paris Under 2.5."
        })
    elif avg_goals > 3.5:
        global_alerts.append({
            "type": "over",
            "message": f"Moyenne élevée ({avg_goals:.1f} buts/match) → Pensez aux paris Over 2.5."
        })

    # Tendance récente
    if len(history) >= 5 and recent_avg < avg_goals * 0.7:
        global_alerts.append({
            "type": "trend_down",
            "message": f"Tendance à la baisse sur les 5 derniers matchs ({recent_avg:.1f} vs {avg_goals:.1f}) → Possible match serré."
        })

    # Écarts de scores rares
    global_alerts.extend(check_rare_scores_gap(history, current_scores))

    # Séries d'équipes
    global_alerts.extend(check_team_streaks(history))

    # Prédictions par match (Poisson pour +2.5 buts)
    per_match_predictions = []
    for match in current_scores:
        match_predictions = []
        # Calcul de la probabilité Over 2.5 pour ce match (utilisation de la moyenne globale)
        prob_over = poisson_over_25_probability(avg_goals)
        if prob_over > POISSON_OVER_25_THRESHOLD:
            match_predictions.append({
                "type": "open_match",
                "message": f"Prédiction : Match Ouvert (+2.5 buts) à {prob_over*100:.0f}%"
            })
        else:
            match_predictions.append({
                "type": "closed_match",
                "message": f"Prédiction : Match Fermé (-2.5 buts) à {(1-prob_over)*100:.0f}%"
            })

        # Alerte si le match actuel est à 0-0 ou 1 but (peut évoluer)
        total = match["home_score"] + match["away_score"]
        if total == 0:
            match_predictions.append({
                "type": "no_goal",
                "message": "0-0 pour l'instant → Under intéressant"
            })
        elif total <= 1:
            match_predictions.append({
                "type": "low_scoring",
                "message": "Score faible → Attention au nul"
            })

        per_match_predictions.append({
            "match_id": match["match_id"],
            "predictions": match_predictions
        })

    return {"global": global_alerts, "per_match": per_match_predictions}

# ------------------- INITIALISATION -------------------
@app.on_event("startup")
async def startup_event():
    init_db()

# ------------------- ENDPOINTS API -------------------
@app.get("/")
def root():
    return {"message": "API Tableau de Bord Bet261 V3 en ligne"}

@app.get("/api/leagues")
async def get_leagues():
    return [{"name": name} for name in LEAGUES.keys()]

@app.get("/api/dashboard")
async def api_dashboard(
    league: str = Query(..., description="Nom de la ligue"),
    start_round: Optional[int] = Query(None, description="Ronde de départ (sinon utilise la dernière connue)")
):
    if league not in LEAGUES:
        raise HTTPException(status_code=404, detail=f"Ligue '{league}' inconnue")

    league_info = LEAGUES[league]
    event_id = league_info["event_id"]
    parent_id = league_info["parent_id"]

    # Déterminer la ronde de départ
    if start_round is None:
        last_round = get_last_round(league)
        start_round = max(1, (last_round - 2) if last_round else 1)

    async with httpx.AsyncClient(follow_redirects=True) as client:
        active_round = await find_active_round(client, event_id, parent_id, start_round)
        if active_round is None:
            return {
                "league": league,
                "error": "Aucune ronde active trouvée",
                "status": "error"
            }

        match_data = await get_scores_for_round(client, event_id, parent_id, active_round)
        if match_data is None:
            return {
                "league": league,
                "round": active_round,
                "error": "Impossible de récupérer les scores",
                "status": "error"
            }

        # Sauvegarde dans l'historique
        save_matches(league, active_round, match_data)

        # Prédictions
        predictions = generate_predictions(league, match_data)

        # Ajouter l'heure estimée pour chaque match (si expectedStart absent)
        for m in match_data:
            if not m.get("expected_start") or m["expected_start"].startswith("0001"):
                m["display_time"] = f"Ronde {active_round} (en cours)"
            else:
                m["display_time"] = m["expected_start"]

        return {
            "league": league,
            "round": active_round,
            "matches": match_data,
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
        <title>Bet261 Virtual Dashboard V3</title>
        <style>
            body {
                font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif;
                background: #f0f2f5;
                margin: 0;
                padding: 15px;
                color: #1a1a1a;
            }
            .container {
                max-width: 500px;
                margin: 0 auto;
            }
            h1 {
                text-align: center;
                font-size: 24px;
                margin-bottom: 5px;
            }
            .subtitle {
                text-align: center;
                color: #666;
                font-size: 14px;
                margin-bottom: 15px;
            }
            select {
                width: 100%;
                padding: 12px;
                font-size: 16px;
                border-radius: 8px;
                border: 1px solid #ccc;
                margin-bottom: 15px;
                background: white;
            }
            .status {
                text-align: center;
                padding: 10px;
                border-radius: 8px;
                margin-bottom: 15px;
                font-weight: bold;
            }
            .status-ok {
                background: #d4edda;
                color: #155724;
            }
            .status-error {
                background: #f8d7da;
                color: #721c24;
            }
            .match-card {
                background: white;
                border-radius: 12px;
                padding: 15px;
                box-shadow: 0 2px 5px rgba(0,0,0,0.05);
                margin-bottom: 10px;
            }
            .match-header {
                display: flex;
                justify-content: space-between;
                align-items: center;
                margin-bottom: 8px;
                font-size: 14px;
                color: #555;
            }
            .team-names {
                font-size: 18px;
                font-weight: bold;
                text-align: center;
                margin: 8px 0;
            }
            .score {
                font-size: 28px;
                font-weight: bold;
                text-align: center;
                margin: 5px 0;
            }
            .prediction-badge {
                background: #fff3cd;
                color: #856404;
                border-radius: 6px;
                padding: 5px 10px;
                margin-top: 8px;
                font-size: 13px;
                border-left: 3px solid #ffc107;
            }
            .prediction-badge.danger {
                background: #f8d7da;
                color: #721c24;
                border-left-color: #dc3545;
            }
            .global-alerts {
                background: #f8f9fa;
                border-radius: 12px;
                padding: 15px;
                margin-bottom: 20px;
            }
            .global-alerts h2 {
                font-size: 18px;
                margin-top: 0;
                color: #333;
            }
            .alert-item {
                margin-bottom: 10px;
                padding: 10px;
                border-radius: 8px;
                background: white;
                box-shadow: 0 1px 3px rgba(0,0,0,0.05);
                font-size: 14px;
            }
            .refresh-info {
                text-align: center;
                font-size: 12px;
                color: #999;
                margin-top: 10px;
            }
        </style>
    </head>
    <body>
        <div class="container">
            <h1>⚽ Dashboard V3</h1>
            <div class="subtitle">Prédictions & Grosses Cotes</div>

            <select id="leagueSelect" onchange="loadDashboard()">
                <option value="">-- Choisir une ligue --</option>
            </select>

            <div id="statusMessage" class="status" style="display:none;"></div>

            <div id="globalAlertsContainer"></div>
            <div id="matchesContainer"></div>

            <div class="refresh-info">Actualisation automatique toutes les 10 secondes</div>
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
                statusEl.className = 'status';

                try {
                    const response = await fetch(`/api/dashboard?league=${encodeURIComponent(league)}`);
                    const data = await response.json();

                    if (data.status === 'ok') {
                        statusEl.style.display = 'block';
                        statusEl.className = 'status status-ok';
                        statusEl.textContent = `✅ Ronde ${data.round} - ${data.matches.length} matchs`;

                        // Alertes globales
                        let globalHtml = '';
                        if (data.global_predictions.length > 0) {
                            globalHtml = '<div class="global-alerts"><h2>🚨 Alertes Globales</h2>';
                            data.global_predictions.forEach(alert => {
                                globalHtml += `<div class="alert-item">${alert.message}</div>`;
                            });
                            globalHtml += '</div>';
                        }
                        document.getElementById('globalAlertsContainer').innerHTML = globalHtml;

                        // Matchs
                        const matchesHtml = data.matches.map(match => {
                            const teamDisplay = match.home_team && match.away_team
                                ? `${match.home_team} vs ${match.away_team}`
                                : `Match #${match.match_id}`;
                            const score = `${match.home_score} - ${match.away_score}`;
                            const time = match.display_time || '';

                            // Prédictions pour ce match
                            const matchPreds = data.per_match_predictions.find(p => p.match_id === match.match_id);
                            let predsHtml = '';
                            if (matchPreds) {
                                matchPreds.predictions.forEach(pred => {
                                    predsHtml += `<div class="prediction-badge ${pred.type === 'rare_score_gap' || pred.type === 'team_streak' ? 'danger' : ''}">${pred.message}</div>`;
                                });
                            }

                            return `
                                <div class="match-card">
                                    <div class="match-header">
                                        <span>${time}</span>
                                        <span>#${match.match_id}</span>
                                    </div>
                                    <div class="team-names">${teamDisplay}</div>
                                    <div class="score">${score}</div>
                                    ${predsHtml}
                                </div>
                            `;
                        }).join('');
                        document.getElementById('matchesContainer').innerHTML = matchesHtml;
                    } else {
                        statusEl.style.display = 'block';
                        statusEl.className = 'status status-error';
                        statusEl.textContent = `❌ ${data.error || 'Erreur inconnue'}`;
                        document.getElementById('globalAlertsContainer').innerHTML = '';
                        document.getElementById('matchesContainer').innerHTML = '';
                    }
                } catch (err) {
                    statusEl.style.display = 'block';
                    statusEl.className = 'status status-error';
                    statusEl.textContent = `❌ Erreur réseau : ${err.message}`;
                }
            }

            loadLeagues();
        </script>
    </body>
    </html>
    """
    return HTMLResponse(html)
