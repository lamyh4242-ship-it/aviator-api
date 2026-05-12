import asyncio
import math
import sqlite3
import time
from datetime import datetime
import httpx
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

DATABASE = "/tmp/virtual_matches.db"
# URL simplifiée
DATA_URL = "https://hg-event-api-prod.sporty-tech.net/api/instantleagues/round/8060"

def init_db():
    conn = sqlite3.connect(DATABASE)
    c = conn.cursor()
    c.execute("CREATE TABLE IF NOT EXISTS matches (id INTEGER PRIMARY KEY AUTOINCREMENT, team TEXT, opponent TEXT, odds REAL, expected_start TEXT, UNIQUE(team, expected_start))")
    conn.commit()
    conn.close()

init_db()

async def run_scan():
    print(f"--- 🛰️ DÉMARRAGE DU SCAN : {datetime.now()} ---")
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
        "Referer": "https://bet261.mg/"
    }
    async with httpx.AsyncClient(headers=headers, follow_redirects=True) as client:
        try:
            response = await client.get(DATA_URL, timeout=10)
            print(f"--- 📡 STATUS CODE : {response.status_code} ---")
            if response.status_code == 200:
                data = response.json()
                events = data.get("round", {}).get("events", []) or data.get("events", [])
                print(f"--- ⚽ MATCHS TROUVÉS : {len(events)} ---")
                
                conn = sqlite3.connect(DATABASE)
                c = conn.cursor()
                for event in events:
                    h = str(event.get("homeTeamName", "")).lower()
                    a = str(event.get("awayTeamName", "")).lower()
                    target = None
                    if "benin" in h or "benin" in a: target = "Bénin"
                    elif "equa" in h or "equa" in a: target = "Guinée Équatoriale"
                    
                    if target:
                        opp = event.get("awayTeamName") if target.lower() in h else event.get("homeTeamName")
                        c.execute("INSERT OR IGNORE INTO matches (team, opponent, odds, expected_start) VALUES (?,?,?,?)",
                                 (target, opp, 2.0, str(time.time())))
                        print(f"✅ MATCH OK : {target} vs {opp}")
                conn.commit()
                conn.close()
        except Exception as e:
            print(f"❌ ERREUR : {e}")

app = FastAPI()
app.add_middleware(CORSMiddleware, allow_origins=["*"])

# Cette route "réveille" le robot à chaque fois que tu l'appelles
@app.get("/dashboard")
async def get_dashboard():
    # On lance un scan rapide en arrière-plan à chaque appel
    asyncio.create_task(run_scan())
    
    results = {}
    conn = sqlite3.connect(DATABASE)
    c = conn.cursor()
    for team in ["Bénin", "Guinée Équatoriale"]:
        c.execute("SELECT odds, opponent FROM matches WHERE team=? ORDER BY id DESC LIMIT 1", (team,))
        row = c.fetchone()
        if not row:
            results[team] = {"current_Ic": 0.0, "ecart": "0 matchs", "opponent": "Scan en cours...", "last_odds": 0, "zone": "FROIDE"}
        else:
            c.execute("SELECT odds FROM matches WHERE team=? ORDER BY id DESC", (team,))
            all_odds = [r[0] for r in c.fetchall()]
            ecart = 0
            for o in all_odds:
                if o >= 25: break
                ecart += 1
            ic = round((ecart / 30) * math.log(ecart + 2), 2)
            results[team] = {"current_Ic": ic, "ecart": f"{ecart} matchs", "opponent": row[1], "last_odds": row[0], "zone": "OUI" if ic >= 1.5 else "NON"}
    conn.close()
    return results

@app.get("/")
def home():
    return {"status": "Robot en ligne. Allez sur /dashboard"}
