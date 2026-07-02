import os
import requests
import json
from datetime import datetime, timedelta
from dotenv import load_dotenv

load_dotenv()

API_KEY = os.getenv('ODDS_API_KEY')
BASE_URL = 'https://api.the-odds-api.com/v4/sports'

# Liste des sports football (toutes les ligues majeures)
FOOTBALL_SPORTS = [
    'soccer_epl', 'soccer_la_liga', 'soccer_serie_a', 'soccer_bundesliga',
    'soccer_ligue_one', 'soccer_primeira_liga', 'soccer_eredivisie',
    'soccer_efl_champ', 'soccer_mls', 'soccer_brazil_serie_a',
    'soccer_argentina_primera_division', 'soccer_j_league', 'soccer_championship'
]  # Vous pouvez ajouter ou réduire selon vos besoins

def fetch_matches_for_sport(sport_key, regions='eu', markets='h2h,correct_score'):
    """Récupère les matchs pour un sport donné avec les marchés souhaités."""
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
            # Filtrer pour ne garder que 1xBet
            bookmakers = match.get('bookmakers', [])
            onexbet = next((b for b in bookmakers if b['key'] == '1xbet'), None)
            if not onexbet:
                continue

            # Extraire les marchés
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
    """Récupère tous les matchs de football depuis toutes les ligues."""
    all_matches = []
    for sport in FOOTBALL_SPORTS:
        print(f"Récupération des matchs pour {sport}...")
        matches = fetch_matches_for_sport(sport)
        all_matches.extend(matches)
    return all_matches

def get_cached_matches():
    """Retourne les matchs depuis le cache ou les met à jour (TTL 5 min)."""
    cache_file = 'data_cache.json'
    if os.path.exists(cache_file):
        with open(cache_file, 'r') as f:
            data = json.load(f)
            last_update = datetime.fromisoformat(data['last_update'])
            if datetime.now() - last_update < timedelta(minutes=5):
                return data['matches']

    # Sinon, on récupère et on met en cache
    matches = fetch_all_football_matches()
    with open(cache_file, 'w') as f:
        json.dump({
            'last_update': datetime.now().isoformat(),
            'matches': matches
        }, f, indent=2)
    return matches