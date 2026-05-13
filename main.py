import asyncio
import math
import sqlite3
import time
import httpx
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

# Configuration
DATABASE = "/tmp/virtual_matches.db"
LEAGUES_URL = "https://hg-event-api-prod.sporty-tech.net/api/instantleagues"

def init_db():
    conn = sqlite3.connect(DATABASE)
    c = conn.cursor()
    # Stockage des matchs avec une contrainte d'unicité pour éviter les doublons
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
    """ Scanne les ligues principales pour trouver le Bénin ou la Guinée Équatoriale """
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
        "Referer": "https://bet261.mg/"
    }
    found_count = 0
    
    async with httpx.AsyncClient(headers=headers, follow_redirects=True) as client:
        try:
            # 1. Récupération de la liste des ligues actives
            resp = await client.get(LEAGUES_URL, timeout=10)
            if resp.status_code != 200:
                return 0
            
            leagues = resp.json()
            # On scanne les 10 premières ligues (souvent les plus actives comme la CAN)
            for league in leagues[:10]:
                league_id = league.get("id")
                detail_url = f"{LEAGUES_URL}/round/{league_id}"
                
                rd = await client.get(detail_url, timeout=10)
                if rd.status_code == 200:
                    events = rd.json().get("round", {}).get("events", [])
                    
                    conn = sqlite3.connect(DATABASE)
                    c = conn.cursor()
                    
                    for e in events:
                        # Noms en minuscules pour une détection bilingue robuste
                        home_raw = str(e.get("homeTeamName", ""))
                        away_raw = str(e.get("awayTeamName", ""))
                        h = home_raw.lower()
                        a = away_raw.lower()
                        
                        target_label = None
                        # Détection Bénin (ou Sudan comme vu sur tes photos)
                        if "benin" in h or "benin" in a or "sudan" in h or "sudan" in a:
                            target_label = "Bénin"
                        # Détection Guinée (ou Congo comme vu sur tes photos)
                        elif "equa" in h or "equa" in a or "congo" in h or "congo" in a:
                            target_label = "Guinée Équatoriale"
                        
                        if target_label:
                            # Identification de l'adversaire
                            opponent = away_raw if target_label.lower() in h or "benin" in h or "equa" in h or "sudan" in h or "congo" in h else home_raw
                            
                            # Extraction de la meilleure cote disponible
                            odds_list = [1.0]
                            for m in e.get("markets", []):
                                for o in m.get("outcomes", []):
                                    try: odds_list.append(float(o.get("odds", 1)))
                                    except: continue
                            max_odds = max(odds_list)
                            
                            # Sauvegarde
                            c.execute("""
                                INSERT OR IGNORE INTO matches (team, opponent, odds, timestamp) 
                                VALUES (?, ?, ?, ?)
                            """, (target_label, opponent, max_odds, str(time.time())))
                            found_count += 1
                    
                    conn.commit()
                    conn.close()
        except Exception as ex:
            print(f"--- ERREUR SCAN : {ex} ---")
            
    return found_count

app = FastAPI()

# Autorise ton application mobile à lire les données
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

@app.get("/dashboard")
async def get_dashboard():
    # Déclenche le scan à chaque appel de l'application
    total_found = await perform_global_scan()
    print(f"--- LOG : Scan terminé. Matchs cibles trouvés : {total_found} ---")
    
    conn = sqlite3.connect(DATABASE)
    c = conn.cursor()
    final_results = {}
    
    for team in ["Bénin", "Guinée Équatoriale"]:
        # Récupère le dernier match pour l'affichage
        c.execute("SELECT odds, opponent FROM matches WHERE team=? ORDER BY id DESC LIMIT 1", (team,))
        last_match = c.fetchone()
        
        if not last_match:
            final_results[team] = {
                "current_Ic": 0.0,
                "ecart": "0",
                "opponent": "En attente...",
                "last_odds": 0,
                "zone": "NON"
            }
        else:
            # Calcul de l'écart (nombre de matchs depuis la dernière cote >= 25)
            c.execute("SELECT odds FROM matches WHERE team=? ORDER BY id DESC", (team,))
            all_history = c.fetchall()
            ecart = 0
            for row in all_history:
                if row[0] >= 25: break
                ecart += 1
            
            # Formule de l'Indice de Confiance
            ic = round((ecart / 30) * math.log(ecart + 2), 2)
            
            final_results[team] = {
                "current_Ic": ic,
                "ecart": f"{ecart} matchs",
                "opponent": last_match[1],
                "last_odds": last_match[0],
                "zone": "OUI" if ic >= 1.5 else "NON"
            }
            
    conn.close()
    return final_results

@app.get("/")
def health_check():
    return {"status": "online", "message": "CAN Chase API is running"}
