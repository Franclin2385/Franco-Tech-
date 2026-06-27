                
from flask import Flask, jsonify
from scraper import get_cached_data, fetch_and_>

app = Flask(__name__)

@app.route('/api/matches', methods=['GET'])
def get_matches():
    # Force la mise à jour à chaque appel de l'>
    fetch_and_parse()
    return jsonify(get_cached_data())

if __name__ == '__main__':
    # Écoute sur l'adresse 0.0.0.0 pour permett>
    app.run(host='0.0.0.0', port=5000, debug=Fa>

