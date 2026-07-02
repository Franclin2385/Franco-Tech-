from flask import Flask, jsonify
from flask_cors import CORS
from scraper import get_cached_matches

app = Flask(__name__)
CORS(app)  # Autorise les requêtes depuis le mobile

@app.route('/api/matches', methods=['GET'])
def matches():
    """Endpoint renvoyant tous les matchs football avec les cotes 1xBet."""
    data = get_cached_matches()
    return jsonify(data)

if __name__ == '__main__':
    app.run(host='0.0.0.0', port=5000, debug=True)