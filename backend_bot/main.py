"""
Exemple d'intégration playwright-stealth pour FrancoBot backend_bot/
--------------------------------------------------------------------
Objectif : maintenir une session Playwright longue durée sur 1xBet,
intercepter les cotes en temps réel via le réseau (pas de reload de page),
avec anti-fingerprinting et jitter pour éviter le blocage de l'IP du VPS.

Installation :
    pip install playwright-stealth --break-system-packages
    playwright install chromium

À adapter :
    - TARGET_URL : URL du live 1xBet que tu scrapes
    - MARKET_IDS : tes constantes déjà identifiées (136, 731, etc.)
    - handle_odds_payload() : ta logique de parsing existante (HiveService, etc.)
"""

import asyncio
import json
import logging
import random
from datetime import datetime, timezone

from playwright.async_api import async_playwright, Response, WebSocket
from playwright_stealth import Stealth

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
)
logger = logging.getLogger("francobot_scraper")

TARGET_URL = "https://1xbet.td/fr/line"  # domaine confirmé : 1xbet.td

# Endpoints réels identifiés via DevTools (Fetch/XHR) — utilisés comme
# filtre RAPIDE (évite de parser du JSON inutile), mais pas comme filtre
# UNIQUE : voir looks_like_odds_payload() plus bas, qui détecte le format
# par le CONTENU plutôt que par le nom exact de la route. Ça évite de
# dépendre de connaître chaque nom d'endpoint à l'avance (ex: le endpoint
# du score exact, jamais confirmé par nom, mais dont le contenu est connu).
ODDS_ENDPOINTS = (
    "Get1x2_VZip",
    "GetSportsShortZip",
    "topChamps",
    "GetTopGamesStatZip",
    "expressDay",
    "GetGameZip",  # CONFIRMÉ (18/08/2026) — endpoint du détail match / score exact
)
# GetGameZip: https://1xbet.td/service-api/LineFeed/GetGameZip
#   ?id={matchId}&isSubGames=true&GroupEvents=true&country=202
#   &mode=4&topGroups=&marketType=1&lng=fr
# Utile pour un appel CIBLÉ sur un matchId précis (score exact à la demande).
# Préfixe large commun à toutes les routes de cotes observées sur 1xbet.td
ODDS_API_PREFIX = "/service-api/"

# --- Football (confirmé via payload réel du 17/08/2026) ---
FOOTBALL_SPORT_ID = 1        # SI: 1, SN: "Football" au niveau match
GROUP_1X2 = 1                 # G: 1 dans E[]/AE[] = groupe 1X2
BET_HOME, BET_AWAY, BET_DRAW = 1, 2, 3  # valeurs de T dans le groupe G:1

# ATTENTION — non confirmé sur ce feed :
# Les constantes historiques MARKET_1X2=136 / MARKET_EXACT_SCORE=731
# n'apparaissent PAS dans les payloads Get1x2_VZip observés (T va de 1 à 14,
# puis 180/181/3827-3830 pour des marchés spéciaux). Le score exact a
# probablement trop d'issues pour être inclus dans ce feed "résumé" et
# vient sans doute d'un autre endpoint (GetGameZip, identifié dans une
# session précédente). À reconfirmer avant de filtrer dessus.
# --- Score exact (confirmé via payload réel du 17/08/2026, match Fenerbahçe-Lyon) ---
EXACT_SCORE_GROUP = 136   # G: 136
EXACT_SCORE_BET_TYPE = 731  # T: 731 à l'intérieur du groupe 136
EXACT_SCORE_OTHER_TYPE = 3786  # "score autre / outsider" (hors grille standard)

MARKET_IDS = {GROUP_1X2, EXACT_SCORE_GROUP}

# Le serveur envoie Cache-Control: public, max-age=5 sur Get1x2_VZip.
# => la donnée ne se renouvelle pas plus vite que ça côté serveur.
# Poller plus vite que ce seuil n'apporte rien et est un signal suspect.
SERVER_CACHE_MAX_AGE = 5

