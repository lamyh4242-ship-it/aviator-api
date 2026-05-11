# main.py – Backend FastAPI avec scraper intégré (simulé) + calculs statistiques
# Déploiement : Render, gratuit. Base SQLite dans /tmp.

import asyncio
import math
import os
import sqlite3
import time
from datetime import datetime
from typing import Optional, List

import httpx
import numpy as np
from fastapi import FastAPI, HTTPException, BackgroundTasks
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import PlainTextResponse
from pydantic import BaseModel

# ==================== CONFIGURATION ====================
DATABASE = "/tmp/virtual_matches.db"
BET261_URL = "https://bet261.mg" # À adapter avec l'URL réelle des données JSON
SCRAPE_INTERVAL = 30 # secondes entre chaque tentative de récupération automatique

# ==================== BASE DE DONNÉES ====================
def init_db():
    conn = sqlite3.connect(DATABASE)
    c = conn.cursor()
    c.execute("""
        CREATE TABLE IF NOT EXISTS matches (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            team TEXT NOT NULL,
            opponent TEXT NOT NULL,
            odds REAL,
            score TEXT,
            timestamp DATETIME DEFAULT CURRENT_TIMESTAMP
        )
    """)
    conn.commit()
    conn.close()

init_db()

# ==================== MODÈLES ====================
class MatchInput(BaseModel):
    team: str # "Bénin" ou "Guinée Équatoriale"
    opponent: str
    odds: float
    score: Optional[str] = None

class StatusResponse(BaseModel):
    team: str
    last_high_odds: Optional[float]
    matches_since_last_high: int
    mean_interval: float
    current_Ic: float
    zone: str
    prob_2_goals: float
    next_adversary: Optional[str]
    next_odds: Optional[float]

# ==================== FONCTIONS STATISTIQUES ====================
def get_team_history(team: str) -> list:
    conn = sqlite3.connect(DATABASE)
    c = conn.cursor()
    c.execute("SELECT odds, opponent, timestamp FROM matches WHERE team=? ORDER BY timestamp DESC LIMIT 500", (team,))
    rows = c.fetchall()
    conn.close()
    return [{"odds": row[0], "opponent": row[1], "timestamp": row[2]} for row in rows]

def compute_metrics(team: str) -> dict:
    history = get_team_history(team)
    if not history:
        return {
            "matches_since": 0,
            "mean_interval": 350,
            "last_high_odds": None,
            "losses_streak": 0,
            "next_opponent": None,
            "next_odds": None
        }
    # Prochain match = le plus récent (dernier inséré)
    last_match = history[0]
    next_opponent = last_match["opponent"]
    next_odds = last_match["odds"]

    # Cotes ≥ 50
    high = [m for m in history if m["odds"] and m["odds"] >= 50]
    if high:
        last_high_odds = high[0]["odds"]
        matches_since = next((i for i, m in enumerate(history) if m["odds"] and m["odds"] >= 50), len(history))
    else:
        last_high_odds = None
        matches_since = len(history)

    high_indices = [i for i, m in enumerate(history) if m["odds"] and m["odds"] >= 50]
    if len(high_indices) >= 2:
        intervals = [high_indices[i] - high_indices[i+1] for i in range(len(high_indices)-1)]
        mean_interval = np.mean(intervals)
    else:
        mean_interval = 350

    return {
        "matches_since": matches_since,
        "mean_interval": mean_interval,
        "last_high_odds": last_high_odds,
        "losses_streak": matches_since,
        "next_opponent": next_opponent,
        "next_odds": next_odds
    }

def poisson_prob(lam, k):
    return (lam**k * math.exp(-lam)) / math.factorial(k)

