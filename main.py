from fastapi import FastAPI, Query, HTTPException
from fastapi.responses import HTMLResponse
import httpx, asyncio, random, sqlite3
from datetime import datetime
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

# ------------------- Autres constantes -------------------
USER_AGENTS = [
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/151.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.4 Safari/605.1.15",
    "Mozilla/5.0 (iPhone; CPU iPhone OS 17_4 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.4 Mobile/15E148 Safari/604.1",
]

BASE_URL = "https://hg-event-api-prod.sporty-tech.net/api/instantleagues"
DB_FILENAME = "history.db"

# ------------------- Base de données (pour l'historique) -------------------
def init_db():
    with sqlite3.connect(DB_FILENAME) as conn:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS matches (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                match_id TEXT NOT NULL,
                league_name TEXT NOT NULL,
                round_num INTEGER NOT NULL,
                home_score INTEGER NOT NULL,
                away_score INTEGER NOT NULL,
                timestamp TEXT NOT NULL,
                UNIQUE(match_id, league_name)
            )
        """)
        conn.commit()

def save_matches(league_name: str, round_num: int, scores: List[Dict[str, int]]):
    timestamp = datetime.utcnow().isoformat()
    with sqlite3.connect(DB_FILENAME) as conn:
        for score in scores:
            conn.execute(
                """
                INSERT INTO matches (match_id, league_name, round_num, home_score, away_score, timestamp)
                VALUES (?, ?, ?, ?, ?, ?)
                ON CONFLICT(match_id, league_name) DO UPDATE SET
                    home_score = excluded.home_score,
                    away_score = excluded.away_score,
                    timestamp = excluded.timestamp
                """,
                (str(score["match_id"]), league_name, round_num, score["home_score"], score["away_score"], timestamp)
            )
        conn.commit()

def get_history(league_name: str, limit: int = 50) -> List[Dict[str, Any]]:
    with sqlite3.connect(DB_FILENAME) as conn:
        rows = conn.execute(
            """
            SELECT match_id, round_num, home_score, away_score, timestamp
            FROM matches
            WHERE league_name = ?
            ORDER BY timestamp DESC
            LIMIT ?
            """,
            (league_name, limit)
        ).fetchall()
        return [{"match_id": r[0], "round_num": r[1], "home_score": r[2], "away_score": r[3], "timestamp": r[4]} for r in rows]

# ------------------- Fonctions utilitaires -------------------
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

def parse_scores(data):
    matches = data.get("matches", [])
    scores = []
    for match in matches:
        if match.get("goals"):
            last_goal = match["goals"][-1]
            scores.append({
                "match_id": match["id"],
                "home_score": int(last_goal["homeScore"]),
                "away_score": int(last_goal["awayScore"]),
            })
        else:
            scores.append({
                "match_id": match["id"],
                "home_score": 0,
                "away_score": 0,
            })
    return scores

async def find_active_round(client, event_id: str, parent_id: int, start_round: int = 1, max_round: int = 100):
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

async def get_scores_for_round(client, event_id: str, parent_id: int, round_num: int):
    url = f"{BASE_URL}/round/{round_num}/playout"
    params = {"eventCategoryId": event_id, "parentEventCategoryId": parent_id}
    result = await fetch_json(client, url, params)
    if result.get("status_http") == 200 and result.get("data"):
        return parse_scores(result["data"])
    return None

# ------------------- Initialisation -------------------
@app.on_event("startup")
async def startup_event():
    init_db()

# ------------------- Endpoints API -------------------
@app.get("/")
def root():
    return {"message": "API Tableau de Bord Bet261 V2 en ligne"}

@app.get("/api/leagues")
async def get_leagues():
    return [{"name": name} for name in LEAGUES.keys()]

@app.get("/api/dashboard")
async def api_dashboard(
    league: str = Query(..., description="Nom de la ligue"),
    start_round: int = Query(1, description="Ronde de départ pour la recherche")
):
    if league not in LEAGUES:
        raise HTTPException(status_code=404, detail=f"Ligue '{league}' inconnue. Vérifiez le nom ou ajoutez-la dans le code.")
    
    league_info = LEAGUES[league]
    event_id = league_info["event_id"]
    parent_id = league_info["parent_id"]

    async with httpx.AsyncClient(follow_redirects=True) as client:
        active_round = await find_active_round(client, event_id, parent_id, start_round)
        if active_round is None:
            return {
                "league": league,
                "error": "Aucune ronde active trouvée",
                "status": "error"
            }

        scores = await get_scores_for_round(client, event_id, parent_id, active_round)
        if scores is None:
            return {
                "league": league,
                "round": active_round,
                "error": "Impossible de récupérer les scores",
                "status": "error"
            }

        save_matches(league, active_round, scores)
        history = get_history(league, limit=50)

        # Prédictions simples
        predictions = []
        if history:
            total_goals = sum(m["home_score"] + m["away_score"] for m in history)
            avg_goals = total_goals / len(history)
            
            if avg_goals < 2.0:
                predictions.append({
                    "type": "under",
                    "message": f"Moyenne faible sur {len(history)} matchs ({avg_goals:.1f} buts/match) → Pensez aux paris Under 2.5."
                })
            elif avg_goals > 3.5:
                predictions.append({
                    "type": "over",
                    "message": f"Moyenne élevée ({avg_goals:.1f} buts/match) → Pensez aux paris Over 2.5."
                })
            
            # Détection de tendance récente
            recent_5 = history[:5]
            recent_avg = sum(m["home_score"] + m["away_score"] for m in recent_5) / len(recent_5)
            if len(history) >= 5 and recent_avg < avg_goals * 0.7:
                predictions.append({
                    "type": "trend_down",
                    "message": f"Tendance à la baisse sur les 5 derniers matchs ({recent_avg:.1f} vs {avg_goals:.1f}) → Possible match serré."
                })
            
            # Analyse des scores actuels faibles
            for score in scores:
                total = score["home_score"] + score["away_score"]
                if total == 0:
                    predictions.append({
                        "type": "no_goal",
                        "message": f"Match #{score['match_id']} : 0-0 → Cote intéressante sur Under."
                    })
                elif total <= 1:
                    predictions.append({
                        "type": "low_scoring",
                        "message": f"Match #{score['match_id']} : {score['home_score']}-{score['away_score']} → Match fermé, attention au nul."
                    })

        return {
            "league": league,
            "round": active_round,
            "scores": scores,
            "predictions": predictions,
            "timestamp": datetime.utcnow().isoformat(),
            "status": "ok"
        }

# ------------------- Interface Web -------------------
@app.get("/dashboard")
async def web_dashboard():
    html = """
    <!DOCTYPE html>
    <html>
    <head>
        <meta charset="UTF-8">
        <meta name="viewport" content="width=device-width, initial-scale=1.0, user-scalable=no">
        <title>Bet261 Virtual Dashboard V2</title>
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
            .scores-grid {
                display: flex;
                flex-direction: column;
                gap: 10px;
            }
            .match-card {
                background: white;
                border-radius: 12px;
                padding: 15px;
                box-shadow: 0 2px 5px rgba(0,0,0,0.05);
                display: flex;
                justify-content: space-between;
                align-items: center;
            }
            .match-id {
                font-size: 12px;
                color: #888;
            }
            .score {
                font-size: 24px;
                font-weight: bold;
                text-align: center;
            }
            .prediction {
                background: #fff3cd;
                color: #856404;
                border-radius: 8px;
                padding: 10px;
                margin-top: 15px;
                font-size: 14px;
                border-left: 4px solid #ffc107;
            }
            .prediction strong {
                display: block;
                margin-bottom: 4px;
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
            <h1>⚽ Dashboard Virtuel</h1>
            <div class="subtitle">Surveillance en temps réel</div>

            <select id="leagueSelect" onchange="loadDashboard()">
                <option value="">-- Choisir une ligue --</option>
            </select>

            <div id="statusMessage" class="status" style="display:none;"></div>
            <div id="scoresContainer" class="scores-grid"></div>
            <div id="predictionsContainer"></div>
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
                        statusEl.textContent = `✅ Ronde ${data.round} - ${data.scores.length} matchs`;

                        const scoresHtml = data.scores.map(score => `
                            <div class="match-card">
                                <div class="match-id">#${score.match_id}</div>
                                <div class="score">${score.home_score} - ${score.away_score}</div>
                                <div></div>
                            </div>
                        `).join('');
                        document.getElementById('scoresContainer').innerHTML = scoresHtml;

                        const predsHtml = data.predictions.map(pred => `
                            <div class="prediction">
                                <strong>🚨 ${pred.type}</strong>
                                ${pred.message}
                            </div>
                        `).join('');
                        document.getElementById('predictionsContainer').innerHTML = predsHtml;
                    } else {
                        statusEl.style.display = 'block';
                        statusEl.className = 'status status-error';
                        statusEl.textContent = `❌ ${data.error || 'Erreur inconnue'}`;
                        document.getElementById('scoresContainer').innerHTML = '';
                        document.getElementById('predictionsContainer').innerHTML = '';
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