# Fenêtre de jitter pour toute action périodique (en secondes)
# Bornes calées au-dessus du cache serveur (5s) pour rester cohérent et discret.
JITTER_MIN, JITTER_MAX = 5.5, 8.0

# Seuil d'alerte : si aucune donnée valide reçue pendant N secondes -> alerte
STALE_THRESHOLD_SECONDS = 60


class ScraperState:
    """Suivi de l'état pour le watchdog / monitoring."""
    def __init__(self):
        self.last_valid_payload_at: datetime | None = None
        self.consecutive_errors = 0


state = ScraperState()


async def jitter_sleep(min_s: float = JITTER_MIN, max_s: float = JITTER_MAX):
    """Pause aléatoire pour casser tout pattern régulier détectable."""
    await asyncio.sleep(random.uniform(min_s, max_s))


def looks_like_block(response: Response) -> bool:
    """Détecte un blocage probable (captcha, 403, 429) dans la réponse."""
    if response.status in (403, 429):
        return True
    ct = response.headers.get("content-type", "")
    if "text/html" in ct and "json" not in ct:
        # Un endpoint censé renvoyer du JSON qui renvoie du HTML = probable captcha
        return True
    return False


async def handle_odds_payload(payload: dict, source: str = "unknown"):
    """
    Gère les DEUX formats de payload observés sur 1xbet.td (17/08/2026) :

    Format A — Get1x2_VZip (liste de matchs, cotes "résumé") :
        { "Value": [ {"I":..., "E":[...], "AE":[{"G":..,"ME":[...]}]}, ... ] }

    Format B — endpoint détail d'un match (probablement GetGameZip ou
    équivalent — nom exact à reconfirmer dans DevTools) :
        { "Value": {"I":..., "GE":[{"G":.., "E":[[...],[...]]}], "SG":[...]} }
        C'est CE format qui contient le score exact (G:136, T:731).

    Champs match (communs) :
        I -> matchId | O1/O2 -> équipes | S -> timestamp coup d'envoi
        LE -> ligue | SI/SN -> id/nom sport | T -> type sport (100 = Football)

    Champs cote (communs, aplatis par _flatten_odd) :
        T -> bet type | P -> paramètre | C -> cote décimale | G -> group id
    """
    raw_value = payload.get("Value")
    if not raw_value:
        return

    state.last_valid_payload_at = datetime.now(timezone.utc)
    state.consecutive_errors = 0

    matches = raw_value if isinstance(raw_value, list) else [raw_value]

    for match in matches:
        match_id = match.get("I")
        if match_id is None:
            continue

        # Ne garder que le football (SI == FOOTBALL_SPORT_ID) — le feed
        # mélange tous les sports actifs dans un même Value[].
        if match.get("SI") != FOOTBALL_SPORT_ID:
            continue

        match_info = {
            "match_id": match_id,
            "home": match.get("O1"),
            "away": match.get("O2"),
            "league": match.get("LE"),
            "sport_id": match.get("SI"),
            "sport_type": match.get("T"),
            "kickoff_ts": match.get("S"),
        }

        odds = []

        # Format A : cotes principales + étendues
        for entry in match.get("E", []):
            odds.append(_flatten_odd(entry, group=entry.get("G")))
        for group_block in match.get("AE", []):
            group_id = group_block.get("G")
            for entry in group_block.get("ME", []):
                odds.append(_flatten_odd(entry, group=group_id))

        # Format B : groupes étendus GE[], chaque groupe contient un
        # tableau de tableaux (E[][]) — on aplatit tout.
        for group_block in match.get("GE", []):
            group_id = group_block.get("G")
            for sub_array in group_block.get("E", []):
                for entry in sub_array:
                    odds.append(_flatten_odd(entry, group=group_id))

        relevant_odds = [o for o in odds if o["group"] in MARKET_IDS] or odds

        exact_score_odds = [o for o in relevant_odds if "exact_score" in o]

        logger.debug(
            "Match %s (%s vs %s) — %d cotes (%d score exact) via %s",
            match_id, match_info["home"], match_info["away"],
            len(odds), len(exact_score_odds), source,
        )

        # TODO: brancher ici tes trois méthodes HiveService existantes :
        #   HiveService.recordConsultation(match_id, match_info)
        #   HiveService.recordPrediction(match_id, relevant_odds)
        #   HiveService.recordResult(match_id, ...)  # via results_scraper.py


