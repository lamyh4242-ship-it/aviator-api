from fastapi import FastAPI, Query
import httpx, asyncio, random

app = FastAPI()

USER_AGENTS = [
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/151.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.4 Safari/605.1.15",
    "Mozilla/5.0 (iPhone; CPU iPhone OS 17_4 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.4 Mobile/15E148 Safari/604.1",
]

def build_headers():
    return {
        "User-Agent": random.choice(USER_AGENTS),
        "App-Version": "34727",
        "Origin": "https://bet261.mg",
        "Referer": "https://bet261.mg/",
        "Accept": "application/json, text/plain, */*",
        "Accept-Language": "fr-FR,fr;q=0.9,en;q=0.8",
        "Sec-Fetch-Site": "same-origin",
        "Sec-Fetch-Mode": "cors",
        "Sec-Fetch-Dest": "empty",
    }

BASE_URL = "https://hg-event-api-prod.sporty-tech.net/api/instantleagues"

async def fetch_json(client, url, params=None, retries=3):
    for attempt in range(retries):
        try:
            r = await client.get(url, params=params, headers=build_headers(), timeout=15.0)
            if r.status_code in (429, 500, 502, 503):
                await asyncio.sleep(2 ** attempt)
                continue
            try:
                data = r.json()
            except Exception:
                data = r.text[:2000]
            return {"status_http": r.status_code, "data": data, "url": str(r.url)}
        except Exception as e:
            if attempt == retries - 1:
                return {"error": str(e)}
            await asyncio.sleep(1)
    return {"error": "Échec après plusieurs tentatives"}

@app.get("/")
def root():
    return {"message": "API passerelle Bet261/SportyTech en ligne"}

@app.get("/dashboard")
async def dashboard(
    round_num: int = Query(..., description="Ronde actuelle"),
    event_id: str = Query("161769", description="eventCategoryId"),
    parent_id: int = 8035
):
    url = f"{BASE_URL}/round/{round_num}/playout"
    params = {"eventCategoryId": event_id, "parentEventCategoryId": parent_id}
    async with httpx.AsyncClient(http2=True, follow_redirects=True) as client:
        result = await fetch_json(client, url, params)
    return {"ronde": round_num, "event_id": event_id, "parent_id": parent_id, **result}

@app.get("/scan")
async def scan_rounds(
    event_id: str = "161769",
    parent_id: int = 8035,
    start: int = 1,
    end: int = 60
):
    """Scanne un intervalle de rondes pour trouver la ronde active"""
    results = []
    async with httpx.AsyncClient(http2=True, follow_redirects=True) as client:
        for r in range(start, end + 1):
            url = f"{BASE_URL}/round/{r}/playout"
            params = {"eventCategoryId": event_id, "parentEventCategoryId": parent_id}
            res = await fetch_json(client, url, params)
            results.append({
                "round": r,
                "status": res.get("status_http"),
                "has_data": bool(res.get("data")),
            })
            await asyncio.sleep(0.5) # éviter de surcharger l'API
    return results
