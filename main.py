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
            # On ajoute un User-Agent pour simuler un navigateur réel
            headers = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"}
            response = await client.get(DATA_URL, headers=headers, timeout=10)
            data = response.json()
            
            round_info = data.get("round", {})
            start_time = round_info.get("expectedStart")
            events = round_info.get("events", [])
            
            conn = sqlite3.connect(DATABASE)
            c = conn.cursor()
            
            for event in events:
                home = event.get("homeTeamName", "")
                away = event.get("awayTeamName", "")
                
                # Extraction de la cote la plus haute (outsider)
                odds_val = 1.0
                markets = event.get("markets", [])
                for m in markets:
                    outcomes = m.get("outcomes", [])
                    if outcomes:
                        current_max = max([float(o.get("odds", 1)) for o in outcomes])
                        if current_max > odds_val: odds_val = current_max

                # Cibles avec détection flexible (Bénin ou Benin, Guinée ou Guinea)
                targets = ["Bénin", "Benin", "Guinée Équatoriale", "Equatorial Guinea"]
                
                for target in targets:
                    if target.lower() in home.lower() or target.lower() in away.lower():
                        display_name = "Bénin" if "benin" in target.lower() else "Guinée Équatoriale"
                        opponent = away if target.lower() in home.lower() else home
                        
                        c.execute("INSERT OR IGNORE INTO matches (team, opponent, odds, expected_start) VALUES (?,?,?,?)",
                                  (display_name, opponent, odds_val, start_time))
            
            conn.commit()
            conn.close()
        except Exception as e:
            print(f"Erreur de capture : {e}")

async def scraper_task():
    while True:
        await fetch_and_process()
        await asyncio.sleep(30)

def compute_stats(team: str):
    conn = sqlite3.connect(DATABASE)
    c = conn.cursor()
    # On récupère tous les matchs pour calculer l'écart réel
    c.execute("SELECT odds, opponent FROM matches WHERE team=? ORDER BY expected_start DESC", (team,))
    rows = c.fetchall()
    conn.close()
    
    if not rows:
        return {"ic": 0, "ecart": 0, "last_odds": 0.0, "opponent": "En attente..."}
    
    # On cherche le dernier match où la cote était >= 25
    history_odds = [r[0] for r in rows]
    last_opponent = rows[0][1]
    last_odds = rows[0][0]
    
    ecart_depuis_grosse_cote = 0
    for o in history_odds:
        if o >= 25:
            break
        ecart_depuis_grosse_cote += 1
        
    # Calcul de l'Indice Ic
    ic = (ecart_depuis_grosse_cote / 350) * math.log(ecart_depuis_grosse_cote + 1) if ecart_depuis_grosse_cote > 0 else 0
    
    return {
        "ic": round(ic, 2),
        "ecart": ecart_depuis_grosse_cote,
        "last_odds": last_odds,
        "opponent": last_opponent
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
        stats = compute_stats(team)
        
        # Prédiction heure (3 min par match moyen)
        min_restantes = max(0, 350 - stats["ecart"]) * 3
        heure_chasse = datetime.fromtimestamp(time.time() + min_restantes*60).strftime("%H:%M")
        
        results[team] = {
            "current_Ic": stats["ic"],
            "ecart": stats["ecart"],
            "opponent": stats["opponent"],
            "last_odds": stats["last_odds"],
            "heure_estimee": heure_chasse,
            "zone": "CHASSE" if stats["ic"] >= 1.8 else "OBSERVATION" if stats["ic"] >= 1.2 else "FROIDE",
            "scores_conseilles": ["1-2", "2-1"] if stats["ic"] >= 1.5 else ["1-0", "0-1"]
        }
    return results
