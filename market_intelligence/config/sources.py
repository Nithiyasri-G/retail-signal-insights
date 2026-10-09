
SIGNAL_SEARCH_TIME_WINDOWS = {
    "Last 1 hour": "now 1-H",
    "Last 4 hours": "now 4-H",
    "Last 1 day": "now 1-d",
    "Last 7 days": "now 7-d",
}


US_SIGNAL_GEOS = {
    "United States - National": "US",
    "Alabama": "US-AL", "Alaska": "US-AK", "Arizona": "US-AZ", "Arkansas": "US-AR",
    "California": "US-CA", "Colorado": "US-CO", "Connecticut": "US-CT", "Delaware": "US-DE",
    "Florida": "US-FL", "Georgia": "US-GA", "Hawaii": "US-HI", "Idaho": "US-ID",
    "Illinois": "US-IL", "Indiana": "US-IN", "Iowa": "US-IA", "Kansas": "US-KS",
    "Kentucky": "US-KY", "Louisiana": "US-LA", "Maine": "US-ME", "Maryland": "US-MD",
    "Massachusetts": "US-MA", "Michigan": "US-MI", "Minnesota": "US-MN", "Mississippi": "US-MS",
    "Missouri": "US-MO", "Montana": "US-MT", "Nebraska": "US-NE", "Nevada": "US-NV",
    "New Hampshire": "US-NH", "New Jersey": "US-NJ", "New Mexico": "US-NM", "New York": "US-NY",
    "North Carolina": "US-NC", "North Dakota": "US-ND", "Ohio": "US-OH", "Oklahoma": "US-OK",
    "Oregon": "US-OR", "Pennsylvania": "US-PA", "Rhode Island": "US-RI", "South Carolina": "US-SC",
    "South Dakota": "US-SD", "Tennessee": "US-TN", "Texas": "US-TX", "Utah": "US-UT",
    "Vermont": "US-VT", "Virginia": "US-VA", "Washington": "US-WA", "West Virginia": "US-WV",
    "Wisconsin": "US-WI", "Wyoming": "US-WY", "District of Columbia": "US-DC",
}


SOURCE_ORDER = ["gnews", "bls", "fda", "weather", "apify"]


SOURCE_LABELS = {
    "gnews": "GNews / Google News RSS",
    "bls": "BLS CPI",
    "fda": "openFDA Food Enforcement",
    "weather": "NOAA Weather Alerts",
    "apify": "Apify Google Trends",
}


SOURCE_ENDPOINTS = {
    "gnews": "GNews package or Google News RSS search feed",
    "bls": "https://api.bls.gov/publicAPI/v2/timeseries/data/",
    "fda": "https://api.fda.gov/food/enforcement.json",
    "weather": "https://api.weather.gov/alerts/active",
    "apify": "apify/google-trends-scraper",
}


ANALYSIS_METHODS = {
    "gnews": "Classifies article titles/descriptions into event types, sentiment, source confidence, then averages article risk into one news feature.",
    "bls": "Batches CPI series, calculates month-over-month and year-over-year movement, then scores inflation pressure.",
    "fda": "Classifies recall reason, FDA class, status, UPC presence, and state coverage, then uses highest adjusted recall severity.",
    "weather": "Collects active NOAA alerts for the selected state, weights severity, urgency, and certainty, then caps supply-chain weather risk at 10.",
    "apify": "Uses top regional Google Trends index divided by 10; backend hard-limits time range and keyword count to protect quota.",
}
