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

from playwright.async_api import async_playwright, Response
from playwright_stealth import Stealth

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
)
logger = logging.getLogger("francobot_scraper")

TARGET_URL = "https://1xbet.com/fr/live"  # à remplacer par ton URL exacte
MARKET_IDS = {136, 731}  # MARKET_1X2, MARKET_EXACT_SCORE

# Fenêtre de jitter pour toute action périodique (en secondes)
JITTER_MIN, JITTER_MAX = 1.5, 3.5

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


async def handle_odds_payload(payload: dict):
    """
    Point d'intégration avec ta logique existante :
    - parsing des champs Value[], I, O1/O2
    - calcul du score label (total_goals + away_goals * 0.001)
    - upsert via HiveService (recordConsultation / recordPrediction / recordResult)
    """
    state.last_valid_payload_at = datetime.now(timezone.utc)
    state.consecutive_errors = 0
    # TODO: brancher ici ton parsing réel + écriture Hive / Firestore
    logger.debug("Payload cotes reçu (%d octets)", len(json.dumps(payload)))


async def on_response(response: Response):
    """Intercepte chaque réponse réseau de la page et filtre les endpoints de cotes."""
    url = response.url

    # Adapte ce filtre à l'endpoint réel identifié dans tes DevTools
    if "LiveFeed" not in url and "GetGameZip" not in url:
        return

    if looks_like_block(response):
        state.consecutive_errors += 1
        logger.warning("Réponse suspecte (%s) sur %s — possible blocage", response.status, url)
        return

    try:
        payload = await response.json()
    except Exception:
        return  # pas du JSON exploitable, on ignore silencieusement

    await handle_odds_payload(payload)


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
