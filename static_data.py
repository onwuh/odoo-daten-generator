"""Static DACH geography/telephony facts for deterministic address assembly.

Structured per-country (dict keyed by country_code, not a flat list) so a future
market beyond DACH is a data addition here, not a redesign of data_factory.py.
"""

COUNTRY_DIAL = {"DE": "49", "AT": "43", "CH": "41"}
ZIP_LEN = {"DE": 5, "AT": 4, "CH": 4}

# Real, plausibility-checked min/max postal-code ranges per city (not blind
# prefix+padding — e.g. Leipzig's range starts at 04103, not a naive "04" +
# random 3 digits, which could land on a non-existent code). Approximate to
# the real range, not certified exact, but every generated zip lands inside
# the city's actual postal band.
CITIES = {
    "DE": [
        {"city": "Berlin", "zip_min": 10115, "zip_max": 13599, "area_code": "30"},
        {"city": "Hamburg", "zip_min": 20095, "zip_max": 22769, "area_code": "40"},
        {"city": "München", "zip_min": 80331, "zip_max": 81929, "area_code": "89"},
        {"city": "Köln", "zip_min": 50667, "zip_max": 51149, "area_code": "221"},
        {"city": "Frankfurt am Main", "zip_min": 60306, "zip_max": 60599, "area_code": "69"},
        {"city": "Stuttgart", "zip_min": 70173, "zip_max": 70629, "area_code": "711"},
        {"city": "Düsseldorf", "zip_min": 40210, "zip_max": 40629, "area_code": "211"},
        {"city": "Leipzig", "zip_min": 4103, "zip_max": 4357, "area_code": "341"},
        {"city": "Dresden", "zip_min": 1067, "zip_max": 1326, "area_code": "351"},
        {"city": "Hannover", "zip_min": 30159, "zip_max": 30669, "area_code": "511"},
        {"city": "Nürnberg", "zip_min": 90402, "zip_max": 90491, "area_code": "911"},
        {"city": "Bremen", "zip_min": 28195, "zip_max": 28779, "area_code": "421"},
        {"city": "Essen", "zip_min": 45127, "zip_max": 45359, "area_code": "201"},
        {"city": "Dortmund", "zip_min": 44135, "zip_max": 44388, "area_code": "231"},
        {"city": "Bonn", "zip_min": 53111, "zip_max": 53229, "area_code": "228"},
        {"city": "Mannheim", "zip_min": 68159, "zip_max": 68309, "area_code": "621"},
        {"city": "Karlsruhe", "zip_min": 76131, "zip_max": 76229, "area_code": "721"},
        {"city": "Wiesbaden", "zip_min": 65183, "zip_max": 65207, "area_code": "611"},
        {"city": "Münster", "zip_min": 48143, "zip_max": 48167, "area_code": "251"},
        {"city": "Augsburg", "zip_min": 86150, "zip_max": 86199, "area_code": "821"},
        {"city": "Kiel", "zip_min": 24103, "zip_max": 24149, "area_code": "431"},
        {"city": "Mainz", "zip_min": 55116, "zip_max": 55131, "area_code": "6131"},
    ],
    "AT": [
        {"city": "Wien", "zip_min": 1010, "zip_max": 1230, "area_code": "1"},
        {"city": "Graz", "zip_min": 8010, "zip_max": 8055, "area_code": "316"},
        {"city": "Linz", "zip_min": 4020, "zip_max": 4060, "area_code": "732"},
        {"city": "Salzburg", "zip_min": 5020, "zip_max": 5033, "area_code": "662"},
    ],
    "CH": [
        {"city": "Zürich", "zip_min": 8001, "zip_max": 8099, "area_code": "44"},
        {"city": "Genf", "zip_min": 1200, "zip_max": 1227, "area_code": "22"},
        {"city": "Bern", "zip_min": 3000, "zip_max": 3030, "area_code": "31"},
        {"city": "Basel", "zip_min": 4000, "zip_max": 4059, "area_code": "61"},
    ],
}

STREET_NAMES = [
    "Industriestraße", "Am Technologiepark", "Bahnhofstraße", "Hauptstraße",
    "Werkstraße", "Gewerbering", "Am Alten Hafen", "Fabrikstraße",
    "Schulstraße", "Gartenweg", "Ringstraße", "Poststraße", "Marktplatz",
    "Am Stadtpark", "Lindenallee", "Birkenweg", "An der Mühle", "Talstraße",
    "Bergstraße", "Kirchweg", "Rosenstraße", "Am Sportplatz", "Feldweg",
    "Waldstraße", "Mühlenweg", "Friedhofstraße", "Neue Straße", "Am Bahnhof",
]

LOCATION_LABELS = ["Lager", "Hauptsitz", "Niederlassung", "Filiale", "Logistikzentrum"]
