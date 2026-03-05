"""Список источников новостей для парсинга."""

# Каждый источник: (название, URL RSS-фида или страницы, тип: "rss" или "web", категория)
SOURCES = [
    # I. Глобальная аналитика и новости рынка
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
        "name": "Logistics Management",
        "url": "https://www.logisticsmgmt.com/rss",
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
        "name": "Reuters Business",
        "url": "https://www.reuters.com/arc/outboundfeeds/v3/all/rss.xml",
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

    # III. Продовольча логістика, Фарма та Зерно
    {
        "name": "World Grain",
        "url": "https://www.world-grain.com/rss",
        "type": "rss",
        "category": "agro",
    },
    {
        "name": "GCCA Cold Chain",
        "url": "https://www.gcca.org/news",
        "type": "web",
        "category": "cold_chain",
    },

    # IV. Інфраструктура та великі проекти
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

    # V. Офіційні медіа-центри операторів
    {
        "name": "FedEx Newsroom",
        "url": "https://newsroom.fedex.com/newsroom/feeds/",
        "type": "rss",
        "category": "operators",
    },
    {
        "name": "DHL Press Center",
        "url": "https://www.dhl.com/global-en/home/press/press-archive.html",
        "type": "web",
        "category": "operators",
    },
    {
        "name": "UPS Pressroom",
        "url": "https://about.ups.com/us/en/newsroom.html",
        "type": "web",
        "category": "operators",
    },

    # Дополнительные
    {
        "name": "LogisticsTI",
        "url": "https://logisticsti.com/",
        "type": "web",
        "category": "analytics",
    },
    {
        "name": "Supply Chain Digital",
        "url": "https://supplychaindigital.com/feed",
        "type": "rss",
        "category": "analytics",
    },
]
