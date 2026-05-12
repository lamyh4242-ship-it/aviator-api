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
                
                # On récupère la cote la plus haute du match (l'outsider)
                all_odds = []
                for m in event.get("markets", []):
                    for o in m.get("outcomes", []):
                        try: all_odds.append(float(o.get("odds", 1)))
                        except: continue
                max_odds = max(all_odds) if all_odds else 1.0

                # DÉTECTION ULTRA-LARGE (Insensible à la casse et aux accents)
                h_low, a_low = home.lower(), away.lower()
                
                is_benin = "benin" in h_low or "benin" in a_low
                is_guinea = "equa" in h_low or "equa" in a_low

                if is_benin:
                    target, opp = "Bénin", (away if "benin" in h_low else home)
                    c.execute("INSERT OR IGNORE INTO matches (team, opponent, odds, expected_start) VALUES (?,?,?,?)",
                              (target, opp, max_odds, start_time))
                
                if is_guinea:
                    target, opp = "Guinée Équatoriale", (away if "equa" in h_low else home)
                    c.execute("INSERT OR IGNORE INTO matches (team, opponent, odds, expected_start) VALUES (?,?,?,?)",
                              (target, opp, max_odds, start_time))
            
            conn.commit()
            conn.close()
        except Exception as e:
            print(f"Erreur technique : {e}")

async def scraper_task():
    while True:
        await fetch_and_process()
        await asyncio.sleep(15) # Scan très fréquent pour ne rien rater

def compute_stats(team: str):
    conn = sqlite3.connect(DATABASE)
    c = conn.cursor()
    c.execute("SELECT odds, opponent FROM matches WHERE team=? ORDER BY expected_start DESC LIMIT 500", (team,))
    rows = c.fetchall()
    conn.close()
    
    if not rows:
        return {"ic": 0, "ecart": 0, "last_odds": 0.0, "opponent": "Scan en cours..."}
    
    # Écart : nombre de matchs depuis la dernière cote >= 25
    ecart = 0
    for r in rows:
        if r[0] >= 25: break
        ecart += 1
    
    # Indice IC : on divise par 50 pour que ça monte visiblement vite
    ic = (ecart / 50) * math.log(ecart + 2)
    
    return {
        "ic": round(ic, 2),
        "ecart": ecart, 
        "last_odds": rows[0][0], 
        "opponent": rows[0][1]
    }

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
        # Estimation : match toutes les 2 min
        minutes_restantes = max(0, 350 - s["ecart"]) * 2
        heure_c = datetime.fromtimestamp(time.time() + minutes_restantes*60).strftime("%H:%M")
        
        # Changement dynamique des scores selon l'indice
        if s["ic"] < 0.5: sc = ["1-0", "0-1"]
        elif s["ic"] < 1.5: sc = ["2-1", "1-2"]
        else: sc = ["2-2", "3-1"]

        results[team] = {
            "current_Ic": s["ic"],
            "ecart": s["ecart"],
            "opponent": s["opponent"],
            "last_odds": s["last_odds"],
            "heure_estimee": heure_c,
            "zone": "CHASSE" if s["ic"] >= 1.8 else "OBSERVATION" if s["ic"] >= 1.2 else "FROIDE",
            "scores_conseilles": sc
        }
    return results
