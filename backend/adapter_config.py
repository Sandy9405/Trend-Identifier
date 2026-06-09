"""
PLATFORM ADAPTER CONFIG
=======================

This is the ONLY thing you edit when adding / replacing a data source.
Drop a new JSON file into /data, add (or edit) one entry below — no logic change.

Each entry declares:

  glob            : case-insensitive filename glob matched against files in /data.
  roles           : list of roles this platform plays. One platform can play several.
                      "india_demand"  -> its rating_count is treated as a DEMAND signal
                                         (auto-disabled if rating_count is absent/all-zero)
                      "india_supply"  -> its assortment counts as Indian SUPPLY conviction
                      "west_supply"   -> what Western fast-fashion DROPPED. Supply only,
                                         NEVER demand — labelled as such everywhere.
  field_map       : canonical field -> raw JSON key, or a LIST of keys tried in order
                    (first non-empty wins). Canonical fields:
                      name, brand, price, mrp, rating, rating_count, in_stock,
                      image_url, product_url
  discount_mode   : "recompute_from_price_mrp" — the only supported mode, on purpose.
                    Scraper-provided discount fields are never trusted (Myntra's
                    `discountPercent` is literally the rupee saving, e.g. 599 "percent").
  date_proxy_regex: optional regex run against image_url to approximate the upload date
                    (a freshness proxy, labelled as a proxy). Must use named groups
                    (?P<y>) (?P<m>) (?P<d>); month may be a number or an English name.
                    No regex / no match -> freshness "unknown" for that product.
  sub_category_filter : optional {"field": raw_key, "keep": [values]} — hard-drops rows
                    at ingest. Usually NOT needed: category/sub-category scoping is
                    handled by the segment dropdowns instead, so mixed scrapes
                    (sarees + jeans + tops) stay browsable rather than discarded.
  category / sub_category (inside field_map): raw keys carrying the segment, if the
                    platform provides them (e.g. AJIO's `segment` / `subCategory`).
                    Missing or unmapped → generic keyword inference from the raw
                    value, product URL, then name (rule tables in ingest.py).
  default_category / default_sub_category : optional fallback describing how the
                    SCRAPE was taken (e.g. ASOS feed is the women's new-in page even
                    though product names don't say "women"). Inference still wins
                    when a row carries its own signal.
  currency        : informational; cross-currency prices are never mixed in one metric.
                    price_band_fit only uses INR platforms.
"""

PLATFORM_ADAPTERS = {
    "myntra": {
        "glob": "*myntra*.json",
        "roles": ["india_demand", "india_supply"],
        "field_map": {
            "name": "name",
            "brand": "brand",
            "price": "price",
            "mrp": "mrp",
            "rating": "rating",
            "rating_count": "ratingCount",
            "in_stock": "inStock",
            "image_url": "imageUrl",
            "product_url": "productUrl",
            # no explicit segment fields — inferred from productUrl/name,
            # with the scrape-level default below as fallback
        },
        "default_category": "women",   # scrape was the women's tops search
        "discount_mode": "recompute_from_price_mrp",
        # http://assets.myntassets.com/assets/images/2025/NOVEMBER/22/xxxx.jpg
        "date_proxy_regex": r"/images/(?P<y>\d{4})/(?P<m>[A-Za-z]+)/(?P<d>\d{1,2})/",
        "currency": "INR",
    },

    "ajio": {
        "glob": "*ajio*.json",
        # AJIO's reviewCount is mapped as rating_count, but in the current scrape it is
        # all-zero — ingest auto-detects that and AJIO contributes NO demand signal.
        # We still declare india_demand so that a future scrape WITH review counts
        # starts contributing demand automatically, with zero config change.
        "roles": ["india_demand", "india_supply"],
        "field_map": {
            "name": "name",
            "brand": "brandName",
            "price": "price",        # listed price; offerPrice is a coupon price, not shelf
            "mrp": "mrp",
            "rating": "rating",
            "rating_count": "reviewCount",
            "in_stock": "inStock",
            "image_url": "mainImage",
            "product_url": "productUrl",
            "category": "segment",          # "Women" / "Men" / ...
            "sub_category": "subCategory",  # "Tops", "Sarees", "Jeans & Jeggings", ...
        },
        # NOTE: no hard sub_category_filter — AJIO's mixed scrape (sarees, jeans,
        # kurtas...) now feeds the segment dropdowns instead of being discarded.
        "discount_mode": "recompute_from_price_mrp",
        # https://assets.ajio.com/medias/sys_master/root1/20250918/...
        "date_proxy_regex": r"/(?P<y>20\d{2})(?P<m>\d{2})(?P<d>\d{2})/",
        "currency": "INR",
    },

    "asos": {
        "glob": "*asos*.json",
        "roles": ["west_supply"],
        "field_map": {
            "name": "productName",
            "brand": None,                       # not provided by this scraper
            "price": ["currentPrice", "originalPrice"],  # currentPrice is empty in this scrape
            "mrp": "originalPrice",
            "rating": None,
            "rating_count": None,
            "in_stock": None,
            "image_url": "imageUrl",
            "product_url": "productUrl",
        },
        "default_category": "women",   # feed scraped is the women's new-in page
        "discount_mode": "recompute_from_price_mrp",
        # ASOS image URLs carry no upload date -> freshness "unknown" (disclosed in /api/meta)
        "date_proxy_regex": None,
        "currency": "GBP",
    },
}
