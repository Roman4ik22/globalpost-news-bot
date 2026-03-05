"""Список источников новостей для парсинга."""

SOURCES = [
    # I. Глобальная аналитика и новости рынка (RSS — надёжные)
    {
        "name": "Supply Chain Dive",
        "url": "https://www.supplychaindive.com/feeds/news/",
        "type": "rss",
        "category": "analytics",
    },
    {
        "name": "The Loadstar",
        "url": "https://theloadstar.com/feed/",
        "type": "rss",
        "category": "analytics",
    },
    {
        "name": "FreightWaves",
        "url": "https://www.freightwaves.com/feed",
        "type": "rss",
        "category": "analytics",
    },
    {
        "name": "Air Cargo News",
        "url": "https://www.aircargonews.net/feed/",
        "type": "rss",
        "category": "analytics",
    },
    {
        "name": "gCaptain",
        "url": "https://gcaptain.com/feed/",
        "type": "rss",
        "category": "analytics",
    },
    {
        "name": "Splash247",
        "url": "https://splash247.com/feed/",
        "type": "rss",
        "category": "analytics",
    },
    {
        "name": "Port Technology",
        "url": "https://www.porttechnology.org/feed/",
        "type": "rss",
        "category": "infrastructure",
    },
    {
        "name": "Journal of Commerce",
        "url": "https://www.joc.com/rss/news",
        "type": "rss",
        "category": "analytics",
    },
    {
        "name": "Hellenic Shipping News",
        "url": "https://www.hellenicshippingnews.com/feed/",
        "type": "rss",
        "category": "analytics",
    },

    # II. Мито та регуляції
    {
        "name": "EU DG TAXUD",
        "url": "https://taxation-customs.ec.europa.eu/news_en",
        "type": "web",
        "category": "customs",
    },

    # III. Інфраструктура
    {
        "name": "Maritime Executive",
        "url": "https://maritime-executive.com/feed",
        "type": "rss",
        "category": "infrastructure",
    },
    {
        "name": "Rail Baltica News",
        "url": "https://www.railbaltica.org/news/",
        "type": "web",
        "category": "infrastructure",
    },

    # IV. Оператори
    {
        "name": "UPS Pressroom",
        "url": "https://about.ups.com/us/en/newsroom.html",
        "type": "web",
        "category": "operators",
    },
]
