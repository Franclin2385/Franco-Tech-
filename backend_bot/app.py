import os
import threading
import time
from flask import Flask, jsonify
from flask_cors import CORS
from scraper import get_cached_matches

app = Flask(__name__)
CORS(app)

# Fonction de rafraîchissement en arrière-plan
def background_refresh():
    while True:
        print("Thread de fond : rafraîchissement du cache...")
        get_cached_matches()  # Force le rafraîchissement
        time.sleep(300)  # 5 minutes

# Démarrer le thread au lancement
thread = threading.Thread(target=background_refresh, daemon=True)
thread.start()

@app.route('/api/matches', methods=['GET'])
def matches():
    # Retourne le cache (qui est toujours frais grâce au thread)
    data = get_cached_matches()
    return jsonify(data)

# Pour Render, on n'utilise PAS app.run().
# Render utilisera Gunicorn pour lancer l'app.
# On garde quand même le bloc pour les tests locaux.
if __name__ == '__main__':
    port = int(os.environ.get('PORT', 5000))
    app.run(host='0.0.0.0', port=port)