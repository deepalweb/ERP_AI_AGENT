# Odoo data fixes needed for transfer attribution

Written for: whoever administers Odoo for Celeste Daily. These three issues limit how
precisely the inventory investigation module (erp-ai-agent) can attribute transfer
shortages to a person or a process step. None of them can be fixed from this codebase —
each needs a change inside Odoo itself. Numbers below are live, checked 2026-10-08.

## 1. Shortage records are logged under a shared login

`stock_move_shortage.create_uid` resolves to the login `mobile` for **4,544 of the
shortage records** at the four Celeste outlets (all time), against 65 for the next most
common named login. This means the module cannot say who recorded a given shortage for
the large majority of lines — every such line shows "shared account" instead of a name.

**Fix:** assign named Odoo logins for shortage entry (scanner/handheld logins, not a
shared device account), so `create_uid` resolves to a real person going forward. This
doesn't need to be retroactive — new shortages recorded correctly going forward are
enough to start closing the gap.

## 2. 34 receipt lines have no linked outbound transfer note

In the current 14-day window, 34 receipt lines (62 units received) have no outbound
transfer note linked via `stock_picking.origin`. For these lines the module cannot show
who dispatched the goods, when, or what the transfer note planned.

**Fix:** for these specific lines, confirm whether an outbound transfer was created and
simply never had its `origin` field set on the matching receipt, or whether no outbound
transfer exists in Odoo for them at all. If the former, relink by setting `origin` on the
receipt to the matching outbound picking's name. If the latter, that's a process gap
(stock arriving at an outlet with no recorded dispatch) worth investigating on its own.

## 3. Shortage lines on cancelled or not-yet-received receipts

93 shortage lines in the current 14-day window sit on receipts in state `cancel` (761
units), and 1 line on a receipt still in state `assigned` (500 units) — not yet received.
The module already excludes these from every headline figure and reports them separately,
so this is not blocking anything today, but it's worth asking: should a shortage record
exist on a transfer that was cancelled before completion? If that reflects real goods that
went missing before the transfer was cancelled, the current exclusion may be hiding real
loss from the headline numbers.

**Fix:** no code change needed. This is a question for whoever manages the cancellation
workflow: confirm whether a cancelled receipt's shortage lines represent real loss, and if
so, how they should be reconciled or re-attributed.

---

None of the three above can be resolved from the application; each needs someone with
Odoo access to make the change or confirm the intended process. Once any of them is
addressed, the module will reflect the improvement automatically on its next scan — no
corresponding code change is needed here.
