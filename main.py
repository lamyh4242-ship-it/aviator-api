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
    # On utilise "timestamp" pour éviter les doublons sur un même match
    c.execute("CREATE TABLE IF NOT EXISTS matches (id INTEGER PRIMARY KEY AUTOINCREMENT, team TEXT, opponent TEXT, odds REAL, timestamp TEXT, UNIQUE(team, timestamp))")
    conn.commit()
    conn.close()

init_db()

async def auto_scan_coupe_afrique():
    headers = {"User-Agent": "Mozilla/5.0", "Referer": "https://bet261.mg/"}
    found_count = 0
    async with httpx.AsyncClient(headers=headers, follow_redirects=True) as client:
        try:
            # 1. Recherche de la ligue (Afrique / Africa)
            resp = await client.get(LEAGUES_URL, timeout=10)
            if resp.status_code == 200:
                leagues = resp.json()
                target_id = None
                for l in leagues:
                    name = l.get("name", "").lower()
                    # Détection bilingue de la ligue
                    if "afri" in name: 
                        target_id = l.get("id")
                        print(f"--- 🏆 LIGUE TROUVÉE : {l.get('name')} (ID: {target_id}) ---")
                        break
                
                if target_id:
                    # 2. Récupération des matchs
                    rd = await client.get(f"{LEAGUES_URL}/round/{target_id}", timeout=10)
                    if rd.status_code == 200:
                        events = rd.json().get("round", {}).get("events", [])
                        conn = sqlite3.connect(DATABASE)
                        c = conn.cursor()
                        
                        for e in events:
                            h = str(e.get("homeTeamName", "")).lower()
                            a = str(e.get("awayTeamName", "")).lower()
                            target_label = None
                            
                            # Détection Benin (FR/EN)
                            if "benin" in h or "benin" in a:
                                target_label = "Bénin"
                            # Détection Guinée Équatoriale / Equatorial Guinea
                            elif "equatorial" in h or "equatorial" in a or "equa" in h or "equa" in a:
                                target_label = "Guinée Équatoriale"
                            
                            if target_label:
                                # Déterminer l'adversaire
                                home_raw = e.get("homeTeamName")
                                away_raw = e.get("awayTeamName")
                                opponent = away_raw if target_label.lower() in home_raw.lower() or "benin" in home_raw.lower() or "equa" in home_raw.lower() else home_raw
                                
                                # Extraction de la meilleure cote (Odds)
                                all_odds = [1.0]
                                for m in e.get("markets", []):
                                    for o in m.get("outcomes", []):
                                        try: all_odds.append(float(o.get("odds", 1)))
                                        except: continue
                                max_odds = max(all_odds)
                                
                                # Insertion en base
                                c.execute("INSERT OR IGNORE INTO matches (team, opponent, odds, timestamp) VALUES (?,?,?,?)",
                                         (target_label, opponent, max_odds, str(time.time())))
                                found_count += 1
                                print(f"✅ CAPTURE : {target_label} vs {opponent} | Cote: {max_odds}")
                        
                        conn.commit()
                        conn.close()
        except Exception as ex:
            print(f"❌ Erreur : {ex}")
    return found_count

app = FastAPI()
app.add_middleware(CORSMiddleware, allow_origins=["*"])

@app.get("/dashboard")
async def get_dashboard():
    # Le scan se déclenche à chaque appel de l'app mobile
    await auto_scan_coupe_afrique()
    
    results = {}
    conn = sqlite3.connect(DATABASE)
    c = conn.cursor()
    for team in ["Bénin", "Guinée Équatoriale"]:
        c.execute("SELECT odds, opponent FROM matches WHERE team=? ORDER BY id DESC", (team,))
        rows = c.fetchall()
        if not rows:
            results[team] = {"current_Ic :": 0.0, "ecart": "0", "opponent": "En attente...", "last_odds": 0, "zone": "NON"}
        else:
            # Calcul de l'écart (matchs sans cote >= 25)
            ecart = 0
            for r in rows:
                if r[0] >= 25: break
                ecart += 1
            # Calcul de l'Indice de Confiance
            ic = round((ecart / 30) * math.log(ecart + 2), 2)
            results[team] = {
                "current_Ic": ic,
                "ecart": f"{ecart} matchs",
                "opponent": rows[0][1],
                "last_odds": rows[0][0],
                "zone": "OUI" if ic >= 1.5 else "NON"
            }
    conn.close()
    return results

@app.get("/")
def home(): return {"status": "Robot opérationnel"}
