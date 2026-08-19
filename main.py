import asyncio, math, sqlite3, time, httpx, os
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse

DATABASE = "/tmp/matrix_virtual.db"
BASE_API_URL = "https://hg-event-api-prod.sporty-tech.net/api/instantleagues"

app = FastAPI()
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Connecter le dossier static pour afficher le HTML sur la page d'accueil
if os.path.exists("static"):
    app.mount("/static", StaticFiles(directory="static"), name="static")

@app.get("/")
async def read_index():
    # Si le fichier index.html existe, on l'affiche directement
    if os.path.exists("static/index.html"):
        return FileResponse("static/index.html")
    # Sinon, on affiche un message de secours
    return {"status": "Le serveur Python est à jour ! Mais index.html est introuvable."}

def init_db():
    conn = sqlite3.connect(DATABASE)
    c = conn.cursor()
    c.execute('''CREATE TABLE IF NOT EXISTS system_tracker 
                 (league TEXT PRIMARY KEY, ecart_under INTEGER, lambda_mu REAL, last_scan INTEGER)''')
    c.execute("INSERT OR IGNORE INTO system_tracker VALUES ('England Virtual', 0, 2.5, 0)")
    c.execute("INSERT OR IGNORE INTO system_tracker VALUES ('Africa Cup', 0, 2.1, 0)")
    conn.commit(); conn.close()

init_db()

async def analyze_algorithm_pressure():
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
        "Accept": "application/json"
    }
    async with httpx.AsyncClient(headers=headers, follow_redirects=True) as client:
        try:
            r = await client.get(BASE_API_URL, timeout=10)
            leagues = r.json()
            
            if isinstance(leagues, dict):
                leagues = leagues.get("data", leagues.get("leagues", []))
            
            targets = {}
            if isinstance(leagues, list):
                for l in leagues:
                    name = str(l.get('name', '')).lower()
                    lid = l.get('id')
                    if not lid: continue
                    
                    if any(k in name for k in ["england", "english", "premier", "anglaise"]):
                        targets["England Virtual"] = lid
                    elif any(k in name for k in ["africa", "can", "afrique", "coupe"]):
                        targets["Africa Cup"] = lid

                if not targets and len(leagues) > 0:
                    for idx, l in enumerate(leagues[:2]):
                        targets[l.get('name', f'Ligue {idx+1}')] = l.get('id')

            conn = sqlite3.connect(DATABASE); c = conn.cursor()
            results = []
            current_time = int(time.time())
            
            for lg_name, lg_id in targets.items():
                if not lg_id: continue
                
                res = await client.get(f"{BASE_API_URL}/playout?eventCategoryId={lg_id}", timeout=10)
                res_data = res.json()
                matches = res_data.get("matches", []) if isinstance(res_data, dict) else []
                
                c.execute("SELECT ecart_under, lambda_mu, last_scan FROM system_tracker WHERE league=?", (lg_name,))
                row = c.fetchone()
                ecart = row[0] if row else 0
                lam = row[1] if row else 2.5
                last_scan = row[2] if row else 0
                
                if current_time - last_scan > 120:
                    ecart += 1 
                    c.execute("INSERT OR REPLACE INTO system_tracker VALUES (?, ?, ?, ?)", (lg_name, ecart, lam, current_time))
                
                N_const = 3.0 if "england" in lg_name.lower() else 4.0
                ic = round((ecart / N_const) * math.log(ecart + 2), 2)
                
                poisson_0 = math.exp(-lam)
                poisson_1 = poisson_0 * lam
                prob_over_1_5 = (1 - (poisson_0 + poisson_1)) * 100
                
                confidence = min(int(prob_over_1_5 + (ic * 15)), 96) 
                
                zone = "OBSERVATION"
                if confidence >= 90: zone = "ALERTE ROUGE"
                elif confidence >= 70: zone = "TENSION"
                
                upcoming = [f"{m.get('homeTeamName','Équipe A')} vs {m.get('awayTeamName','Équipe B')}" for m in matches[:3]]
                if not upcoming:
                    upcoming = ["Prochains matchs en attente..."]
                
                if confidence >= 96 and current_time - last_scan > 120:
                    c.execute("UPDATE system_tracker SET ecart_under=0 WHERE league=?", (lg_name,))
                
                results.append({
                    "league": lg_name,
                    "ic_tension": ic,
                    "confidence": confidence,
                    "zone": zone,
                    "targets": upcoming,
                    "recommended_bet": "OVER 1.5 BUTS" if confidence >= 70 else "ATTENDRE",
                    "scores": ["2-1", "1-2", "2-2"] if confidence >= 90 else ["1-1", "2-0", "0-2"]
                })
                
            conn.commit(); conn.close()
            return sorted(results, key=lambda x: x['confidence'], reverse=True)
            
        except Exception as e:
            print(f"Erreur Scan: {e}")
            return []

@app.get("/dashboard")
async def dashboard():
    return await analyze_algorithm_pressure()
