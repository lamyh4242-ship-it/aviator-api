import asyncio, sqlite3, time, math, json
from datetime import datetime, timedelta
from collections import Counter, deque
import numpy as np
from scipy import stats
from scipy.stats import poisson, chi2_contingency, entropy
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
import httpx

# ------------------------------------------------------------------------------------
# CONFIGURATION
# ------------------------------------------------------------------------------------
DATABASE = "can_pro_analyzer.db"
BASE_API = "https://hg-event-api-prod.sporty-tech.net/api/instantleagues"
LEAGUE_KEYWORD = "Africa" # pour identifier la CAN
HISTORY_DEPTH = 30 # nombre de derniers matchs utilisés pour les stats
UPDATE_INTERVAL = 120 # secondes entre deux scans

app = FastAPI()
app.add_middleware(CORSMiddleware, allow_origins=["*"])

# ------------------------------------------------------------------------------------
# BASE DE DONNÉES
# ------------------------------------------------------------------------------------
def init_db():
    conn = sqlite3.connect(DATABASE); c = conn.cursor()
    # Matchs joués (résultats réels)
    c.execute("""
        CREATE TABLE IF NOT EXISTS matches (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            home TEXT,
            away TEXT,
            home_score INTEGER,
            away_score INTEGER,
            timestamp TEXT,
            league_id TEXT
        )
    """)
    # Prédictions effectuées pour validation
    c.execute("""
        CREATE TABLE IF NOT EXISTS predictions (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            home TEXT,
            away TEXT,
            predicted_scores TEXT,
            confidence REAL,
            model_scores TEXT,
            timestamp TEXT,
            actual_home_score INTEGER,
            actual_away_score INTEGER,
            validated INTEGER DEFAULT 0
        )
    """)
    # Cache des derniers scans pour analyse cyclique
    c.execute("""
        CREATE TABLE IF NOT EXISTS team_cycle (
            team TEXT PRIMARY KEY,
            last_results TEXT,
            tension REAL
        )
    """)
    conn.commit(); conn.close()

init_db()

# ------------------------------------------------------------------------------------
# OUTILS D'EXTRACTION API
# ------------------------------------------------------------------------------------
async def fetch_json(url):
    async with httpx.AsyncClient() as client:
        resp = await client.get(url)
        return resp.json()

async def get_can_league_id():
    leagues = await fetch_json(BASE_API)
    for l in leagues:
        if LEAGUE_KEYWORD.lower() in l.get("name", "").lower():
            return l["id"]
    return leagues[0]["id"] if leagues else None

async def fetch_live_matches():
    """Récupère les matchs en cours / à venir de la CAN."""
    league_id = await get_can_league_id()
    if not league_id:
        return []
    data = await fetch_json(f"{BASE_API}/playout?eventCategoryId={league_id}")
    return data.get("matches", [])

async def fetch_finished_matches():
    """
    Récupère les matchs terminés récemment.
    L'API ne fournit pas toujours un filtre direct, on exploite les scores non nuls.
    """
    league_id = await get_can_league_id()
    if not league_id:
        return []
    # On demande beaucoup de matchs, on filtrera ceux avec score
    data = await fetch_json(f"{BASE_API}/playout?eventCategoryId={league_id}&limit=100")
    matches = data.get("matches", [])
    finished = [m for m in matches if m.get("homeScore") is not None and m.get("awayScore") is not None]
    return finished

# ------------------------------------------------------------------------------------
# GESTION DE L'HISTORIQUE (PERSISTANCE)
# ------------------------------------------------------------------------------------
def save_finished_matches(matches):
    conn = sqlite3.connect(DATABASE); c = conn.cursor()
    for m in matches:
        home = m["homeTeamName"]
        away = m["awayTeamName"]
        hs = m["homeScore"]
        aws = m["awayScore"]
        ts = m.get("startTime", datetime.utcnow().isoformat())
        lid = m.get("eventCategoryId", "")
        # Évite les doublons approximatifs
        c.execute("SELECT id FROM matches WHERE home=? AND away=? AND timestamp=?",
                  (home, away, ts))
        if not c.fetchone():
            c.execute("INSERT INTO matches (home, away, home_score, away_score, timestamp, league_id) VALUES (?,?,?,?,?,?)",
                      (home, away, hs, aws, ts, lid))
    conn.commit(); conn.close()

