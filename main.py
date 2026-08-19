from fastapi import FastAPI
import httpx
import asyncio

app = FastAPI()

BASE_API_URL = "https://bet261.mg/virtual"

@app.get("/")
def read_root():
    return {"message": "Le serveur Python fonctionne ! Il ne trouve juste pas ton fichier index.html, mais l'API est en ligne."}

@app.get("/dashboard")
async def get_dashboard():
    # Ces 'headers' servent à imiter un vrai navigateur (Anti-bot)
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
        "Accept": "application/json, text/plain, */*",
        "Accept-Language": "fr-FR,fr;q=0.9,en-US;q=0.8,en;q=0.7",
        "Connection": "keep-alive"
    }
    
    async with httpx.AsyncClient() as client:
        try:
            # On envoie la requête avec notre "déguisement"
            r = await client.get(BASE_API_URL, headers=headers, timeout=15.0)
            
            # --- LIGNES DE DEBUG POUR RENDER ---
            print("=== DEBUT DU DEBUG ===")
            print(f"Statut HTTP reçu : {r.status_code}")
            print(f"Contenu brut (500 premiers caractères) : {r.text[:500]}")
            print("=== FIN DU DEBUG ===")
            # -----------------------------------

            # Si le site renvoie une erreur (comme 403 Forbidden)
            r.raise_for_status() 
            
            # On essaie de convertir la réponse en JSON
            data = r.json()
            
            # Note: Si tu avais un filtre pour "england", "premier" etc, 
            # tu peux le remettre ici. Pour l'instant, on renvoie tout pour tester.
            return data
            
        except Exception as e:
            print(f"ERREUR CRITIQUE : {str(e)}")
            return {
                "error": "Impossible de récupérer les données",
                "details": str(e),
                "conseil": "Va voir l'onglet 'Logs' sur Render pour comprendre l'erreur."
            }
