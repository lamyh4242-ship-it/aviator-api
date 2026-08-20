from fastapi import FastAPI
import httpx

app = FastAPI()

@app.get("/")
def read_root():
    return {"message": "Le serveur de scraping Bet261 est en ligne !"}

@app.get("/dashboard")
async def get_dashboard(round_num: int = 25, event_id: str = "1617698"):
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/151.0.0.0 Safari/537.36",
        "App-Version": "34727",
        "Origin": "https://bet261.mg",
        "Referer": "https://bet261.mg/",
        "Accept": "application/json, text/plain, */*"
    }
    
    # On scanne 10 rondes
    rounds_to_test = list(range(round_num, round_num + 10))
    valid_rounds_data = {}
    debug_log = {} # NOUVEAU : On enregistre les réponses du serveur
    
    async with httpx.AsyncClient() as client:
        for r_num in rounds_to_test:
            target_api = f"https://hg-event-api-prod.sporty-tech.net/api/instantleagues/round/{r_num}?eventCategoryId={event_id}&getNext=false"
            try:
                r = await client.get(target_api, headers=headers, timeout=5.0)
                # On note le statut (ex: 200, 400, 403, 404)
                debug_log[f"test_ronde_{r_num}"] = f"HTTP {r.status_code}"
                
                if r.status_code == 200:
                    try:
                        valid_rounds_data[f"ronde_{r_num}"] = r.json()
                    except:
                        pass
            except Exception as e:
                debug_log[f"test_ronde_{r_num}"] = f"Erreur de connexion : {str(e)}"
                
    if valid_rounds_data:
        return {
            "status": "SUCCES",
            "message": f"Trouvé {len(valid_rounds_data)} rondes !",
            "rayons_x": debug_log,
            "data": valid_rounds_data
        }
    else:
        return {
            "status": "ECHEC_TOTAL",
            "rayons_x_serveur": debug_log,
            "explication": "Regarde les codes HTTP ci-dessus. Si c'est 400, c'est que les numéros sont trop vieux ou dans trop longtemps. Si c'est 403, c'est que le App-Version a changé !"
        }
