from fastapi import FastAPI
import httpx

app = FastAPI()

@app.get("/")
def read_root():
    return {"message": "Le serveur de scraping Bet261 (Mode Playout) est en ligne !"}

@app.get("/dashboard")
async def get_dashboard(round_num: int = 20, event_id: str = "1617698"):
    # URL exacte récupérée de ta capture d'écran 17758
    target_api = f"https://hg-event-api-prod.sporty-tech.net/api/instantleagues/round/{round_num}/playout?eventCategoryId={event_id}&parentEventCategoryId=8035"
    
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
                "ronde_demandee": round_num,
                "status_http": r.status_code,
                "data": data_content
            }
            
        except Exception as e:
            return {
                "error": "Erreur lors de la récupération",
                "details": str(e)
            }
