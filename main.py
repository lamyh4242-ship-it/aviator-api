import asyncio
import math
import sqlite3
import time
from datetime import datetime
import httpx
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

DATABASE = "/tmp/virtual_matches.db"
DATA_URL = "https://hg-event-api-prod.sporty-tech.net/api/instantleagues/round/5?eventCategoryId=146214&getNext=false"

def init_db():
    conn = sqlite3.connect(DATABASE)
    c = conn.cursor()
    c.execute("""
        CREATE TABLE IF NOT EXISTS matches (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            team TEXT,
            opponent TEXT,
            odds REAL,
            expected_start TEXT,
            UNIQUE(team, expected_start)
        )
    """)
    conn.commit()
    conn.close()

init_db()

async def fetch_and_process():
    async with httpx.AsyncClient() as client:
        try:
            headers = {"User-Agent": "Mozilla/5.0"}
            response = await client.get(DATA_URL, headers=headers, timeout=15)
            data = response.json()
            
            events = data.get("round", {}).get("events", [])
            start_time = data.get("round", {}).get("expectedStart", str(time.time()))
            
            conn = sqlite3.connect(DATABASE)
            c = conn.cursor()
            
            for event in events:
                home = event.get("homeTeamName", "")
                away = event.get("awayTeamName", "")
                
                # Récupération de la cote max du match (outsider)
                max_odds = 1.0
                for m in event.get("markets", []):
                    for o in m.get("outcomes", []):
                        val = float(o.get("odds", 1))
                        if val > max_odds: max_odds = val

                # DETECTION EXACTE SELON TES INFOS
                # On check 'Benin' et 'Equatorial Guinea'
                is_benin = "Benin" in [home, away]
                is_guinea = "Equatorial Guinea" in [home, away]

                if is_benin or is_guinea:
                    target = "Bénin" if is_benin else "Guinée Équatoriale"
                    # L'adversaire est l'autre équipe
                    if is_benin:
                        opponent = away if home == "Benin" else home
                    else:
                        opponent = away if home == "Equatorial Guinea" else home
                    
                    c.execute("INSERT OR IGNORE INTO matches (team, opponent, odds, expected_start) VALUES (?,?,?,?)",
                              (target, opponent, max_odds, start_time))
            
            conn.commit()
            conn.close()
        except Exception as e:
            print(f"Erreur : {e}")

async def scraper_task():
    while True:
        await fetch_and_process()
        await asyncio.sleep(20)

def compute_stats(team: str):
    conn = sqlite3.connect(DATABASE)
    c = conn.cursor()
    c.execute("SELECT odds, opponent FROM matches WHERE team=? ORDER BY expected_start DESC", (team,))
    rows = c.fetchall()
    conn.close()
    
    if not rows:
        return {"ic": 0, "ecart": 0, "last_odds": 0.0, "opponent": "Scan en cours..."}
    
    last_odds = rows[0][0]
    last_opponent = rows[0][1]
    
    # Calcul de l'écart réel (matchs sans cote >= 25)
    ecart = 0
    for r in rows:
        if r[0] >= 25: break
        ecart += 1
    
    # Indice IC ultra-sensible pour le démarrage
    ic = (ecart / 50) * math.log(ecart + 2)
    
    return {"ic": round(ic, 2), "ecart": ecart, "last_odds": last_odds, "opponent": last_opponent}

app = FastAPI()
app.add_middleware(CORSMiddleware, allow_origins=["*"])

@app.on_event("startup")
async def startup():
    asyncio.create_task(scraper_task())

@app.get("/dashboard")
def get_dashboard():
    results = {}
    for team in ["Bénin", "Guinée Équatoriale"]:
        s = compute_stats(team)
        # 3 min par match restant pour l'écart de 350
        min_r = max(0, 350 - s["ecart"]) * 3
        heure_c = datetime.fromtimestamp(time.time() + min_r*60).strftime("%H:%M")
        
        results[team] = {
            "current_Ic": s["ic"],
            "ecart": s["ecart"],
            "opponent": s["opponent"],
            "last_odds": s["last_odds"],
            "heure_estimee": heure_c,
            "zone": "CHASSE" if s["ic"] >= 1.8 else "OBSERVATION" if s["ic"] >= 1.2 else "FROIDE",
            "scores_conseilles": ["2-1", "1-2"] if s["ic"] >= 1.5 else ["1-0", "0-1"]
        }
    return results
