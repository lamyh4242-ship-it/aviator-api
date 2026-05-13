import asyncio
import math
import sqlite3
import time
import httpx
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

DATABASE = "/tmp/virtual_matches.db"
# URL directe de la ligue 8060
DATA_URL = "https://hg-event-api-prod.sporty-tech.net/api/instantleagues/round/8060"

def init_db():
    conn = sqlite3.connect(DATABASE)
    c = conn.cursor()
    c.execute("CREATE TABLE IF NOT EXISTS matches (id INTEGER PRIMARY KEY AUTOINCREMENT, team TEXT, opponent TEXT, odds REAL, expected_start TEXT, UNIQUE(team, expected_start))")
    conn.commit()
    conn.close()

init_db()

async def fetch_now():
    """ Cette fonction force le scan immédiatement """
    headers = {"User-Agent": "Mozilla/5.0", "Referer": "https://bet261.mg/"}
    async with httpx.AsyncClient(headers=headers, follow_redirects=True) as client:
        try:
            r = await client.get(DATA_URL, timeout=10)
            if r.status_code == 200:
                data = r.json()
                events = data.get("round", {}).get("events", []) or data.get("events", [])
                conn = sqlite3.connect(DATABASE)
                c = conn.cursor()
                for e in events:
                    h, a = str(e.get("homeTeamName", "")), str(e.get("awayTeamName", ""))
                    # Détection Bénin / Guinée
                    target = "Bénin" if "benin" in h.lower() or "benin" in a.lower() else None
                    if not target:
                        target = "Guinée Équatoriale" if "equa" in h.lower() or "equa" in a.lower() else None
                    
                    if target:
                        opp = a if target.lower() in h.lower() else h
                        c.execute("INSERT OR IGNORE INTO matches (team, opponent, odds, expected_start) VALUES (?,?,?,?)",
                                 (target, opp, 2.0, str(time.time())))
                conn.commit()
                conn.close()
                return len(events)
        except: pass
    return 0

app = FastAPI()
app.add_middleware(CORSMiddleware, allow_origins=["*"])

@app.get("/dashboard")
async def get_dashboard():
    # FORCE LE SCAN à chaque fois que l'app mobile demande les données
    nb = await fetch_now()
    print(f"--- SCAN FORCE : {nb} matchs trouvés ---")
    
    results = {}
    conn = sqlite3.connect(DATABASE)
    c = conn.cursor()
    for team in ["Bénin", "Guinée Équatoriale"]:
        c.execute("SELECT odds, opponent FROM matches WHERE team=? ORDER BY id DESC LIMIT 1", (team,))
        row = c.fetchone()
        if not row:
            results[team] = {"current_Ic": 0.0, "ecart": "0", "opponent": "Attente...", "last_odds": 0, "zone": "NON"}
        else:
            c.execute("SELECT odds FROM matches WHERE team=? ORDER BY id DESC", (team,))
            ecart = len(c.fetchall())
            ic = round((ecart / 30) * math.log(ecart + 2), 2)
            results[team] = {"current_Ic": ic, "ecart": f"{ecart}", "opponent": row[1], "last_odds": row[0], "zone": "OUI" if ic >= 1.5 else "NON"}
    conn.close()
    return results

@app.get("/")
def read_root():
    return {"message": "API Active"}
