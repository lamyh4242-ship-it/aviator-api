import asyncio
import math
import os
import sqlite3
import time
from datetime import datetime
from typing import Optional
import httpx
import numpy as np
from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware

# ==================== CONFIGURATION ====================
DATABASE = "/tmp/virtual_matches.db"
# Le lien magique que tu as trouvé
DATA_URL = "https://hg-event-api-prod.sporty-tech.net/api/instantleagues/round/5?eventCategoryId=146214&getNext=false"

def init_db():
    conn = sqlite3.connect(DATABASE)
    c = conn.cursor()
    c.execute("""
        CREATE TABLE IF NOT EXISTS matches (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            team TEXT NOT NULL,
            opponent TEXT NOT NULL,
            odds REAL,
            score TEXT,
            timestamp DATETIME DEFAULT CURRENT_TIMESTAMP,
            expected_start TEXT,
            UNIQUE(team, expected_start)
        )
    """)
    conn.commit()
    conn.close()

init_db()

# ==================== LOGIQUE DE CAPTURE (AUTOMATIQUE) ====================
async def fetch_and_process():
    async with httpx.AsyncClient() as client:
        try:
            response = await client.get(DATA_URL, timeout=10)
            data = response.json()
            
            round_info = data.get("round", {})
            start_time = round_info.get("expectedStart")
            events = round_info.get("events", [])
            
            conn = sqlite3.connect(DATABASE)
            c = conn.cursor()
            
            for event in events:
                home = event.get("homeTeamName")
                away = event.get("awayTeamName")
                # On cherche les cotes dans les marchés (markets)
                markets = event.get("markets", [])
                odds_val = 1.0
                # On cherche la cote du score exact ou de la victoire surprise
                for m in markets:
                    if m.get("marketName") == "Correct Score" or m.get("marketName") == "1X2":
                        # Logique pour extraire la cote la plus haute pour l'outsider
                        outcomes = m.get("outcomes", [])
                        if outcomes:
                            odds_val = max([float(o.get("odds", 1)) for o in outcomes])

                for target in ["Bénin", "Guinée Équatoriale"]:
                    if home == target or away == target:
                        opponent = away if home == target else home
                        try:
                            c.execute("INSERT OR IGNORE INTO matches (team, opponent, odds, expected_start) VALUES (?,?,?,?)",
                                      (target, opponent, odds_val, start_time))
                        except:
                            pass
            conn.commit()
            conn.close()
        except Exception as e:
            print(f"Erreur de capture : {e}")

async def scraper_task():
    while True:
        await fetch_and_process()
        await asyncio.sleep(30) # Vérification toutes les 30 sec

# ==================== ANALYSE ET PRÉDICTION ====================
def compute_ic(team: str):
    conn = sqlite3.connect(DATABASE)
    c = conn.cursor()
    c.execute("SELECT odds FROM matches WHERE team=? ORDER BY id DESC", (team,))
    rows = c.fetchall()
    conn.close()
    
    if not rows: return 0.0, 350, 0
    
    # On cherche les cotes >= 25 (ton nouveau palier)
    history = [r[0] for r in rows]
    try:
        matches_since = next(i for i, o in enumerate(history) if o >= 25)
    except StopIteration:
        matches_since = len(history)
        
    mean_interval = 350 # Valeur par défaut
    ic = (matches_since / mean_interval) * math.log(matches_since + 1) if matches_since > 0 else 0
    return round(ic, 3), matches_since, history[0]

# ==================== API FASTAPI ====================
app = FastAPI()
app.add_middleware(CORSMiddleware, allow_origins=["*"])

@app.on_event("startup")
async def startup():
    asyncio.create_task(scraper_task())

@app.get("/dashboard")
def get_dashboard():
    results = {}
    for team in ["Bénin", "Guinée Équatoriale"]:
        ic, ecart, last_odds = compute_ic(team)
        
        # Prédiction de l'heure
        matchs_restants = max(0, 350 - ecart)
        minutes_restantes = matchs_restants * 3
        heure_chasse = datetime.fromtimestamp(time.time() + minutes_restantes*60).strftime("%H:%M")
        
        # Prédiction Score Exact
        scores = ["1-2", "2-1"] if ic > 1.5 else ["1-0", "0-1"]
        
        results[team] = {
            "current_Ic": ic,
            "ecart": ecart,
            "zone": "CHASSE" if ic >= 1.8 else "OBSERVATION" if ic >= 1.2 else "FROIDE",
            "heure_estimee": heure_chasse,
            "scores_conseilles": scores,
            "last_odds": last_odds
        }
    return results
