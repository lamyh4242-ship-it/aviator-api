from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import PlainTextResponse
from pydantic import BaseModel
from typing import List, Optional
import numpy as np
import sqlite3
import math
import datetime
import os

# --- Base de données SQLite dans /tmp (autorisé en écriture sur Render) ---
DATABASE = "/tmp/virtual_matches.db"

def init_db():
    conn = sqlite3.connect(DATABASE)
    cur = conn.cursor()
    cur.execute("""
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

app = FastAPI()
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_credentials=True, allow_methods=["*"], allow_headers=["*"])

# --- Modèles ---
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

# --- Fonctions statistiques ---
def get_team_history(team: str) -> list:
    conn = sqlite3.connect(DATABASE)
    cur = conn.cursor()
    cur.execute("SELECT odds, opponent, timestamp FROM matches WHERE team=? ORDER BY timestamp DESC LIMIT 500", (team,))
    rows = cur.fetchall()
    conn.close()
    return [{"odds": row[0], "opponent": row[1], "timestamp": row[2]} for row in rows]

def compute_metrics(team: str) -> dict:
    history = get_team_history(team)
    if not history:
        return {"matches_since": 0, "mean_interval": 350, "last_high_odds": None, "losses_streak": 0}

    # Dernière cote ≥ 50
    high_odds = [m for m in history if m["odds"] and m["odds"] >= 50]
    if high_odds:
        last_high = high_odds[0]
        last_high_odds = last_high["odds"]
        # Nombre de matchs depuis cette cote élevée
        matches_since = next((i for i, m in enumerate(history) if m["odds"] and m["odds"] >= 50), len(history))
    else:
        last_high_odds = None
        matches_since = len(history)

    # Intervalle moyen entre cotes ≥ 50
    high_indices = [i for i, m in enumerate(history) if m["odds"] and m["odds"] >= 50]
    if len(high_indices) >= 2:
        intervals = [high_indices[i] - high_indices[i+1] for i in range(len(high_indices)-1)]
        mean_interval = np.mean(intervals)
    else:
        mean_interval = 350 # défaut basé sur observation approximative

    losses_streak = matches_since # approximation

    return {
        "matches_since": matches_since,
        "mean_interval": mean_interval,
        "last_high_odds": last_high_odds,
        "losses_streak": losses_streak
    }

def poisson_prob(lam, k):
    return (lam**k * math.exp(-lam)) / math.factorial(k)

# --- Endpoints ---
@app.post("/submit-match")
def submit_match(match: MatchInput):
    conn = sqlite3.connect(DATABASE)
    cur = conn.cursor()
    cur.execute("INSERT INTO matches (team, opponent, odds, score) VALUES (?,?,?,?)",
                (match.team, match.opponent, match.odds, match.score))
    conn.commit()
    conn.close()

    metrics = compute_metrics(match.team)
    E_act = metrics["matches_since"]
    E_moy = metrics["mean_interval"]
    N_pertes = metrics["losses_streak"]
    Ic = (E_act / E_moy) * math.log(N_pertes + 1) if E_moy > 0 else 0.0
    alert = Ic >= 1.8
    return {"message": "Match enregistré", "Ic": round(Ic, 3), "alert": alert}

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

    lam = 0.8 # moyenne de buts (à ajuster)
    prob_2 = poisson_prob(lam, 2)

    return StatusResponse(
        team=team,
        last_high_odds=metrics["last_high_odds"],
        matches_since_last_high=E_act,
        mean_interval=round(E_moy, 1),
        current_Ic=round(Ic, 3),
        zone=zone,
        prob_2_goals=round(prob_2, 4)
    )

@app.get("/dashboard")
def dashboard():
    benin = get_status("Bénin")
    guinee = get_status("Guinée Équatoriale")
    return {"Bénin": benin, "Guinée Équatoriale": guinee}

# Service Worker pour notifications
@app.get("/sw.js", response_class=PlainTextResponse)
def service_worker():
    return """
self.addEventListener('push', function(event) {
  const data = event.data.json();
  const options = {
    body: data.body,
    icon: 'data:image/svg+xml,%3Csvg xmlns=\"http://www.w3.org/2000/svg\" viewBox=\"0 0 24 24\" fill=\"%23f5b042\"%3E%3Cpath d=\"M12 2L2 22h8v-6h4v6h8L12 2z\"/%3E%3C/svg%3E'
  };
  event.waitUntil(
    self.registration.showNotification(data.title, options)
  );
});
"""
