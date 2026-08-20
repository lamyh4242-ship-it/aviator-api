from fastapi import FastAPI
import httpx
import asyncio

app = FastAPI()

@app.get("/")
def read_root():
    return {"message": "Le serveur de scraping Bet261 est en ligne !"}

@app.get("/dashboard")
async def get_dashboard(round_num: int = 35):
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/151.0.0.0 Safari/537.36",
        "App-Version": "34727",
        "Origin": "https://bet261.mg",
        "Referer": "https://bet261.mg/",
        "Accept": "application/json, text/plain, */*"
    }
    
    # Le radar : on scanne la ronde demandée + les 15 prochaines (soit 30 minutes dans le futur)
    rounds_to_test = list(range(round_num, round_num + 16))
    valid_rounds_data = {}
    
    async with httpx.AsyncClient() as client:
        for r_num in rounds_to_test:
            target_api = f"https://hg-event-api-prod.sporty-tech.net/api/instantleagues/round/{r_num}?eventCategoryId=1616288&getNext=false"
            try:
                r = await client.get(target_api, headers=headers, timeout=5.0)
                # Si le serveur répond que la ronde existe (présente ou future)
                if r.status_code == 200:
                    try:
                        data_content = r.json()
                        valid_rounds_data[f"ronde_{r_num}"] = data_content
                    except:
                        pass
            except Exception:
                continue # On ignore les erreurs et on passe à la ronde suivante
                
    # On renvoie toutes les données trouvées !
    if valid_rounds_data:
        return {
            "status": "SUCCES",
            "message": f"Données récupérées pour {len(valid_rounds_data)} rondes !",
            "rondes_trouvees": list(valid_rounds_data.keys()),
            "data": valid_rounds_data
        }
    else:
        return {
            "status": "ECHEC",
            "error": "Toutes les rondes testées sont fermées.",
            "solution": "Le numéro de base est trop vieux. Augmente le 'round_num' dans l'URL (ex: mets 50 ou 60)."
        }
