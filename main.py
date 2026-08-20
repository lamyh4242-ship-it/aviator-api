from fastapi import FastAPI
import httpx

app = FastAPI()

@app.get("/")
def read_root():
    return {"message": "Le serveur de scraping Bet261 est en ligne !"}

@app.get("/dashboard")
async def get_dashboard(round_num: int = 12, event_id: str = "1617698"):
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/151.0.0.0 Safari/537.36",
        "App-Version": "34727",
        "Origin": "https://bet261.mg",
        "Referer": "https://bet261.mg/",
        "Accept": "application/json, text/plain, */*"
    }
    
    # Le radar scanne la ronde demandée + les 10 prochaines
    rounds_to_test = list(range(round_num, round_num + 11))
    valid_rounds_data = {}
    
    async with httpx.AsyncClient() as client:
        for r_num in rounds_to_test:
            # On utilise maintenant le event_id dynamique !
            target_api = f"https://hg-event-api-prod.sporty-tech.net/api/instantleagues/round/{r_num}?eventCategoryId={event_id}&getNext=false"
            try:
                r = await client.get(target_api, headers=headers, timeout=5.0)
                if r.status_code == 200:
                    try:
                        data_content = r.json()
                        valid_rounds_data[f"ronde_{r_num}"] = data_content
                    except:
                        pass
            except Exception:
                continue 
                
    if valid_rounds_data:
        return {
            "status": "SUCCES",
            "message": f"Données récupérées pour {len(valid_rounds_data)} rondes !",
            "event_category_id_utilise": event_id,
            "rondes_trouvees": list(valid_rounds_data.keys()),
            "data": valid_rounds_data
        }
    else:
        return {
            "status": "ECHEC",
            "error": "Aucune ronde trouvée.",
            "solution": f"Vérifie que l'ID ({event_id}) et le numéro de ronde ({round_num}) sont toujours d'actualité."
        }