def get_team_history(team, limit=HISTORY_DEPTH):
    conn = sqlite3.connect(DATABASE); c = conn.cursor()
    c.execute("""
        SELECT home, away, home_score, away_score FROM matches
        WHERE home=? OR away=?
        ORDER BY timestamp DESC LIMIT ?
    """, (team, team, limit))
    rows = c.fetchall()
    conn.close()
    history = []
    for home, away, hs, aws in rows:
        if home == team:
            history.append({"opponent": away, "gf": hs, "ga": aws})
        else:
            history.append({"opponent": home, "gf": aws, "ga": hs})
    return history

# ------------------------------------------------------------------------------------
# MODÈLES MATHÉMATIQUES (12 indicateurs)
# ------------------------------------------------------------------------------------
def poisson_prediction(avg_goals_for, avg_goals_against, opp_avg_against, opp_avg_for):
    """
    1. Loi de Poisson pour les scores exacts.
    Retourne la distribution de probabilité pour les scores 0-0 à 3-3.
    """
    lambda_home = avg_goals_for * opp_avg_against
    lambda_away = opp_avg_for * avg_goals_against
    probs = {}
    for i in range(4):
        for j in range(4):
            p = poisson.pmf(i, lambda_home) * poisson.pmf(j, lambda_away)
            probs[f"{i}-{j}"] = round(p, 4)
    return probs

def chi_square_uniformity(history_scores):
    """
    2. Test du Chi² : vérifie si la distribution des scores suit une loi uniforme
    Retourne la p-value (faible = rejet de l'uniformité = pattern détecté)
    """
    score_counts = Counter(history_scores)
    if len(score_counts) < 2:
        return 0.5
    observed = list(score_counts.values())
    expected = [sum(observed) / len(observed)] * len(observed)
    try:
        _, p = stats.chisquare(observed, f_exp=expected)
    except:
        p = 0.5
    return p

def shannon_entropy(sequence):
    """
    3. Entropie de Shannon : mesure la prévisibilité
    Forte entropie = aléatoire, faible = patterns.
    """
    if not sequence:
        return 0
    value_counts = Counter(sequence)
    total = len(sequence)
    probs = [count / total for count in value_counts.values()]
    return entropy(probs, base=2)

def markov_transition(sequence, states):
    """
    4. Matrice de transition de Markov (1er ordre)
    Retourne la probabilité de l'état suivant.
    """
    if len(sequence) < 2:
        return None
    seq = sequence[-2:] # on prend les deux derniers
    transitions = {}
    for i in range(len(sequence)-1):
        current = sequence[i]
        nxt = sequence[i+1]
        transitions.setdefault(current, []).append(nxt)
    if seq[-1] not in transitions:
        return None
    next_counts = Counter(transitions[seq[-1]])
    total = sum(next_counts.values())
    return {k: v/total for k, v in next_counts.items()}

def linear_regression_trend(values):
    """
    5. Régression linéaire pour détecter une tendance (hausse/baisse des buts)
    Retourne la pente et la p-value.
    """
    if len(values) < 3:
        return 0, 1.0
    x = np.arange(len(values))
    slope, intercept, r_value, p_value, _ = stats.linregress(x, values)
    return slope, p_value

def autocorrelation(series, lag=1):
    """
    6. Autocorrélation pour détecter des cycles.
    """
    if len(series) < lag + 2:
        return 0
    n = len(series)
    mean = np.mean(series)
    num = sum((series[i] - mean) * (series[i+lag] - mean) for i in range(n-lag))
    den = sum((series[i] - mean)**2 for i in range(n))
    return num/den if den else 0

def runs_test(sequence):
    """
    7. Test de Wald-Wolfowitz (runs) pour détecter le non-aléatoire.
    Retourne la p-value.
    """
    # Simplification : on transforme en binaire (au-dessus/médiane)
    if not sequence:
        return 0.5
    median = np.median(sequence)
    binary = [1 if x > median else 0 for x in sequence]
    runs = 1 + sum(1 for i in range(1, len(binary)) if binary[i] != binary[i-1])
    n1 = sum(binary)
    n2 = len(binary) - n1
    if n1 == 0 or n2 == 0:
        return 0.01
    mean_runs = 1 + 2*n1*n2/(n1+n2)
    std_runs = math.sqrt(2*n1*n2*(2*n1*n2 - n1 - n2) / ((n1+n2)**2 * (n1+n2-1)))
    if std_runs == 0:
        return 0.5
    z = (runs - mean_runs) / std_runs
    return 2 * (1 - stats.norm.cdf(abs(z)))

def confidence_interval_boost(ic_raw):
    """
    8. Indice de tension amélioré (ton I_c) normalisé + boost cyclique.
    """
    return min(round(ic_raw * 50, 1), 96)

