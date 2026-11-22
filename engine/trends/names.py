"""
engine/trends/names.py

football-data.co.uk names are used for all logic and matching; this table only
changes how a team is SHOWN on the page and in trend lines.
Teams not listed are shown as they are.
"""

DISPLAY = {
    # La Liga
    "Ath Madrid": "Atlético Madrid", "Ath Bilbao": "Athletic Club", "Celta": "Celta Vigo",
    "Sociedad": "Real Sociedad", "Vallecano": "Rayo Vallecano", "Betis": "Real Betis",
    "Espanol": "Espanyol", "Alaves": "Alavés", "Sp Gijon": "Sporting Gijón", "La Coruna": "Deportivo",
    # EPL
    "Nott'm Forest": "Nottingham Forest",
    # Bundesliga
    "Ein Frankfurt": "Eintracht Frankfurt", "Leverkusen": "Bayer Leverkusen", "M'gladbach": "Gladbach",
    "FC Koln": "Köln", "St Pauli": "St. Pauli", "Hertha": "Hertha Berlin",
    # Serie A
    "Milan": "AC Milan", "Verona": "Hellas Verona",
    # Ligue 1
    "Paris SG": "Paris Saint-Germain", "St Etienne": "Saint-Étienne",
}


def display(name: str) -> str:
    return DISPLAY.get(name, name)
