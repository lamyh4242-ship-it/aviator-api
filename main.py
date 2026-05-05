from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from typing import List, Optional
import numpy as np
import pandas as pd
from sklearn.linear_model import LogisticRegression
from datetime import datetime, timedelta
import math

app = FastAPI()

# Autoriser les appels depuis n'importe quel domaine (pour test)
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Modèle de la requête
class AnalysisRequest(BaseModel):
    coefficients: List[float] # historique brut (ex: [2.16, 4.68, ...])
    last_tour_time: Optional[str] = None # "HH:MM" (heure du dernier tour, local ou UTC)
    interval_seconds: float = 30.0 # durée moyenne entre deux tours
    future_turns_poisson: int = 5 # horizon pour la loi de Poisson

# Modèle de la réponse
class AnalysisResponse(BaseModel):
    total_tours: int
    volatilite: float
    esperance: float
    sma10: Optional[float]
    sma25: Optional[float]
    sma50: Optional[float]
    alpha_pareto: float
    prob_25_bayes: float
    prob_5_bayes: float
    prob_10_bayes: float
    markov_25: float
    poisson_25_5: float
    logistic_25: float # probabilité issue de la régression logistique
    logistic_5: float
    logistic_10: float
    tours_restants_25: Optional[int]
    tours_restants_5: Optional[int]
    tours_restants_10: Optional[int]
    heure_entree_25: Optional[str]
    heure_entree_5: Optional[str]
    heure_entree_10: Optional[str]
    heatmap_bins: List[int] # comptages pour 5 plages

# ---------- Fonctions statistiques ----------
def basic_stats(arr):
    return np.mean(arr), np.std(arr), np.var(arr)

def moving_average(arr, window):
    if len(arr) < window:
        return None
    return np.mean(arr[-window:])

def pareto_alpha(arr, xm=1.0):
    if len(arr) == 0:
        return 0.0
    # MLE pour alpha : n / sum(log(x_i / xm))
    log_sum = np.sum(np.log(np.array(arr) / xm))
    return len(arr) / log_sum if log_sum > 0 else 0.0

def bayesian_prob(arr, threshold, prior=0.3):
    hits = np.sum(np.array(arr) >= threshold)
    n = len(arr)
    if n == 0:
        return prior
    p_data = hits / n
    posterior = (prior * p_data) / (prior * p_data + (1 - prior) * (1 - p_data))
    return posterior

def markov_transition(arr):
    if len(arr) < 2:
        return 0.0
    states = [0 if x < 2.5 else (1 if x < 5 else 2) for x in arr]
    trans = {
        'from0to1': 0, 'from1to1': 0, 'from2to1': 0,
        'cnt0': 0, 'cnt1': 0, 'cnt2': 0
    }
    for i in range(len(states) - 1):
        f, t = states[i], states[i+1]
        if f == 0:
            trans['cnt0'] += 1
            if t == 1:
                trans['from0to1'] += 1
        elif f == 1:
            trans['cnt1'] += 1
            if t == 1:
                trans['from1to1'] += 1
        else:
            trans['cnt2'] += 1
            if t == 1:
                trans['from2to1'] += 1
    last = states[-1]
    if last == 0:
        return (trans['from0to1'] / trans['cnt0']) if trans['cnt0'] > 0 else 0.2
    elif last == 1:
        return (trans['from1to1'] / trans['cnt1']) if trans['cnt1'] > 0 else 0.45
    else:
        return (trans['from2to1'] / trans['cnt2']) if trans['cnt2'] > 0 else 0.1

def poisson_prob(arr, threshold, future_turns=5):
    if len(arr) == 0:
        return 0.0
    hits = np.sum(np.array(arr) >= threshold)
    lambda_ = (hits / len(arr)) * future_turns
    return 1 - math.exp(-lambda_)

