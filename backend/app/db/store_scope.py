"""The store-scope filter every sales/operations metric in this app is narrowed
to: the outlets Celeste operates directly. Excludes SPAR co-located locations
and partner-branded fulfillment channels (Budget Kade, 21+, Red Bull, Nestle,
Fonterra, UStore) that show up in res_store but aren't Celeste's own retail
business. IDs are used for filtering (not names) so a future rename can't
silently break the filter.

Celeste Daily Ja-Ela (store_id 68) was previously included here but is
excluded as of 2026-09-14: checked live and it has zero rows in
sale_order, uber_order, AND pickme_order across their entire history --
not a real trading outlet, so every metric in this app was silently
scoping in a dead store. The 4 remaining outlets all have substantial,
real order history in every channel.
"""

CELESTE_STORE_IDS = (1, 2, 45, 46)

CELESTE_STORE_NAMES = [
    "Celeste Daily Colombo 6",
    "Celeste Daily Pannipitiya",
    "Celeste Daily - Attidiya",
    "Celeste Daily Ethul Kotte",
]

# sale_order.state values that represent a completed/confirmed sale, for
# every revenue/order/velocity query in the app. 'done' is included for
# forward-compatibility (locked orders after full delivery+invoicing) even
# though this database currently has zero such rows. 'received' was added
# 2026-09-18: found via direct DB audit to hold 1,943 real orders (~Rs
# 5.08M) spanning the whole dataset that every query silently excluded;
# confirmed with the user to be a genuinely completed sale. 'cancel',
# 'draft', and 'sent' are deliberately excluded -- never real revenue.
SALE_ORDER_LIVE_STATES = ("sale", "done", "received")
