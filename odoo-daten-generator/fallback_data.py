# Single source of truth for all hardcoded fallback names used when the LLM is
# unavailable or returns insufficient data.

FALLBACK_COMPANIES = [
    'ACME Consulting GmbH', 'FutureSoft AG', 'Innovativ Solutions GmbH',
    'DataWorks KG', 'NextGen Systems GmbH',
]

FALLBACK_PRODUCTS = {
    'IT': ['Cloud Service Paket', 'Supportvertrag Premium', 'SaaS Lizenz', 'Firewall Appliance', 'Backup Lösung'],
    'Fertigung': ['Schraubensatz M6', 'Hydraulikpumpe', 'Förderband Motor', 'Sensorik Kit', 'Wartungspaket'],
    'Handel': ['Kassensystem', 'Barcode Scanner', 'Regalmodul', 'Etikettendrucker', 'Verpackungseinheit'],
}

FALLBACK_EMPLOYEES = [
    'Anna Schmidt', 'Lukas Weber', 'Mia Fischer', 'Jonas Wagner', 'Lea Becker',
    'Paul Hoffmann', 'Nina Keller', 'Tim Schäfer', 'Laura Bauer', 'Felix Richter',
    'Sophie Wolf', 'Max König', 'Emma Hartmann', 'Ben Krämer', 'Lena Schuster',
]

FALLBACK_SUPPLIERS = [
    'Alpha Supplies GmbH', 'Global Parts AG', 'Logistik & Co. KG',
    'TechImport Ltd.', 'Bürobedarf Müller', 'Industriebedarf König',
]

FALLBACK_OPPORTUNITY_TITLES = [
    'ERP-Einführung Müller & Partner', 'Cloud-Migration Q3', 'Wartungsvertrag Verlängerung',
    'Digitalisierung Auftragsabwicklung', 'System-Ablösung Legacy', 'Rollout neues Lagermodul',
    'Konsolidierung IT-Infrastruktur', 'Schulungspaket Vertriebsteam', 'API-Anbindung Drittsystem',
]

FALLBACK_PROJECT_STAGES = {
    'IT': ['Kickoff', 'Analyse & Planung', 'Entwicklung', 'Testing & QA', 'Deployment', 'Abnahme'],
    'Fertigung': ['Planung', 'Beschaffung', 'Produktion', 'Qualitätskontrolle', 'Montage', 'Abnahme'],
    'Handel': ['Planung', 'Beschaffung', 'Lagerung', 'Verkauf', 'Auslieferung', 'Nachbetreuung'],
    'default': ['Planung', 'Umsetzung', 'Testing', 'Review', 'Abnahme', 'Abschluss'],
}

FALLBACK_TASK_NAMES = ['Analyse', 'Design', 'Entwicklung', 'Testing', 'Schulung', 'Dokumentation', 'Review']

FALLBACK_CV_BULLETS = [
    'Mehrjährige Berufserfahrung in vergleichbarer Position',
    'Erfolgreiche Mitarbeit in verschiedenen Projektteams',
    'Kontinuierliche Weiterbildung im relevanten Fachbereich',
    'Verantwortung für die Betreuung von Kunden und Partnern',
]
