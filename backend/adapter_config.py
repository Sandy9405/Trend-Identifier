"""
PLATFORM ADAPTER CONFIG
=======================

This is the ONLY thing you edit when adding / replacing a data source.
Drop a new JSON file into /data, add (or edit) one entry below — no logic change.

Each entry declares:

  glob            : case-insensitive filename glob matched against files in /data
                    (.json or .csv — CSVs are read via their header row).
  roles           : list of roles this platform plays. One platform can play several.
                      "india_demand"  -> its rating_count is treated as a DEMAND signal
                                         (auto-disabled if rating_count is absent/all-zero)
                      "india_supply"  -> its assortment counts as Indian SUPPLY conviction
                      "west_supply"   -> what Western fast-fashion DROPPED. Supply only,
                                         NEVER demand — labelled as such everywhere.
                      "pos_sales"     -> the BUYER'S OWN till data. The strongest proof of
                                         local demand; activates own-sales scoring the
                                         moment a matching file lands in /data.
  field_map       : canonical field -> raw key. Schema tolerance, so future sources with
                    different shapes need only a mapping, never code:
                      - plain key:        "price": "sellingPrice"
                      - dot path:         "price": "pricing.selling_price"   (nested JSON)
                      - fallback list:    "price": ["sellingPrice", "price", "asp"]
                                          (first non-empty wins — one entry absorbs
                                           several vendor schema variants)
                    Canonical fields:
                      name, brand, price, mrp, rating, rating_count, in_stock,
                      image_url, product_url, category, sub_category,
                      units_sold, returns          (the last two for pos_sales files)
  root_path       : optional dot path to the row array when the JSON is an object
                    wrapper, e.g. "data.products". Undeclared single-list wrappers
                    are also unwrapped automatically.
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

    # ── THE BUYER'S OWN SALES DATA ─────────────────────────────────────────
    # Activates automatically when any file whose name contains "sales" or
    # "pos" lands in /data (CSV export from the buyer's POS/ERP, or JSON).
    # No file present → the engine runs market-only and SAYS SO. The fallback
    # key lists below absorb the usual export-column variants, so most ERP
    # dumps work without touching this entry. Template with the expected
    # columns: data/buyer_sales_template.csv.example
    "buyer_pos": {
        "glob": "*sales*",
        "roles": ["pos_sales"],
        "field_map": {
            "name": ["style_name", "product_name", "item_name", "description", "name"],
            "brand": ["brand", "label"],
            "price": ["asp", "avg_selling_price", "selling_price", "price"],
            "mrp": ["mrp", "list_price"],
            "units_sold": ["units_sold", "qty_sold", "quantity_sold", "units", "net_units"],
            "returns": ["units_returned", "returns", "return_qty"],
            "rating": None,
            "rating_count": None,
            "in_stock": None,
            "image_url": None,
            "product_url": None,
            "category": ["category", "gender", "segment"],
            "sub_category": ["sub_category", "subcategory", "article_type"],
        },
        # POS exports usually carry no gender/sub-category columns; assume the
        # buyer's own category unless a row says otherwise:
        "default_category": "women",
        "default_sub_category": "tops",
        "discount_mode": "recompute_from_price_mrp",
        "date_proxy_regex": None,
        "currency": "INR",
    },

    # US department-store feed → west_supply, same as ASOS: what the West
    # DROPPED, never demand. Fallback key lists cover the common scraper
    # field-name variants; if your export uses different keys, edit only the
    # lists below. NOTE the glob also catches the common misspellings.
    "nordstrom": {
        "glob": "*no*str*m*",   # nordstrom / norstrom / norstrodam ...
        "roles": ["west_supply"],
        "field_map": {
            "name": ["productName", "name", "title", "product_name", "displayName"],
            "brand": ["brand", "brandName", "brand_name"],
            "price": ["currentPrice", "salePrice", "price", "current_price"],
            "mrp": ["originalPrice", "regularPrice", "listPrice", "original_price", "mrp"],
            "rating": ["rating", "reviewStarRating"],
            "rating_count": ["reviewCount", "ratingCount", "review_count"],
            "in_stock": None,
            "image_url": ["imageUrl", "image", "mainImage", "image_url"],
            "product_url": ["productUrl", "url", "link", "product_url"],
            "category": ["gender", "division", "category"],
            "sub_category": ["productType", "subCategory", "sub_category"],
        },
        "discount_mode": "recompute_from_price_mrp",
        "date_proxy_regex": None,
        "currency": "USD",
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
