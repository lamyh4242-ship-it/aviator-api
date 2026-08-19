from fastapi import FastAPI
import httpx

app = FastAPI()

# URL de l'API customer de SportyTech trouvée dans config.json
API_URL = "https://hg-customer-api-prod.sporty-tech.net/api/"

@app.get("/")
def read_root():
    return {"message": "Le serveur de scraping Bet261 est en ligne !"}

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
            # On interroge une route courante de l'API SportyTech (ex: configuration ou bannières/sports)
            r = await client.get(f"{API_URL}common/client-config", headers=headers, timeout=15.0)
            
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
