import asyncio
import math
import sqlite3
import time
from datetime import datetime
import httpx
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

DATABASE = "/tmp/virtual_matches.db"
# L'URL avec l'ID de la ligue 8060
DATA_URL = "https://hg-event-api-prod.sporty-tech.net/api/instantleagues/round/8060?eventCategoryId=146214&getNext=false"

def init_db():
    conn = sqlite3.connect(DATABASE)
    c = conn.cursor()
    c.execute("CREATE TABLE IF NOT EXISTS matches (id INTEGER PRIMARY KEY AUTOINCREMENT, team TEXT, opponent TEXT, odds REAL, expected_start TEXT, UNIQUE(team, expected_start))")
    conn.commit()
    conn.close()
    print("--- BASE DE DONNÉES INITIALISÉE ---")

init_db()

async def fetch_and_process():
    headers = {
        "User-Agent": "Mozilla/5.0 (Linux; Android 10; K) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Mobile Safari/537.36",
        "Accept": "application/json",
        "Origin": "https://bet261.mg",
        "Referer": "https://bet261.mg/"
    }
    
    async with httpx.AsyncClient(headers=headers, follow_redirects=True) as client:
        try:
            print("--- TENTATIVE DE CONNEXION AU FLUX... ---")
            response = await client.get(DATA_URL, timeout=20)
            data = response.json()
            
            events = data.get("round", {}).get("events", [])
            print(f"--- RÉSULTAT DU SCAN : {len(events)} MATCHS TROUVÉS ---")

            if len(events) > 0:
                conn = sqlite3.connect(DATABASE)
                c = conn.cursor()
                start_time = str(time.time())
                
                for event in events:
                    h = str(event.get("homeTeamName", "")).lower()
                    a = str(event.get("awayTeamName", "")).lower()
                    
                    # On simplifie la détection au maximum
                    if "benin" in h or "benin" in a or "equa" in h or "equa" in a:
                        all_odds = [1.0]
                        for m in event.get("markets", []):
                            for o in m.get("outcomes", []):
                                try: all_odds.append(float(o.get("odds", 1)))
                                except: continue
                        
                        max_o = max(all_odds)
                        team_name = "Bénin" if "benin" in h or "benin" in a else "Guinée Équatoriale"
                        opp = event.get("awayTeamName") if "benin" in h or "equa" in h else event.get("homeTeamName")
                        
                        c.execute("INSERT OR IGNORE INTO matches (team, opponent, odds, expected_start) VALUES (?,?,?,?)", 
                                 (team_name, opp, max_o, start_time))
                        print(f"✅ MATCH ENREGISTRÉ : {team_name} vs {opp}")
                
                conn.commit()
                conn.close()
        except Exception as e:
            print(f"❌ ERREUR LORS DU SCAN : {e}")

# TASK QUI TOURNE EN BOUCLE
async def scraper_loop():
    while True:
        await fetch_and_process()
        await asyncio.sleep(20)

app = FastAPI()
app.add_middleware(CORSMiddleware, allow_origins=["*"])

@app.on_event("startup")
async def startup_event():
    # Démarre le scan immédiatement au lancement
    asyncio.create_task(scraper_loop())

@app.get("/dashboard")
def get_dashboard():
    results = {}
    conn = sqlite3.connect(DATABASE)
    c = conn.cursor()
    for team in ["Bénin", "Guinée Équatoriale"]:
        c.execute("SELECT odds, opponent FROM matches WHERE team=? ORDER BY id DESC", (team,))
        rows = c.fetchall()
        
        if not rows:
            results[team] = {"current_Ic": 0, "ecart": "0 matchs", "opponent": "Scan en cours...", "last_odds": 0, "zone": "FROIDE"}
        else:
            ecart = 0
            for r in rows:
                if r[0] >= 25: break
                ecart += 1
            ic = round((ecart / 30) * math.log(ecart + 2), 2)
            results[team] = {
                "current_Ic": ic,
                "ecart": f"{ecart} matchs",
                "opponent": rows[0][1],
                "last_odds": rows[0][0],
                "zone": "CHASSE" if ic >= 1.5 else "FROIDE"
            }
    conn.close()
    return results
