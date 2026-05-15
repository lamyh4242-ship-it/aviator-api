import asyncio, math, sqlite3, time, httpx
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

DATABASE = "/tmp/can_full_scan.db"
BASE_API_URL = "https://hg-event-api-prod.sporty-tech.net/api/instantleagues"

def init_db():
    conn = sqlite3.connect(DATABASE); c = conn.cursor()
    # On stocke par équipe pour suivre l'historique de chacune
    c.execute("CREATE TABLE IF NOT EXISTS history (team TEXT PRIMARY KEY, ecart INTEGER, last_opp TEXT)")
    conn.commit(); conn.close()

init_db()

async def scan_all_can_matches():
    async with httpx.AsyncClient() as client:
        try:
            r_leagues = await client.get(BASE_API_URL)
            leagues = r_leagues.json()
            can_id = next((l['id'] for l in leagues if "Africa" in l['name']), leagues[0]['id'])
            
            r_matches = await client.get(f"{BASE_API_URL}/playout?eventCategoryId={can_id}")
            matches = r_matches.json().get("matches", [])
            
            predictions = []
            conn = sqlite3.connect(DATABASE); c = conn.cursor()
            
            for m in matches:
                h = m.get("homeTeamName")
                a = m.get("awayTeamName")
                
                # Mise à jour de l'écart pour ces équipes (logique de cycle)
                c.execute("INSERT OR IGNORE INTO history (team, ecart) VALUES (?, 0)", (h,))
                c.execute("UPDATE history SET ecart = ecart + 1, last_opp = ? WHERE team = ?", (a, h))
                
                c.execute("SELECT ecart FROM history WHERE team = ?", (h,))
                current_ecart = c.fetchone()[0]
                
                # Calcul de l'indice de confiance pour ce match précis
                ic = round((current_ecart / 10) * 1.8, 2)
                
                predictions.append({
                    "home": h,
                    "away": a,
                    "ic": ic,
                    "confidence": min(int(ic * 50), 96),
                    "scores": ["1-1", "2-1", "1-0"] if ic > 1.2 else ["0-0", "1-0"]
                })
            
            conn.commit(); conn.close()
            return predictions
        except: return []

app = FastAPI()
app.add_middleware(CORSMiddleware, allow_origins=["*"])

@app.get("/dashboard")
async def dashboard():
    all_preds = await scan_all_can_matches()
    # On trie pour mettre les matchs les plus "surs" (96%) en haut
    return sorted(all_preds, key=lambda x: x['ic'], reverse=True)
