from fastapi import FastAPI
import httpx

app = FastAPI()

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
            
            # On tente de décoder le JSON proprement, sinon on récupère le texte brut
            try:
                data_content = r.json()
            except:
                data_content = r.text[:1000] # Affiche les 1000 premiers caractères du texte brut
            
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