def recent_form_index(history, last=5):
    """
    9. Forme récente : moyenne de buts marqués sur les 5 derniers matchs.
    """
    recent = history[:last]
    if not recent:
        return 0
    return sum(h["gf"] for h in recent) / len(recent)

def defensive_strength(history):
    """10. Solidité défensive : moyenne de buts encaissés."""
    if not history:
        return 0
    return sum(h["ga"] for h in history) / len(history)

def hot_cold_score(team, all_teams_history):
    """
    11. Détection équipes en forme (hot/cold) basée sur la tendance récente.
    """
    hist = all_teams_history.get(team, [])
    if len(hist) < 5:
        return 0
    recent_gf = [h["gf"] for h in hist[:5]]
    slope, _ = linear_regression_trend(recent_gf)
    return slope

def cycle_detection_score(sequence):
    """
    12. Score composite de cyclicité (runs test, autocorr, entropie).
    """
    p_runs = runs_test(sequence)
    auto = abs(autocorrelation(sequence, 1))
    ent = shannon_entropy(sequence)
    # Combine : faible p-value, forte autocorr, faible entropie = forte cyclicité
    score = (1 - p_runs) * 0.4 + auto * 0.4 + (1 - min(ent/3, 1)) * 0.2
    return min(score, 1.0)

# ------------------------------------------------------------------------------------
# PREDICTEUR GLOBAL
# ------------------------------------------------------------------------------------
def compute_composite_prediction(home, away, all_histories):
    """
    Pour un match donné, calcule tous les indicateurs et un score composite de confiance.
    Retourne :
        confidence : pourcentage
        suggested_scores : liste des 3 meilleurs scores
        model_details : dict des contributions de chaque modèle
    """
    home_hist = all_histories.get(home, [])
    away_hist = all_histories.get(away, [])
    # Statistiques de base
    if home_hist:
        home_avg_gf = sum(m["gf"] for m in home_hist)/len(home_hist)
        home_avg_ga = sum(m["ga"] for m in home_hist)/len(home_hist)
    else:
        home_avg_gf = home_avg_ga = 1.0
    if away_hist:
        away_avg_gf = sum(m["gf"] for m in away_hist)/len(away_hist)
        away_avg_ga = sum(m["ga"] for m in away_hist)/len(away_hist)
    else:
        away_avg_gf = away_avg_ga = 1.0

    # 1. Poisson
    poisson_probs = poisson_prediction(home_avg_gf, home_avg_ga, away_avg_ga, away_avg_gf)
    top_scores = sorted(poisson_probs.items(), key=lambda x: x[1], reverse=True)[:3]

    # 2. Chi² sur la distribution des scores de l'équipe à domicile
    home_score_dist = [f"{m['gf']}-{m['ga']}" for m in home_hist]
    chi_p = chi_square_uniformity(home_score_dist)

    # 3. Entropie des résultats domicile
    home_results = ["W" if m["gf"] > m["ga"] else "D" if m["gf"] == m["ga"] else "L" for m in home_hist]
    ent_home = shannon_entropy(home_results)

    # 4. Markov pour le résultat domicile
    markov_probs = markov_transition(home_results, states=["W","D","L"])
    markov_confidence = 0
    if markov_probs and len(home_results) >= 2:
        last_state = home_results[-1]
        next_prob = markov_probs.get(last_state, 0)
        markov_confidence = max(markov_probs.values()) if markov_probs else 0

    # 5. Tendance des buts marqués à domicile
    home_gf_seq = [m["gf"] for m in home_hist]
    slope, p_slope = linear_regression_trend(home_gf_seq)
    trend_strength = abs(slope) * (1 - min(p_slope, 0.5)) * 2

    # 6. Autocorrélation des buts marqués
    auto_gf = abs(autocorrelation(home_gf_seq, 1))

    # 7. Runs test sur GF
    runs_p = runs_test(home_gf_seq)

    # 8. Tension (Ic) maison
    ic_raw = len(home_hist) * 0.1 # simplifié, à ajuster avec vraie tension
    tension_conf = confidence_interval_boost(ic_raw)

    # 9. Forme récente
    form_home = recent_form_index(home_hist)

    # 10. Défense
    def_away = defensive_strength(away_hist)

    # 11. Hot/cold
    hot_score_home = hot_cold_score(home, all_histories)
    hot_score_away = hot_cold_score(away, all_histories)
    hot_diff = hot_score_home - hot_score_away

    # 12. Cyclicité
    cycle_score = cycle_detection_score(home_gf_seq)

    # SCORE COMPOSITE (pondération empirique)
    # Basé sur : plus les indicateurs sont "anormaux", plus la confiance monte
    base_confidence = (
        0.15 * (1 - min(chi_p, 0.9)) + # faible p-value = pattern
        0.15 * (1 - min(ent_home/3, 1)) + # faible entropie
        0.10 * markov_confidence +
        0.10 * trend_strength +
        0.10 * auto_gf +
        0.10 * (1 - runs_p) +
        0.10 * cycle_score +
        0.10 * (abs(hot_diff) / 2) +
        0.05 * (form_home / 3) +
        0.05 * (1 - min(def_away/3, 1))
    )
    # Ajustement empirique pour ramener entre 50 et 96%
    confidence = min(50 + base_confidence * 50, 96)

    # Scores suggérés : on prend les 3 meilleurs de Poisson, mais on peut forcer 1-1, 2-1, 1-0 si forte tension
    if tension_conf > 80:
        suggested_scores = ["1-1", "2-1", "1-0"]
    else:
        suggested_scores = [s[0] for s in top_scores]

    return {
        "confidence": round(confidence, 1),
        "suggested_scores": suggested_scores,
        "details": {
            "chi_p": round(chi_p, 3),
            "entropy": round(ent_home, 3),
            "markov": round(markov_confidence, 3),
            "trend_strength": round(trend_strength, 3),
            "autocorr": round(auto_gf, 3),
            "runs_p": round(runs_p, 3),
            "cycle_score": round(cycle_score, 3),
            "hot_diff": round(hot_diff, 3),
            "tension": tension_conf
        }
    }

