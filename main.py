from fastapi import FastAPI
import httpx

app = FastAPI()

# On cible directement le fichier de données JSON officiel du site
TARGET_URL = "https://bet261.mg/seo_virtual.json"

@app.get("/")
def read_root():
    return {"message": "Le serveur de scraping Bet261 est en ligne !"}

@app.get("/dashboard")
async def get_dashboard():
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
        "Accept": "application/json, text/plain, */*",
        "Referer": "https://bet261.mg/virtual"
    }
    
    async with httpx.AsyncClient() as client:
        try:
            r = await client.get(TARGET_URL, headers=headers, timeout=15.0)
            
            return {
                "status_http": r.status_code,
                "data": r.json() if r.status_code == 200 else r.text
            }
            
        except Exception as e:
            return {
                "error": "Erreur lors de la récupération",
                "details": str(e)
            }
