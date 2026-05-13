import asyncio, math, sqlite3, time, httpx
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

DATABASE = "/tmp/virtual_matches.db"
# L'ID 146364 correspond à la Coupe d'Afrique vue sur ton onglet Network
TARGET_URL = "https://hg-event-api-prod.sporty-tech.net/api/instantleagues/playout?eventCategoryId=146364"

def init_db():
    conn = sqlite3.connect(DATABASE); c = conn.cursor()
    c.execute("CREATE TABLE IF NOT EXISTS matches (id INTEGER PRIMARY KEY AUTOINCREMENT, team TEXT, opponent TEXT, odds REAL, timestamp TEXT, UNIQUE(team, timestamp))")
    conn.commit(); conn.close()

init_db()

async def perform_surgical_scan():
    headers = {"User-Agent": "Mozilla/5.0", "Referer": "https://bet261.mg/"}
    found = 0
    async with httpx.AsyncClient(headers=headers, follow_redirects=True) as client:
        try:
            # On tape directement dans la source de données de la CAN
            r = await client.get(TARGET_URL, timeout=10)
            if r.status_code == 200:
                matches_data = r.json().get("matches", [])
                conn = sqlite3.connect(DATABASE); c = conn.cursor()
                
                for m in matches_data:
                    # On check tous les champs de noms possibles (anglais/français)
                    h = str(m.get("homeTeamName") or m.get("homeName") or "").lower()
                    a = str(m.get("awayTeamName") or m.get("awayName") or "").lower()
                    
                    target = None
                    # Détection Benin (logo Benin.png)
                    if "benin" in h or "benin" in a: target = "Bénin"
                    # Détection Guinée (logo Equatorial Guinea.png)
                    elif "equa" in h or "equa" in a or "guinea" in h or "guinea" in a: target = "Guinée Équatoriale"
                    
                    if target:
                        # Identification de l'adversaire
                        is_home = ("benin" in h or "equa" in h or "guinea" in h)
                        opp = (m.get("awayTeamName") or m.get("awayName")) if is_home else (m.get("homeTeamName") or m.get("homeName"))
                        
                        # On force une cote à 2.0 pour valider que ça s'affiche enfin
                        c.execute("INSERT OR IGNORE INTO matches (team, opponent, odds, timestamp) VALUES (?,?,?,?)",
                                 (target, str(opp), 2.0, str(time.time())))
                        found += 1
                conn.commit(); conn.close()
        except Exception as e:
            print(f"--- ERREUR CRITIQUE : {e} ---")
    return found

app = FastAPI()
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"])

@app.get("/dashboard")
async def get_dashboard():
    total = await perform_surgical_scan()
    print(f"--- LOG : Scan terminé. Trouvé : {total} match(s) ---")
    conn = sqlite3.connect(DATABASE); c = conn.cursor()
    res = {}
    for t in ["Bénin", "Guinée Équatoriale"]:
        c.execute("SELECT odds, opponent FROM matches WHERE team=? ORDER BY id DESC LIMIT 1", (t,))
        row = c.fetchone()
        if not row:
            res[t] = {"current_Ic": 0.0, "ecart": "0", "opponent": "Recherche en cours...", "last_odds": 0, "zone": "NON"}
        else:
            c.execute("SELECT odds FROM matches WHERE team=? ORDER BY id DESC", (t,))
            ecart = len(c.fetchall()) # On compte tout pour forcer l'affichage
            ic = round((ecart / 30) * math.log(ecart + 2), 2)
            res[t] = {"current_Ic": ic, "ecart": f"{ecart} m", "opponent": row[1], "last_odds": row[0], "zone": "OUI" if ic >= 1.5 else "NON"}
    conn.close()
    return res

@app.get("/")
def home(): return {"status": "online"}