# ------------------------------------------------------------------------------------
# VALIDATION AUTOMATIQUE
# ------------------------------------------------------------------------------------
async def validate_predictions():
    """Vérifie les prédictions passées et enregistre les résultats réels."""
    finished = await fetch_finished_matches()
    save_finished_matches(finished)
    conn = sqlite3.connect(DATABASE); c = conn.cursor()
    c.execute("SELECT id, home, away, predicted_scores FROM predictions WHERE validated=0")
    preds = c.fetchall()
    for pid, home, away, pred_scores_json in preds:
        # Cherche le match correspondant dans les résultats
        for m in finished:
            if m["homeTeamName"] == home and m["awayTeamName"] == away:
                actual = f"{m['homeScore']}-{m['awayScore']}"
                pred_scores = json.loads(pred_scores_json)
                success = 1 if actual in pred_scores else 0
                c.execute("""
                    UPDATE predictions
                    SET actual_home_score=?, actual_away_score=?, validated=?, success=?
                    WHERE id=?
                """, (m["homeScore"], m["awayScore"], 1, success, pid))
                break
    conn.commit(); conn.close()

# ------------------------------------------------------------------------------------
# ENDPOINT PRINCIPAL
# ------------------------------------------------------------------------------------
@app.get("/dashboard")
async def dashboard():
    # 1. Récupère les matchs en direct
    live_matches = await fetch_live_matches()
    # 2. Récupère l'historique de toutes les équipes impliquées
    all_teams = set()
    for m in live_matches:
        all_teams.add(m["homeTeamName"])
        all_teams.add(m["awayTeamName"])
    histories = {team: get_team_history(team) for team in all_teams}

    predictions = []
    conn = sqlite3.connect(DATABASE); c = conn.cursor()
    for m in live_matches:
        home = m["homeTeamName"]
        away = m["awayTeamName"]
        pred = compute_composite_prediction(home, away, histories)
        # Sauvegarde de la prédiction
        c.execute("""
            INSERT INTO predictions (home, away, predicted_scores, confidence, model_scores, timestamp)
            VALUES (?, ?, ?, ?, ?, ?)
        """, (
            home, away,
            json.dumps(pred["suggested_scores"]),
            pred["confidence"],
            json.dumps(pred["details"]),
            datetime.utcnow().isoformat()
        ))
        predictions.append({
            "home": home,
            "away": away,
            "confidence": pred["confidence"],
            "scores": pred["suggested_scores"],
            "details": pred["details"]
        })
    conn.commit(); conn.close()

    # Trie par confiance décroissante
    predictions.sort(key=lambda x: x["confidence"], reverse=True)
    return predictions

# ------------------------------------------------------------------------------------
# TÂCHE DE VALIDATION EN ARRIÈRE-PLAN
# ------------------------------------------------------------------------------------
async def validation_loop():
    while True:
        await asyncio.sleep(300) # toutes les 5 minutes
        try:
            await validate_predictions()
        except Exception as e:
            print(f"Validation error: {e}")

@app.on_event("startup")
async def startup_event():
    asyncio.create_task(validation_loop())

# ------------------------------------------------------------------------------------
if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)
