import os
import requests
from datetime import datetime, timedelta
from dotenv import load_dotenv

load_dotenv()

API_KEY = os.getenv('ODDS_API_KEY')
BASE_URL = 'https://api.the-odds-api.com/v4/sports'

# Liste des ligues (réduisez-la si vous êtes sur le plan gratuit de Render pour éviter les timeouts)
FOOTBALL_SPORTS = [
    'soccer_epl', 'soccer_la_liga', 'soccer_serie_a', 'soccer_bundesliga',
    'soccer_ligue_one', 'soccer_primeira_liga', 'soccer_eredivisie',
]

# Cache mémoire
_cached_matches = []
_last_update = None
CACHE_TTL = 300  # 5 minutes

def fetch_matches_for_sport(sport_key, regions='eu', markets='h2h,correct_score'):
    url = f'{BASE_URL}/{sport_key}/odds'
    params = {
        'apiKey': API_KEY,
        'regions': regions,
        'markets': markets,
        'oddsFormat': 'decimal',
        'dateFormat': 'iso',
    }
    try:
        response = requests.get(url, params=params)
        response.raise_for_status()
        data = response.json()
        matches = []
        for match in data:
            bookmakers = match.get('bookmakers', [])
            onexbet = next((b for b in bookmakers if b['key'] == '1xbet'), None)
            if not onexbet:
                continue
            markets_data = {m['key']: m['outcomes'] for m in onexbet.get('markets', [])}
            if 'h2h' not in markets_data or 'correct_score' not in markets_data:
                continue
            matches.append({
                'id': match['id'],
                'sport_key': match['sport_key'],
                'commence_time': match['commence_time'],
                'home_team': match['home_team'],
                'away_team': match['away_team'],
                'h2h': markets_data['h2h'],
                'correct_score': markets_data['correct_score'],
            })
        return matches
    except Exception as e:
        print(f"Erreur pour {sport_key}: {e}")
        return []

def fetch_all_football_matches():
    all_matches = []
    for sport in FOOTBALL_SPORTS:
        print(f"Récupération des matchs pour {sport}...")
        all_matches.extend(fetch_matches_for_sport(sport))
    return all_matches

def get_cached_matches():
    global _cached_matches, _last_update
    now = datetime.now()
    if _last_update and (now - _last_update).seconds < CACHE_TTL:
        return _cached_matches
    
    # Sinon, on rafraîchit
    print("Rafraîchissement du cache en mémoire...")
    _cached_matches = fetch_all_football_matches()
    _last_update = now
    return _cached_matches