def decode_exact_score(param: float | None) -> tuple[int, int] | None:
    """
    Décode le paramètre P d'une cote de score exact (G:136, T:731) en
    (buts_domicile, buts_exterieur).

    Formule confirmée sur données réelles :
        P = buts_domicile + buts_exterieur * 0.001
    Ex : P=3.002 -> (3, 2)   |   P=1 -> (1, 0)   |   P=None -> (0, 0)
    """
    if param is None:
        return (0, 0)
    home = int(param)
    # Arrondi pour éviter les erreurs de précision flottante (ex: 2.999999)
    away = round((param - home) * 1000)
    return (home, away)


def _flatten_odd(entry: dict, group: int | None) -> dict:
    """Normalise une entrée de cote (E[]/AE[].ME[] ou GE[].E[][]) en dict plat."""
    flat = {
        "bet_type": entry.get("T"),
        "param": entry.get("P"),
        "coef": entry.get("C"),
        "coef_str": entry.get("CV"),
        "group": group if group is not None else entry.get("G"),
        "featured": bool(entry.get("CE")),
    }
    if flat["group"] == EXACT_SCORE_GROUP and flat["bet_type"] == EXACT_SCORE_BET_TYPE:
        flat["exact_score"] = decode_exact_score(flat["param"])
    return flat


async def on_response(response: Response):
    """
    Détection PAR CONTENU plutôt que par nom d'endpoint — plus robuste,
    puisque 1xbet.td peut avoir plusieurs endpoints (Get1x2_VZip et
    d'autres non-identifiés) qui renvoient des formats compatibles, et
    que les noms d'URL exacts n'ont pas pu être confirmés à 100%.

    On se limite au JSON de taille "raisonnable" venant du même domaine,
    pour éviter de parser inutilement des logos, du CSS, des trackers.
    """
    url = response.url
    if "1xbet.td" not in url:
        return

    content_type = response.headers.get("content-type", "")
    if "json" not in content_type:
        return

    if looks_like_block(response):
        state.consecutive_errors += 1
        logger.warning("Réponse suspecte (%s) sur %s — possible blocage", response.status, url)
        return

    try:
        payload = await response.json()
    except Exception:
        return  # pas du JSON exploitable, on ignore silencieusement

    # Sniff de forme : on ne traite que les payloads qui ressemblent à
    # une réponse de cotes (présence de "Value" avec la bonne structure).
    value = payload.get("Value") if isinstance(payload, dict) else None
    if value is None:
        return

    looks_like_odds = (
        isinstance(value, dict) and ("GE" in value or "E" in value)
    ) or (
        isinstance(value, list) and value and isinstance(value[0], dict) and "E" in value[0]
    )
    if not looks_like_odds:
        return

    await handle_odds_payload(payload, source="xhr")


def on_websocket(ws: WebSocket):
    """
    NOTE : investigation faite le 17/08/2026 — le seul WebSocket observé sur
    1xbet.td ('solid.ws', initié par tag.js) n'envoie que des heartbeats
    {"resource":"ping",...} toutes les ~5s. Pas de flux de cotes dessus.
    1xbet.td n'expose donc pas de WS public pour les cotes live à ce stade.
    Le canal WS est laissé ici en veille (log uniquement) au cas où d'autres
    connexions apparaîtraient sur d'autres pages/marchés à l'avenir.
    """
    if "solid.ws" in ws.url:
        return  # heartbeat connu, on ignore explicitement

    logger.info("WebSocket non identifié détecté : %s — à investiguer", ws.url)


