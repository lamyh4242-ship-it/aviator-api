from fastapi import FastAPI, Query
from fastapi.responses import HTMLResponse
import httpx, asyncio, random
from datetime import datetime

app = FastAPI()

USER_AGENTS = [
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/151.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.4 Safari/605.1.15",
    "Mozilla/5.0 (iPhone; CPU iPhone OS 17_4 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.4 Mobile/15E148 Safari/604.1",
]

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

BASE_URL = "https://hg-event-api-prod.sporty-tech.net/api/instantleagues"

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
    """Extrait les scores finaux de chaque match à partir des données JSON de /playout."""
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

@app.get("/")
def root():
    return {"message": "API passerelle Bet261/SportyTech en ligne"}

@app.get("/dashboard")
async def dashboard(
    round_num: int = Query(..., description="Ronde actuelle"),
    event_id: str = Query("161769", description="eventCategoryId"),
    parent_id: int = 8035
):
    url = f"{BASE_URL}/round/{round_num}/playout"
    params = {"eventCategoryId": event_id, "parentEventCategoryId": parent_id}
    async with httpx.AsyncClient(follow_redirects=True) as client:
        result = await fetch_json(client, url, params)
    return {"ronde": round_num, "event_id": event_id, "parent_id": parent_id, **result}

@app.get("/scan")
async def scan_rounds(
    event_id: str = "161769",
    parent_id: int = 8035,
    start: int = 1,
    end: int = 60
):
    """Scanne un intervalle de rondes pour trouver la ronde active"""
    results = []
    async with httpx.AsyncClient(follow_redirects=True) as client:
        for r in range(start, end + 1):
            url = f"{BASE_URL}/round/{r}/playout"
            params = {"eventCategoryId": event_id, "parentEventCategoryId": parent_id}
            res = await fetch_json(client, url, params)
            results.append({
                "round": r,
                "status": res.get("status_http"),
                "has_data": bool(res.get("data")),
            })
            await asyncio.sleep(0.5)
    return results

@app.get("/scores")
async def get_scores_json(
    round_num: int = Query(..., description="Ronde actuelle"),
    event_id: str = Query("161769", description="eventCategoryId"),
    parent_id: int = 8035
):
    """Retourne les scores au format JSON (pour un usage programmatique)."""
    url = f"{BASE_URL}/round/{round_num}/playout"
    params = {"eventCategoryId": event_id, "parentEventCategoryId": parent_id}
    async with httpx.AsyncClient(follow_redirects=True) as client:
        result = await fetch_json(client, url, params)

    if result.get("status_http") != 200:
        return result

    scores = parse_scores(result["data"])
    return {"ronde": round_num, "scores": scores, "timestamp": datetime.utcnow().isoformat()}

@app.get("/scores-view", response_class=HTMLResponse)
async def get_scores_html(
    round_num: int = Query(..., description="Ronde actuelle"),
    event_id: str = Query("161769", description="eventCategoryId"),
    parent_id: int = 8035,
    refresh: int = Query(10, description="Délai de rafraîchissement automatique en secondes (0 pour désactiver)")
):
    """Retourne une page HTML simple et lisible pour mobile avec les scores."""
    url = f"{BASE_URL}/round/{round_num}/playout"
    params = {"eventCategoryId": event_id, "parentEventCategoryId": parent_id}
    async with httpx.AsyncClient(follow_redirects=True) as client:
        result = await fetch_json(client, url, params)

    if result.get("status_http") != 200:
        return HTMLResponse(f"""
        <html>
        <head><meta charset="UTF-8"><meta name="viewport" content="width=device-width, initial-scale=1.0">
        <title>Erreur</title></head>
        <body style="font-family:Arial; text-align:center; padding-top:50px;">
            <h1>Erreur {result.get('status_http')}</h1>
            <p>{result.get('data')}</p>
        </body>
        </html>
        """)

    scores = parse_scores(result["data"])
    timestamp = datetime.utcnow().strftime("%H:%M:%S")

    # Génération des cartes pour chaque match
    cards_html = ""
    for score in scores:
        cards_html += f"""
        <div class="match-card">
            <div class="match-id">Match #{score['match_id']}</div>
            <div class="score">
                <span class="home">{score['home_score']}</span>
                <span class="separator">-</span>
                <span class="away">{score['away_score']}</span>
            </div>
        </div>
        """

    # Meta refresh si demandé
    refresh_meta = f'<meta http-equiv="refresh" content="{refresh}">' if refresh > 0 else ""

    html = f"""
    <!DOCTYPE html>
    <html>
    <head>
        <meta charset="UTF-8">
        <meta name="viewport" content="width=device-width, initial-scale=1.0">
        <title>Scores Virtuels - Ronde {round_num}</title>
        {refresh_meta}
        <style>
            body {{
                font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, Oxygen, Ubuntu, sans-serif;
                background: #f0f2f5;
                margin: 0;
                padding: 15px;
            }}
            .container {{
                max-width: 500px;
                margin: 0 auto;
            }}
            h1 {{
                text-align: center;
                color: #1a1a1a;
                margin-bottom: 5px;
            }}
            .timestamp {{
                text-align: center;
                color: #666;
                font-size: 14px;
                margin-bottom: 20px;
            }}
            .match-card {{
                background: white;
                border-radius: 12px;
                padding: 15px;
                margin-bottom: 10px;
                box-shadow: 0 2px 5px rgba(0,0,0,0.05);
            }}
            .match-id {{
                font-size: 12px;
                color: #777;
                margin-bottom: 8px;
                text-align: center;
            }}
            .score {{
                font-size: 28px;
                font-weight: bold;
                text-align: center;
                display: flex;
                justify-content: center;
                align-items: center;
                gap: 12px;
            }}
            .home, .away {{
                color: #2c3e50;
                flex: 1;
            }}
            .home {{
                text-align: right;
            }}
            .away {{
                text-align: left;
            }}
            .separator {{
                color: #aaa;
                flex: 0 0 auto;
            }}
        </style>
    </head>
    <body>
        <div class="container">
            <h1>Ronde {round_num}</h1>
            <div class="timestamp">Dernière mise à jour : {timestamp} UTC</div>
            {cards_html}
        </div>
    </body>
    </html>
    """
    return HTMLResponse(html)
