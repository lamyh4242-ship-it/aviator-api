from fastapi import FastAPI
import httpx

app = FastAPI()

@app.get("/")
def read_root():
    return {"message": "Le serveur de scraping Bet261 est en ligne !"}

@app.get("/dashboard")
async def get_dashboard():
    # On cible l'URL générale des matchs/calendrier pour cette catégorie (ID 8035)
    target_api = "https://hg-event-api-prod.sporty-tech.net/api/instantleagues/category/8035/matches"
    
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/151.0.0.0 Safari/537.36",
        "App-Version": "34727",
        "Origin": "https://bet261.mg",
        "Referer": "https://bet261.mg/",
        "Accept": "application/json, text/plain, */*"
    }
    
    async with httpx.AsyncClient() as client:
        try:
            r = await client.get(target_api, headers=headers, timeout=15.0)
            
            try:
                data_content = r.json()
            except:
                data_content = r.text[:1000]
            
            return {
                "status_http": r.status_code,
                "content_type": r.headers.get("content-type"),
                "data": data_content
            }
            
        except Exception as e:
            return {
                "error": "Erreur lors de la récupération",
                "details": str(e)
            }