# ==================== SCRAPER (SIMULÉ POUR DÉMO) ====================
async def scrape_bet261():
    """
    Exemple de scraper. En conditions réelles, il faudrait :
    - Identifier l'endpoint JSON des matchs virtuels (via les outils dev du navigateur)
    - Utiliser des headers réalistes
    - Gérer les rotations de proxy si nécessaire
    Pour la démo, on simule une récupération.
    """
    # Liste de matchs simulés (en vrai, on ferait un appel HTTP)
    simulated_matches = [
        {"team": "Bénin", "opponent": "Nigeria", "odds": 100, "score": None},
        {"team": "Guinée Équatoriale", "opponent": "Cameroun", "odds": 50, "score": None},
        # Ajoutez ici de vrais appels API
    ]
    # Exemple d'appel avec httpx (si vous avez l'URL exacte)
    # async with httpx.AsyncClient() as client:
    # resp = await client.get(f"{BET261_URL}/api/virtual-matches?league=CAN")
    # data = resp.json()
    # for match in data:
    # # parser et insérer
    # pass
    # Pour l'instant, on insère les simulés
    conn = sqlite3.connect(DATABASE)
    c = conn.cursor()
    for m in simulated_matches:
        c.execute("INSERT INTO matches (team, opponent, odds, score) VALUES (?,?,?,?)",
                  (m["team"], m["opponent"], m["odds"], m["score"]))
    conn.commit()
    conn.close()

# ==================== TÂCHE DE FOND (BACKGROUND) ====================
async def periodic_scraper():
    while True:
        try:
            await scrape_bet261()
        except Exception as e:
            print(f"Scraper error: {e}")
        await asyncio.sleep(SCRAPE_INTERVAL)

# ==================== FASTAPI ====================
app = FastAPI()
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_credentials=True, allow_methods=["*"], allow_headers=["*"])

@app.on_event("startup")
async def startup_event():
    asyncio.create_task(periodic_scraper())

@app.post("/submit-match")
def submit_match(match: MatchInput):
    conn = sqlite3.connect(DATABASE)
    c = conn.cursor()
    c.execute("INSERT INTO matches (team, opponent, odds, score) VALUES (?,?,?,?)",
              (match.team, match.opponent, match.odds, match.score))
    conn.commit()
    conn.close()
    # Recalculer les métriques pour renvoyer l'Ic
    metrics = compute_metrics(match.team)
    E_act = metrics["matches_since"]
    E_moy = metrics["mean_interval"]
    N_pertes = metrics["losses_streak"]
    Ic = (E_act / E_moy) * math.log(N_pertes + 1) if E_moy > 0 else 0.0
    return {"message": "Match enregistré", "Ic": round(Ic, 3), "alert": Ic >= 1.8}

@app.get("/status/{team}", response_model=StatusResponse)
def get_status(team: str):
    if team not in ["Bénin", "Guinée Équatoriale"]:
        raise HTTPException(status_code=400, detail="Équipe non supportée")
    metrics = compute_metrics(team)
    E_act = metrics["matches_since"]
    E_moy = metrics["mean_interval"]
    N_pertes = metrics["losses_streak"]
    Ic = (E_act / E_moy) * math.log(N_pertes + 1) if E_moy > 0 else 0.0

    if Ic < 1.0:
        zone = "froide"
    elif Ic < 1.8:
        zone = "observation"
    else:
        zone = "chasse"

    lam = 0.8 # moyenne buts Bénin/Guinée Éq.
    prob_2 = poisson_prob(lam, 2)

    return StatusResponse(
        team=team,
        last_high_odds=metrics["last_high_odds"],
        matches_since_last_high=E_act,
        mean_interval=round(E_moy, 1),
        current_Ic=round(Ic, 3),
        zone=zone,
        prob_2_goals=round(prob_2, 4),
        next_adversary=metrics["next_opponent"],
        next_odds=metrics["next_odds"]
    )

@app.get("/dashboard")
def dashboard():
    return {
        "Bénin": get_status("Bénin"),
        "Guinée Équatoriale": get_status("Guinée Équatoriale")
    }

# Service Worker pour notifications
@app.get("/sw.js", response_class=PlainTextResponse)
def service_worker():
    return """
self.addEventListener('push', function(event) {
  const data = event.data.json();
  const options = {
    body: data.body,
    icon: 'data:image/svg+xml,%3Csvg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" fill="%23f5b042"%3E%3Cpath d="M12 2L2 22h8v-6h4v6h8L12 2z"/%3E%3C/svg%3E'
  };
  event.waitUntil(
    self.registration.showNotification(data.title, options)
  );
});
"""
