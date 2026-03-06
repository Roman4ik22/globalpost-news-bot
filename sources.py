"""Список источников новостей для парсинга."""

SOURCES = [
    # === Надёжные RSS-источники ===
    {
        "name": "Supply Chain Dive",
        "url": "https://www.supplychaindive.com/feeds/news/",
        "type": "rss",
    },
    {
        "name": "The Loadstar",
        "url": "https://theloadstar.com/feed/",
        "type": "rss",
    },
    {
        "name": "FreightWaves",
        "url": "https://www.freightwaves.com/feed",
        "type": "rss",
    },
    {
        "name": "Air Cargo News",
        "url": "https://www.aircargonews.net/feed/",
        "type": "rss",
    },
    {
        "name": "gCaptain",
        "url": "https://gcaptain.com/feed/",
        "type": "rss",
    },
    {
        "name": "Splash247",
        "url": "https://splash247.com/feed/",
        "type": "rss",
    },
    {
        "name": "Hellenic Shipping News",
        "url": "https://www.hellenicshippingnews.com/feed/",
        "type": "rss",
    },
    {
        "name": "Seatrade Maritime",
        "url": "https://www.seatrade-maritime.com/rss.xml",
        "type": "rss",
    },
    {
        "name": "Container News",
        "url": "https://container-news.com/feed/",
        "type": "rss",
    },
    # === Google News — всегда свежие ===
    {
        "name": "Google News Shipping",
        "url": "https://news.google.com/rss/search?q=shipping+freight+logistics+container&hl=en&gl=US&ceid=US:en",
        "type": "rss",
    },
    {
        "name": "Google News Trade",
        "url": "https://news.google.com/rss/search?q=global+trade+tariffs+customs&hl=en&gl=US&ceid=US:en",
        "type": "rss",
    },
]