def logistic_prediction(arr, threshold):
    """
    Régression logistique simple : utilise les 5 derniers coefficients
    pour prédire si le prochain tour dépassera 'threshold'.
    Retourne la probabilité estimée.
    """
    if len(arr) < 6:
        return 0.0
    # Création d'un dataset : features = 5 derniers multiplicateurs, label = 1 si suivant >= threshold
    X, y = [], []
    for i in range(len(arr) - 5):
        X.append(arr[i:i+5])
        y.append(1 if arr[i+5] >= threshold else 0)
    if len(set(y)) < 2: # besoin des deux classes
        return float(np.mean(y)) if len(y) > 0 else 0.0
    model = LogisticRegression(max_iter=1000)
    model.fit(X, y)
    last_features = np.array(arr[-5:]).reshape(1, -1)
    proba = model.predict_proba(last_features)[0]
    # proba[0] pour classe 0, proba[1] pour classe 1
    return proba[1] if len(proba) > 1 else proba[0]

def estimate_turns(arr, target):
    intervals, last = [], -1
    for i, v in enumerate(arr):
        if v >= target:
            if last != -1:
                intervals.append(i - last)
            last = i
    if not intervals:
        return None
    avg = np.mean(intervals)
    since_last = len(arr) - 1 - last
    remaining = max(1, int(round(avg - since_last)))
    return remaining

def add_minutes_to_time(time_str, minutes):
    try:
        t = datetime.strptime(time_str, "%H:%M")
        t += timedelta(minutes=minutes)
        return t.strftime("%H:%M")
    except:
        return "--:--"

def heatmap_bins(arr):
    bins = [0]*5
    for v in arr:
        if v < 2: bins[0] += 1
        elif v < 4: bins[1] += 1
        elif v < 8: bins[2] += 1
        elif v < 15: bins[3] += 1
        else: bins[4] += 1
    return bins

# ---------- Endpoint principal ----------
@app.post("/analyze", response_model=AnalysisResponse)
def analyze(data: AnalysisRequest):
    coeffs = data.coefficients
    if len(coeffs) < 3:
        raise HTTPException(status_code=400, detail="Au moins 3 coefficients requis")

    arr = np.array(coeffs)
    mean_val, std_val, var_val = basic_stats(arr)

    sma10 = moving_average(arr, 10)
    sma25 = moving_average(arr, 25)
    sma50 = moving_average(arr, 50)

    alpha = pareto_alpha(arr)
    prob25 = bayesian_prob(arr, 2.5)
    prob5 = bayesian_prob(arr, 5.0)
    prob10 = bayesian_prob(arr, 10.0)

    markov_val = markov_transition(arr)
    poisson_val = poisson_prob(arr, 2.5, data.future_turns_poisson)

    # Régression logistique pour chaque seuil
    log_25 = logistic_prediction(arr, 2.5)
    log_5 = logistic_prediction(arr, 5.0)
    log_10 = logistic_prediction(arr, 10.0)

    tours_25 = estimate_turns(arr, 2.5)
    tours_5 = estimate_turns(arr, 5.0)
    tours_10 = estimate_turns(arr, 10.0)

    # Calcul des heures d'entrée
    heure_25, heure_5, heure_10 = None, None, None
    if data.last_tour_time:
        interval_min = data.interval_seconds / 60
        if tours_25 is not None:
            heure_25 = add_minutes_to_time(data.last_tour_time, tours_25 * interval_min)
        if tours_5 is not None:
            heure_5 = add_minutes_to_time(data.last_tour_time, tours_5 * interval_min)
        if tours_10 is not None:
            heure_10 = add_minutes_to_time(data.last_tour_time, tours_10 * interval_min)

    bins = heatmap_bins(arr)

    return AnalysisResponse(
        total_tours=len(coeffs),
        volatilite=round(std_val, 2),
        esperance=round(mean_val, 2),
        sma10=round(sma10, 2) if sma10 is not None else None,
        sma25=round(sma25, 2) if sma25 is not None else None,
        sma50=round(sma50, 2) if sma50 is not None else None,
        alpha_pareto=round(alpha, 2),
        prob_25_bayes=round(prob25, 4),
        prob_5_bayes=round(prob5, 4),
        prob_10_bayes=round(prob10, 4),
        markov_25=round(markov_val, 4),
        poisson_25_5=round(poisson_val, 4),
        logistic_25=round(log_25, 4),
        logistic_5=round(log_5, 4),
        logistic_10=round(log_10, 4),
        tours_restants_25=tours_25,
        tours_restants_5=tours_5,
        tours_restants_10=tours_10,
        heure_entree_25=heure_25,
        heure_entree_5=heure_5,
        heure_entree_10=heure_10,
        heatmap_bins=bins
    )
