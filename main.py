import asyncio
import math
import sqlite3
import time
from datetime import datetime
import httpx
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

DATABASE = "/tmp/virtual_matches.db"
# URL directe du flux
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
    # Simulation d'un vrai navigateur pour éviter le blocage
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
        "Accept": "application/json, text/plain, */*",
        "Origin": "https://bet261.mg",
        "Referer": "https://bet261.mg/"
    }
    
    async with httpx.AsyncClient(headers=headers, follow_redirects=True) as client:
        try:
            response = await client.get(DATA_URL, timeout=15)
            if response.status_code != 200:
                print(f"Erreur API: Code {response.status_code}")
                return

            data = response.json()
            events = data.get("round", {}).get("events", [])
            start_time = data.get("round", {}).get("expectedStart", str(time.time()))
            
            conn = sqlite3.connect(DATABASE)
            c = conn.cursor()
            
            for event in events:
                home = event.get("homeTeamName", "Inconnu")
                away = event.get("awayTeamName", "Inconnu")
                
                # Extraction sécurisée de la cote
                all_odds = [1.0]
                for m in event.get("markets", []):
                    for o in m.get("outcomes", []):
                        try: all_odds.append(float(o.get("odds", 1)))
                        except: continue
                max_odds = max(all_odds)

                # Détection ultra-simplifiée
                h_low, a_low = home.lower(), away.lower()
                
                # BENIN
                if "benin" in h_low or "benin" in a_low:
                    opp = away if "benin" in h_low else home
                    c.execute("INSERT OR IGNORE INTO matches (team, opponent, odds, expected_start) VALUES (?,?,?,?)",
                              ("Bénin", opp, max_odds, start_time))
                
                # GUINEE
                if "equa" in h_low or "equa" in a_low or "guine" in h_low or "guine" in a_low:
                    opp = away if ("equa" in h_low or "guine" in h_low) else home
                    c.execute("INSERT OR IGNORE INTO matches (team, opponent, odds, expected_start) VALUES (?,?,?,?)",
                              ("Guinée Équatoriale", opp, max_odds, start_time))
            
            conn.commit()
            conn.close()
        except Exception as e:
            print(f"Erreur robot: {e}")

async def scraper_task():
    while True:
        await fetch_and_process()
        await asyncio.sleep(10) # Scan toutes les 10 secondes pour forcer la base

def compute_stats(team: str):
    conn = sqlite3.connect(DATABASE)
    c = conn.cursor()
    # On récupère tout l'historique pour calculer l'écart réel
    c.execute("SELECT odds, opponent FROM matches WHERE team=? ORDER BY expected_start DESC", (team,))
    rows = c.fetchall()
    conn.close()
    
    if not rows:
        return {"ic": 0, "ecart": 0, "last_odds": 0.0, "opponent": "Scan en cours..."}
    
    ecart = 0
    for r in rows:
        if r[0] >= 25: break
        ecart += 1
    
    # Formule boostée pour voir des résultats tout de suite
    ic = (ecart / 30) * math.log(ecart + 2)
    
    return {"ic": round(ic, 2), "ecart": ecart, "last_odds": rows[0][0], "opponent": rows[0][1]}

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
        # Estimation temps
        min_r = max(1, 350 - s["ecart"]) * 3
        heure_c = datetime.fromtimestamp(time.time() + min_r*60).strftime("%H:%M")
        
        results[team] = {
            "current_Ic": s["ic"],
            "ecart": f"{s['ecart']} matchs",
            "opponent": s["opponent"],
            "last_odds": s["last_odds"],
            "heure_estimee": heure_c,
            "zone": "CHASSE" if s["ic"] >= 1.8 else "OBSERVATION" if s["ic"] >= 1.2 else "FROIDE",
            "scores_conseilles": ["2-1", "1-2"] if s["ic"] >= 1.0 else ["1-0", "0-1"]
        }
    return results
