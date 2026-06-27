import time
import threading
import requests

LATEST_LIVE_DATA = []
data_lock = threading.Lock()

# Ta clé API active pour The Odds API
THE_ODDS_API_KEY = "32e78e99bae8ee48e449cfc72d94dd1b"
ODDS_API_URL = f"https://api.the-odds-api.com/v4/sports/soccer/odds/?apiKey={THE_ODDS_API_KEY}&regions=eu&markets=h2h&oddsFormat=decimal"

def fetch_and_parse():
    global LATEST_LIVE_DATA
    try:
        response = requests.get(ODDS_API_URL, timeout=10)
        if response.status_code == 200:
            raw_matches = response.json()
            real_matches = []
            
            for i, match in enumerate(raw_matches):
                eq1 = match.get("home_team")
                eq2 = match.get("away_team")
                bookmakers = match.get("bookmakers", [])
                v1, x, v2 = 1.0, 1.0, 1.0
                
                if bookmakers:
                    markets = bookmakers[0].get("markets", [])
                    if markets:
                        outcomes = markets[0].get("outcomes", [])
                        for outcome in outcomes:
                            name = outcome.get("name")
                            price = outcome.get("price", 1.0)
                            if name == eq1: v1 = price
                            elif name == eq2: v2 = price
                            elif name in ["Draw", "Nul", "X"]: x = price

                real_matches.append({
                    "id": match.get("id", f"odds_{i}"),
                    "sport": "Football",
                    "championnat": match.get("sport_title", "Football"),
                    "equipe_domicile": eq1,
                    "equipe_exterieur": eq2,
                    "statut": "En direct",
                    "score_actuel": "0 - 0",
                    "probabilites": {
                        "V1": round(1 / v1, 2) if v1 > 1 else 0.33,
                        "X": round(1 / x, 2) if x > 1 else 0.33,
                        "V2": round(1 / v2, 2) if v2 > 1 else 0.33
                    },
                    "cotes": {"V1": v1, "X": x, "V2": v2}
                })
                
            with data_lock:
                LATEST_LIVE_DATA = real_matches
            print(f"🔥 [{time.strftime('%H:%M:%S')}] {len(real_matches)} matchs récupérés. Crédits restants : {response.headers.get('x-requests-remaining', 'Inconnu')}")
        else:
            print(f"⚠️ Erreur API externe : Code {response.status_code}")
    except Exception as e:
        print(f"❌ Erreur réseau ou parsing : {e}")

def get_cached_data():
    with data_lock:
        return LATEST_LIVE_DATA