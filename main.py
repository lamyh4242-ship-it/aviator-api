import asyncio, math, sqlite3, time, httpx
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

# Configuration de la base de données
DATABASE = "/tmp/virtual_matches.db"
BASE_API_URL = "https://hg-event-api-prod.sporty-tech.net/api/instantleagues"

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

async def perform_universal_scan():
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
        "Referer": "https://bet261.mg/"
    }
    found_count = 0
    
    async with httpx.AsyncClient(headers=headers, follow_redirects=True) as client:
        try:
            # ÉTAPE 1 : Récupérer toutes les ligues actives
            r_leagues = await client.get(BASE_API_URL, timeout=10)
            if r_leagues.status_code != 200:
                return 0
            
            leagues = r_leagues.json()
            
            # ÉTAPE 2 : Fouiller chaque ligue pour le Bénin ou la Guinée
            for league in leagues:
                l_id = league.get("id")
                scan_url = f"{BASE_API_URL}/playout?eventCategoryId={l_id}"
                
                try:
                    r = await client.get(scan_url, timeout=5)
                    if r.status_code == 200:
                        matches_data = r.json().get("matches", [])
                        
                        conn = sqlite3.connect(DATABASE)
                        c = conn.cursor()
                        
                        for m in matches_data:
                            h_raw = str(m.get("homeTeamName") or m.get("homeName") or "")
                            a_raw = str(m.get("awayTeamName") or m.get("awayName") or "")
                            h, a = h_raw.lower(), a_raw.lower()
                            
                            target_label = None
                            if "benin" in h or "benin" in a:
                                target_label = "Bénin"
                            elif any(x in h or x in a for x in ["equa", "guinea"]):
                                target_label = "Guinée Équatoriale"
                            
                            if target_label:
                                # EXTRACTION DE LA COTE RÉELLE (1x2)
                                match_odds = 2.0
                                for market in m.get("markets", []):
                                    if "1x2" in market.get("name", "").lower():
                                        # On prend la cote la plus haute (l'outsider)
                                        match_odds = max([float(o.get("odds", 2.0)) for o in market.get("outcomes", [])])
                                
                                is_home = any(x in h for x in ["benin", "equa", "guinea"])
                                opponent = a_raw if is_home else h_raw
                                
                                c.execute("""
                                    INSERT OR IGNORE INTO matches (team, opponent, odds, timestamp) 
                                    VALUES (?, ?, ?, ?)
                                """, (target_label, str(opponent), match_odds, str(time.time())))
                                found_count += 1
                        
                        conn.commit()
                        conn.close()
                except:
                    continue
                    
        except Exception as ex:
            print(f"--- ERREUR SCAN : {ex} ---")
            
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
    # Déclenche le scan automatique
    total = await perform_universal_scan()
    print(f"--- LOG : Scan universel terminé. Trouvé : {total} match(s) ---") # Pour tes logs Render
    
    conn = sqlite3.connect(DATABASE)
    c = conn.cursor()
    res = {}
    
    for t in ["Bénin", "Guinée Équatoriale"]:
        c.execute("SELECT odds, opponent FROM matches WHERE team=? ORDER BY id DESC LIMIT 1", (t,))
        row = c.fetchone()
        
        if not row:
            # FORMAT CRUCIAL POUR DÉBLOQUER TON APP MOBILE
            res[t] = {
                "current_Ic": 0.0, 
                "ecart": "0", 
                "opponent": "En attente", 
                "last_odds": 0, 
                "zone": "NON"
            }
        else:
            c.execute("SELECT odds FROM matches WHERE team=?", (t,))
            history = c.fetchall()
            ecart = len(history)
            
            # Calcul de l'indice de confiance
            ic = round((ecart / 30) * math.log(ecart + 2), 2)
            res[t] = {
                "current_Ic": ic,
                "ecart": str(ecart),
                "opponent": row[1],
                "last_odds": row[0],
                "zone": "OUI" if ic >= 1.5 else "NON"
            }
    conn.close()
    return res

@app.get("/")
def home():
    return {"status": "online"}
