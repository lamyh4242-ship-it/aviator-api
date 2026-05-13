import asyncio
import math
import sqlite3
import time
import httpx
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

DATABASE = "/tmp/virtual_matches.db"
LEAGUES_URL = "https://hg-event-api-prod.sporty-tech.net/api/instantleagues"

def init_db():
    conn = sqlite3.connect(DATABASE)
    c = conn.cursor()
    c.execute("CREATE TABLE IF NOT EXISTS matches (id INTEGER PRIMARY KEY AUTOINCREMENT, team TEXT, opponent TEXT, odds REAL, timestamp TEXT, UNIQUE(team, timestamp))")
    conn.commit()
    conn.close()

init_db()

async def perform_scan():
    headers = {"User-Agent": "Mozilla/5.0", "Referer": "https://bet261.mg/"}
    count = 0
    async with httpx.AsyncClient(headers=headers) as client:
        try:
            r = await client.get(LEAGUES_URL, timeout=10)
            if r.status_code == 200:
                leagues = r.json()
                target_id = next((l.get("id") for l in leagues if "afri" in l.get("name", "").lower()), None)
                
                if target_id:
                    rd = await client.get(f"{LEAGUES_URL}/round/{target_id}", timeout=10)
                    if rd.status_code == 200:
                        events = rd.json().get("round", {}).get("events", [])
                        conn = sqlite3.connect(DATABASE)
                        c = conn.cursor()
                        for e in events:
                            h, a = str(e.get("homeTeamName", "")).lower(), str(e.get("awayTeamName", "")).lower()
                            name = "Bénin" if "benin" in h or "benin" in a else ("Guinée Équatoriale" if "equa" in h or "equa" in a or "equatorial" in h or "equatorial" in a else None)
                            
                            if name:
                                opp = e.get("awayTeamName") if name.lower() in h else e.get("homeTeamName")
                                c.execute("INSERT OR IGNORE INTO matches (team, opponent, odds, timestamp) VALUES (?,?,?,?)", (name, opp, 2.0, str(time.time())))
                                count += 1
                        conn.commit()
                        conn.close()
        except Exception as ex: print(f"--- ERREUR SCAN : {ex} ---")
    return count

app = FastAPI()
app.add_middleware(CORSMiddleware, allow_origins=["*"])

@app.get("/dashboard")
async def get_dashboard():
    # Déclenche le scan
    found = await perform_scan()
    print(f"--- LOG : Scan effectué, {found} matchs trouvés ---")
    
    conn = sqlite3.connect(DATABASE)
    c = conn.cursor()
    res = {}
    for t in ["Bénin", "Guinée Équatoriale"]:
        c.execute("SELECT odds, opponent FROM matches WHERE team=? ORDER BY id DESC LIMIT 1", (t,))
        row = c.fetchone()
        if not row:
            res[t] = {"current_Ic": 0.0, "ecart": "0", "opponent": "Scan...", "last_odds": 0, "zone": "NON"}
        else:
            c.execute("SELECT odds FROM matches WHERE team=? ORDER BY id DESC", (t,))
            ecart = len(c.fetchall())
            res[t] = {"current_Ic": 1.5, "ecart": f"{ecart} matchs", "opponent": row[1], "last_odds": row[0], "zone": "OUI"}
    conn.close()
    return res
