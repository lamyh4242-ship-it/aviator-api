import asyncio
import math
import sqlite3
import time
from datetime import datetime
import httpx
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

DATABASE = "/tmp/virtual_matches.db"
# NOUVELLE URL : On utilise l'API de base qui est plus ouverte
DATA_URL = "https://hg-event-api-prod.sporty-tech.net/api/instantleagues/round/8060"

def init_db():
    conn = sqlite3.connect(DATABASE)
    c = conn.cursor()
    c.execute("CREATE TABLE IF NOT EXISTS matches (id INTEGER PRIMARY KEY AUTOINCREMENT, team TEXT, opponent TEXT, odds REAL, expected_start TEXT, UNIQUE(team, expected_start))")
    conn.commit()
    conn.close()

init_db()

async def fetch_and_process():
    # On imite un navigateur Chrome sur Windows (très classique)
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
        "Accept": "application/json, text/plain, */*",
        "Referer": "https://bet261.mg/",
        "Origin": "https://bet261.mg"
    }
    
    async with httpx.AsyncClient(headers=headers, follow_redirects=True) as client:
        try:
            # On ajoute un paramètre aléatoire pour éviter que Bet261 nous serve une vieille page (Cache)
            params = {"t": int(time.time())}
            response = await client.get(DATA_URL, params=params, timeout=15)
            
            print(f"--- DEBUG : Code {response.status_code} ---")
            
            if response.status_code == 200:
                data = response.json()
                # La structure du JSON peut varier, on cherche partout
                events = data.get("round", {}).get("events", [])
                if not events: # Si ce n'est pas dans 'round', on cherche à la racine
                    events = data.get("events", [])

                print(f"Matchs trouvés : {len(events)}")

                conn = sqlite3.connect(DATABASE)
                c = conn.cursor()
                
                for event in events:
                    home = str(event.get("homeTeamName", ""))
                    away = str(event.get("awayTeamName", ""))
                    
                    # On cherche Bénin ou Guinée
                    target = None
                    if "benin" in home.lower() or "benin" in away.lower():
                        target = "Bénin"
                    elif "equa" in home.lower() or "equa" in away.lower() or "guine" in home.lower():
                        target = "Guinée Équatoriale"

                    if target:
                        opp = away if target.lower() in home.lower() else home
                        # Extraction cotes
                        odds = 1.0
                        for m in event.get("markets", []):
                            for o in m.get("outcomes", []):
                                try: odds = max(odds, float(o.get("odds", 1)))
                                except: continue
                        
                        c.execute("INSERT OR IGNORE INTO matches (team, opponent, odds, expected_start) VALUES (?,?,?,?)",
                                 (target, opp, odds, str(time.time())))
                        print(f"✅ Enregistré : {target} vs {opp} (Cote: {odds})")
                
                conn.commit()
                conn.close()
            else:
                print(f"⚠️ Erreur serveur : {response.text[:100]}")
                
        except Exception as e:
            print(f"❌ Erreur Scan : {e}")

async def scraper_loop():
    while True:
        await fetch_and_process()
        await asyncio.sleep(25)

app = FastAPI()
app.add_middleware(CORSMiddleware, allow_origins=["*"])

@app.on_event("startup")
async def startup():
    asyncio.create_task(scraper_loop())

@app.get("/dashboard")
def get_dashboard():
    results = {}
    conn = sqlite3.connect(DATABASE)
    c = conn.cursor()
    for team in ["Bénin", "Guinée Équatoriale"]:
        c.execute("SELECT odds, opponent FROM matches WHERE team=? ORDER BY id DESC LIMIT 50", (team,))
        rows = c.fetchall()
        if not rows:
            results[team] = {"current_Ic": 0, "ecart": "0 matchs", "opponent": "Scan en cours...", "last_odds": 0, "zone": "FROIDE"}
        else:
            ecart = 0
            for r in rows:
                if r[0] >= 25: break
                ecart += 1
            ic = round((ecart / 30) * math.log(ecart + 2), 2)
            results[team] = {"current_Ic": ic, "ecart": f"{ecart} matchs", "opponent": rows[0][1], "last_odds": rows[0][0], "zone": "CHASSE" if ic >= 1.5 else "FROIDE"}
    conn.close()
    return results
