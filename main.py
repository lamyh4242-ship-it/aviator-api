import asyncio
import math
import sqlite3
import time
import httpx
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

# Configuration de la base de données
DATABASE = "/tmp/virtual_matches.db"
LEAGUES_URL = "https://hg-event-api-prod.sporty-tech.net/api/instantleagues"

def init_db():
    conn = sqlite3.connect(DATABASE)
    c = conn.cursor()
    c.execute("""
        CREATE TABLE IF NOT EXISTS matches (
            id INTEGER PRIMARY KEY AUTOINCREMENT, 
            team TEXT, 
            opponent TEXT, 
            odds REAL, 
            timestamp TEXT, 
            UNIQUE(team, timestamp)
        )
    """)
    conn.commit()
    conn.close()

init_db()

async def perform_global_scan():
    """ 
    Scanne l'API pour trouver les IDs de matchs vus sur l'onglet Network 
    et les associer aux noms d'équipes (Benin/Equatorial Guinea).
    """
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
        "Referer": "https://bet261.mg/"
    }
    found_count = 0
    
    async with httpx.AsyncClient(headers=headers, follow_redirects=True) as client:
        try:
            # 1. On récupère la liste des ligues
            r = await client.get(LEAGUES_URL, timeout=10)
            if r.status_code != 200:
                return 0
            
            leagues = r.json()
            # On parcourt toutes les ligues disponibles
            for league in leagues:
                l_id = league.get("id")
                # On interroge le détail du round (vu dans l'analyse Network)
                rd = await client.get(f"{LEAGUES_URL}/round/{l_id}", timeout=10)
                
                if rd.status_code == 200:
                    data = rd.json()
                    events = data.get("round", {}).get("events", [])
                    
                    conn = sqlite3.connect(DATABASE)
                    c = conn.cursor()
                    
                    for e in events:
                        # Extraction des noms (Bet261 utilise l'anglais dans son API)
                        h_raw = str(e.get("homeTeamName", ""))
                        a_raw = str(e.get("awayTeamName", ""))
                        h = h_raw.lower()
                        a = a_raw.lower()
                        
                        target_label = None
                        # Détection basée sur les logos Benin.png et Equatorial Guinea.png vus sur ton PC
                        if "benin" in h or "benin" in a:
                            target_label = "Bénin"
                        elif "equatorial" in h or "equatorial" in a or "guinea" in h or "guinea" in a:
                            target_label = "Guinée Équatoriale"
                        
                        if target_label:
                            # Déterminer qui est l'adversaire
                            is_home = any(x in h for x in ["benin", "equatorial"])
                            opponent = a_raw if is_home else h_raw
                            
                            # Extraction de la cote (souvent dans le premier marché 1X2)
                            match_odds = 2.0
                            markets = e.get("markets", [])
                            if markets:
                                for m in markets:
                                    if "1x2" in m.get("name", "").lower():
                                        outcomes = m.get("outcomes", [])
                                        if outcomes:
                                            # On prend la cote la plus haute pour le calcul d'écart
                                            match_odds = max([float(o.get("odds", 2.0)) for o in outcomes])
                            
                            # Insertion en base de données
                            c.execute("""
                                INSERT OR IGNORE INTO matches (team, opponent, odds, timestamp) 
                                VALUES (?, ?, ?, ?)
                            """, (target_label, opponent, match_odds, str(time.time())))
                            found_count += 1
                    
                    conn.commit()
                    conn.close()
        except Exception as ex:
            print(f"--- ERREUR DURANT LE SCAN : {ex} ---")
            
    return found_count

app = FastAPI()

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

@app.get("/dashboard")
async def get_dashboard():
    # Déclenche le scan chirurgical
    total = await perform_global_scan()
    print(f"--- LOG : Scan terminé. Matchs cibles capturés : {total} ---")
    
    conn = sqlite3.connect(DATABASE)
    c = conn.cursor()
    res = {}
    
    for t in ["Bénin", "Guinée Équatoriale"]:
        c.execute("SELECT odds, opponent FROM matches WHERE team=? ORDER BY id DESC LIMIT 1", (t,))
        row = c.fetchone()
        
        if not row:
            res[t] = {"current_Ic": 0.0, "ecart": "0", "opponent": "Scan en cours...", "last_odds": 0, "zone": "NON"}
        else:
            # Calcul de l'écart dynamique
            c.execute("SELECT odds FROM matches WHERE team=? ORDER BY id DESC", (t,))
            history = c.fetchall()
            ecart = 0
            for h_row in history:
                if h_row[0] >= 25: break
                ecart += 1
            
            ic = round((ecart / 30) * math.log(ecart + 2), 2)
            res[t] = {
                "current_Ic": ic,
                "ecart": f"{ecart} matchs",
                "opponent": row[1],
                "last_odds": row[0],
                "zone": "OUI" if ic >= 1.5 else "NON"
            }
    conn.close()
    return res

@app.get("/")
def home():
    return {"status": "online", "message": "API Bet261 Ready"}
