"""The store-scope filter every sales/operations metric in this app is narrowed
to: the outlets Celeste operates directly. Excludes SPAR co-located locations
and partner-branded fulfillment channels (Budget Kade, 21+, Red Bull, Nestle,
Fonterra, UStore) that show up in res_store but aren't Celeste's own retail
business. IDs are used for filtering (not names) so a future rename can't
silently break the filter.
"""

CELESTE_STORE_IDS = (1, 2, 45, 46, 68)

CELESTE_STORE_NAMES = [
    "Celeste Daily Colombo 6",
    "Celeste Daily Pannipitiya",
    "Celeste Daily - Attidiya",
    "Celeste Daily Ethul Kotte",
    "Celeste Daily Ja-Ela",
]
