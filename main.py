import asyncio, math, sqlite3, time, httpx
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

DATABASE = "/tmp/matrix_virtual.db"
BASE_API_URL = "https://hg-event-api-prod.sporty-tech.net/api/instantleagues"

app = FastAPI()
app.add_middleware(CORSMiddleware, allow_origins=["*"])

def init_db():
    conn = sqlite3.connect(DATABASE)
    c = conn.cursor()
    # Table pour stocker la mémoire du serveur (Saturation des Buts)
    c.execute('''CREATE TABLE IF NOT EXISTS system_tracker 
                 (league TEXT PRIMARY KEY, ecart_under INTEGER, lambda_mu REAL, last_scan INTEGER)''')
    c.execute("INSERT OR IGNORE INTO system_tracker VALUES ('England Virtual', 0, 2.5, 0)")
    c.execute("INSERT OR IGNORE INTO system_tracker VALUES ('Africa Cup', 0, 2.1, 0)")
    conn.commit(); conn.close()

init_db()

async def analyze_algorithm_pressure():
    headers = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"}
    async with httpx.AsyncClient(headers=headers, follow_redirects=True) as client:
        try:
            # 1. Capture des flux de l'API
            r = await client.get(BASE_API_URL, timeout=10)
            leagues = r.json()
            
            targets = {
                "England Virtual": next((l['id'] for l in leagues if "England" in l['name']), None),
                "Africa Cup": next((l['id'] for l in leagues if "Africa" in l['name'] or "CAN" in l['name']), None)
            }
            
            conn = sqlite3.connect(DATABASE); c = conn.cursor()
            results = []
            current_time = int(time.time())
            
            for lg_name, lg_id in targets.items():
                if not lg_id: continue
                
                # 2. Scan des matchs à venir
                res = await client.get(f"{BASE_API_URL}/playout?eventCategoryId={lg_id}", timeout=10)
                data = res.json().get("matches", [])
                if not data: continue
                
                # 3. Récupération de la mémoire de saturation
                c.execute("SELECT ecart_under, lambda_mu, last_scan FROM system_tracker WHERE league=?", (lg_name,))
                row = c.fetchone()
                ecart = row[0] if row else 0
                lam = row[1] if row else 2.5
                last_scan = row[2] if row else 0
                
                # L'algorithme virtuel tourne environ toutes les 3 minutes (180s)
                # On incrémente la pression (E) à chaque nouveau cycle détecté
                if current_time - last_scan > 120:
                    ecart += 1 
                    c.execute("UPDATE system_tracker SET ecart_under=?, last_scan=? WHERE league=?", (ecart, current_time, lg_name))
                
                # 4. Modélisation Mathématique
                N_const = 3.0 if lg_name == "England Virtual" else 4.0
                
                # Calcul de l'Indice Ic
                ic = round((ecart / N_const) * math.log(ecart + 2), 2)
                
                # Loi de Poisson: P(X >= 2 buts)
                poisson_0 = math.exp(-lam)
                poisson_1 = poisson_0 * lam
                prob_over_1_5 = (1 - (poisson_0 + poisson_1)) * 100
                
                # Fiabilité dynamique croisée (Loi de Poisson + Saturation PRNG)
                confidence = min(int(prob_over_1_5 + (ic * 15)), 96) 
                
                # Seuils d'Alerte
                zone = "OBSERVATION"
                if confidence >= 90: zone = "ALERTE ROUGE"
                elif confidence >= 70: zone = "TENSION"
                
                # Sélection des cibles prioritaires (les 3 premiers matchs de la liste)
                upcoming = [f"{m.get('homeTeamName','')} vs {m.get('awayTeamName','')}" for m in data[:3]]
                
                # Purge de la mémoire: Si on a atteint 96%, le cycle va casser, on réinitialise l'écart
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
            # On place toujours la ligue la plus sous pression en haut de l'API
            return sorted(results, key=lambda x: x['confidence'], reverse=True)
            
        except Exception as e:
            print(f"Erreur Système: {e}")
            return []

@app.get("/dashboard")
async def dashboard():
    return await analyze_algorithm_pressure()