async def watchdog():
    """Vérifie périodiquement que le flux de données est toujours vivant."""
    while True:
        await asyncio.sleep(15)
        if state.last_valid_payload_at is None:
            continue
        elapsed = (datetime.now(timezone.utc) - state.last_valid_payload_at).total_seconds()
        if elapsed > STALE_THRESHOLD_SECONDS:
            logger.error(
                "Aucune donnée valide depuis %.0fs — vérifier l'IP / session / captcha",
                elapsed,
            )
            # TODO: brancher ici ton webhook Telegram/Discord d'alerte


async def fetch_exact_score_for_match(page, match_id: int, country: str = "202") -> dict | None:
    """
    Appel CIBLÉ à GetGameZip pour un matchId donné, quand tu veux le score
    exact "à la demande" plutôt que d'attendre qu'il passe naturellement
    dans le flux intercepté. Réutilise la session/cookies déjà ouverts sur
    `page`, donc pas de nouvelle connexion suspecte.

    Usage : await fetch_exact_score_for_match(page, 730328427)
    """
    url = (
        f"https://1xbet.td/service-api/LineFeed/GetGameZip"
        f"?id={match_id}&isSubGames=true&GroupEvents=true"
        f"&country={country}&mode=4&topGroups=&marketType=1&lng=fr"
    )
    try:
        response = await page.request.get(url)
    except Exception:
        logger.warning("Échec de l'appel ciblé GetGameZip pour matchId=%s", match_id)
        return None

    if looks_like_block(response):
        state.consecutive_errors += 1
        logger.warning("Blocage probable sur appel ciblé GetGameZip matchId=%s", match_id)
        return None

    try:
        payload = await response.json()
    except Exception:
        return None

    await handle_odds_payload(payload, source="targeted:GetGameZip")
    return payload


async def run_scraper():
    stealth = Stealth(
        navigator_languages_override=("fr-FR", "fr"),
        navigator_platform_override="Win32",
        # navigator_user_agent_override="Mozilla/5.0 ... " # optionnel, garde cohérent avec le reste
    )

    async with stealth.use_async(async_playwright()) as p:
        browser = await p.chromium.launch(
            headless=True,
            args=[
                "--disable-blink-features=AutomationControlled",
            ],
        )
        context = await browser.new_context(
            locale="fr-FR",
            timezone_id="Africa/Ndjamena",
            viewport={"width": 1366, "height": 768},
        )
        page = await context.new_page()
        page.on("response", lambda r: asyncio.create_task(on_response(r)))
        page.on("websocket", on_websocket)

        logger.info("Ouverture de la session sur %s", TARGET_URL)
        await page.goto(TARGET_URL, wait_until="networkidle")

        # Session longue durée : on ne recharge JAMAIS la page.
        # On laisse le live tourner et on intercepte le flux réseau en continu.
        watchdog_task = asyncio.create_task(watchdog())

        try:
            while True:
                # Activité légère occasionnelle pour paraître "vivant"
                if random.random() < 0.1:
                    await page.mouse.move(
                        random.randint(0, 1300), random.randint(0, 700)
                    )
                await jitter_sleep(10, 20)
        except asyncio.CancelledError:
            pass
        finally:
            watchdog_task.cancel()
            await browser.close()


async def main_with_restart():
    """Boucle de restart automatique en cas de crash (complément à systemd)."""
    backoff = 5
    while True:
        try:
            await run_scraper()
        except Exception:
            logger.exception("Le scraper a crashé, redémarrage dans %ds", backoff)
            await asyncio.sleep(backoff)
            backoff = min(backoff * 2, 120)  # backoff exponentiel, plafonné à 2min
        else:
            backoff = 5


if __name__ == "__main__":
    asyncio.run(main_with_restart())
