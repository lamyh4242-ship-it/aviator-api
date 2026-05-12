import asyncio
import math
import sqlite3
import time
from datetime import datetime
import httpx
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

DATABASE = "/tmp/virtual_matches.db"
# L'URL du flux Bet261
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
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
        "Accept": "application/json",
        "Referer": "https://bet261.mg/"
    }
    
    async with httpx.AsyncClient(headers=headers, follow_redirects=True) as client:
        try:
            response = await client.get(DATA_URL, timeout=15)
            data = response.json()
            
            events = data.get("round", {}).get("events", [])
            start_time = data.get("round", {}).get("expectedStart", str(time.time()))
            
            # LOG DE DIAGNOSTIC : Pour voir dans Render ce que le robot voit
            print(f"--- Scan à {datetime.now().strftime('%H:%M:%S')} ---")
            print(f"Matchs trouvés dans le flux : {len(events)}")

            conn = sqlite3.connect(DATABASE)
            c = conn.cursor()
            
            for event in events:
                home = str(event.get("homeTeamName", ""))
                away = str(event.get("awayTeamName", ""))
                
                # Extraction de toutes les cotes disponibles
                all_odds = [1.0]
                for m in event.get("markets", []):
                    for o in m.get("outcomes", []):
                        try: all_odds.append(float(o.get("odds", 1)))
                        except: continue
                max_odds = max(all_odds)

                # DÉTECTION PAR MOT-CLÉ (Plus fiable que le nom complet)
                # On cherche juste si "benin" ou "equa" est présent dans le texte
                is_benin = "benin" in home.lower() or "benin" in away.lower()
                is_guinea = "equa" in home.lower() or "equa" in away.lower()

                if is_benin:
                    opp = away if "benin" in home.lower() else home
                    print(f"✅ MATCH DÉTECTÉ : Bénin vs {opp} (Cote: {max_odds})")
                    c.execute("INSERT OR IGNORE INTO matches (team, opponent, odds, expected_start) VALUES (?,?,?,?)",
                              ("Bénin", opp, max_odds, start_time))
                
                if is_guinea:
                    opp = away if "equa" in home.lower() else home
                    print(f"✅ MATCH DÉTECTÉ : Guinée Éq. vs {opp} (Cote: {max_odds})")
                    c.execute("INSERT OR IGNORE INTO matches (team, opponent, odds, expected_start) VALUES (?,?,?,?)",
                              ("Guinée Équatoriale", opp, max_odds, start_time))
            
            conn.commit()
            conn.close()
        except Exception as e:
            print(f"❌ ERREUR SCAN : {e}")

async def scraper_task():
    while True:
        await fetch_and_process()
        await asyncio.sleep(15)

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
    
    ecart = 0
    for r in rows:
        if r[0] >= 25: break
        ecart += 1
    
    # Indice IC très sensible pour le test
    ic = (ecart / 25) * math.log(ecart + 2)
    
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
        # Estimation : match toutes les 2 min
        m_r = max(1, 350 - s["ecart"]) * 2
        h_c = datetime.fromtimestamp(time.time() + m_r*60).strftime("%H:%M")
        
        results[team] = {
            "current_Ic": s["ic"],
            "ecart": f"{s['ecart']} matchs",
            "opponent": s["opponent"],
            "last_odds": s["last_odds"],
            "heure_estimee": h_c,
            "zone": "CHASSE" if s["ic"] >= 1.5 else "OBSERVATION" if s["ic"] >= 1.0 else "FROIDE",
            "scores_conseilles": ["2-1", "1-2"] if s["ic"] >= 1.0 else ["1-0", "0-1"]
        }
    return results
