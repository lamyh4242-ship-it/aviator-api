import asyncio
import math
import sqlite3
import time
from datetime import datetime
import httpx
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

DATABASE = "/tmp/virtual_matches.db"
# URL mise à jour avec l'ID de la ligue 8060 (Coupe d'Afrique)
DATA_URL = "https://hg-event-api-prod.sporty-tech.net/api/instantleagues/round/8060?eventCategoryId=146214&getNext=false"

def init_db():
    conn = sqlite3.connect(DATABASE)
    c = conn.cursor()
    c.execute("CREATE TABLE IF NOT EXISTS matches (id INTEGER PRIMARY KEY AUTOINCREMENT, team TEXT, opponent TEXT, odds REAL, expected_start TEXT, UNIQUE(team, expected_start))")
    conn.commit()
    conn.close()

init_db()

async def fetch_and_process():
    # Headers encore plus proches d'un mobile Android (puisque tu es sur mobile)
    headers = {
        "User-Agent": "Mozilla/5.0 (Linux; Android 10; K) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Mobile Safari/537.36",
        "Accept": "application/json, text/plain, */*",
        "Accept-Language": "fr-FR,fr;q=0.9",
        "Origin": "https://bet261.mg",
        "Referer": "https://bet261.mg/"
    }
    
    async with httpx.AsyncClient(headers=headers, follow_redirects=True) as client:
        try:
            response = await client.get(DATA_URL, timeout=15)
            # DEBUG : On affiche le début de la réponse pour comprendre
            print(f"--- LOG DEBUG : Code {response.status_code} ---")
            
            data = response.json()
            # On cherche les événements de manière plus large dans le JSON
            round_data = data.get("round", {})
            events = round_data.get("events", [])
            
            print(f"Matchs détectés : {len(events)}") # C'est ce chiffre qu'on veut voir monter !

            conn = sqlite3.connect(DATABASE)
            c = conn.cursor()
            start_time = round_data.get("expectedStart", str(time.time()))
            
            for event in events:
                home = event.get("homeTeamName", "")
                away = event.get("awayTeamName", "")
                
                # Extraction de la cote max
                all_odds = [1.0]
                for m in event.get("markets", []):
                    for o in m.get("outcomes", []):
                        all_odds.append(float(o.get("odds", 1)))
                max_odds = max(all_odds)

                # Comparaison minuscule pour éviter les ratés
                h, a = home.lower(), away.lower()
                if "benin" in h or "benin" in a:
                    opp = away if "benin" in h else home
                    c.execute("INSERT OR IGNORE INTO matches (team, opponent, odds, expected_start) VALUES (?,?,?,?)", ("Bénin", opp, max_odds, start_time))
                    print(f"✅ Bénin enregistré vs {opp}")

                if "equa" in h or "equa" in a:
                    opp = away if "equa" in h else home
                    c.execute("INSERT OR IGNORE INTO matches (team, opponent, odds, expected_start) VALUES (?,?,?,?)", ("Guinée Équatoriale", opp, max_odds, start_time))
                    print(f"✅ Guinée Éq. enregistrée vs {opp}")

            conn.commit()
            conn.close()
        except Exception as e:
            print(f"Erreur : {e}")

async def scraper_task():
    while True:
        await fetch_and_process()
        await asyncio.sleep(20)

def compute_stats(team: str):
    conn = sqlite3.connect(DATABASE)
    c = conn.cursor()
    c.execute("SELECT odds, opponent FROM matches WHERE team=? ORDER BY expected_start DESC", (team,))
    rows = c.fetchall()
    conn.close()
    
    if not rows:
        return {"ic": 0, "ecart": 0, "last_odds": 0.0, "opponent": "Scan en cours..."}
    
    ecart = 0
    for r in rows:
        if r[0] >= 25: break
        ecart += 1
    
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
        results[team] = {
            "current_Ic": s["ic"],
            "ecart": s["ecart"],
            "opponent": s["opponent"],
            "last_odds": s["last_odds"],
            "heure_estimee": "--:--",
            "zone": "CHASSE" if s["ic"] >= 1.5 else "FROIDE",
            "scores_conseilles": ["1-0", "0-1"]
        }
    return results
