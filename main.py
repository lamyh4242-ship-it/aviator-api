from fastapi import FastAPI
import httpx

app = FastAPI()

# L'API events de leur infrastructure technique
API_URL = "https://hg-event-api-prod.sporty-tech.net/api/"

@app.get("/")
def read_root():
    return {"message": "Le serveur de scraping Bet261 / SportyTech est en ligne !"}

@app.get("/dashboard")
async def get_dashboard():
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
        "Origin": "https://bet261.mg",
        "Referer": "https://bet261.mg/",
        "Accept": "application/json, text/plain, */*"
    }
    
    async with httpx.AsyncClient() as client:
        try:
            # On essaie d'interroger l'API events (par exemple pour lister les sports ou les matchs virtuels)
            # Note: On pointe vers une route standard d'API de paris sportifs
            r = await client.get(f"{API_URL}public/lots", headers=headers, timeout=15.0)
            
            return {
                "status_http": r.status_code,
                "data": r.json() if r.status_code == 200 else r.text
            }
            
        except Exception as e:
            return {
                "error": "Erreur lors de la communication avec l'API SportyTech",
                "details": str(e)
            }
