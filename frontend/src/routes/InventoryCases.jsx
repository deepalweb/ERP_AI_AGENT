import Investigate from "./Investigate";

// The full inventory case workspace (family drill-down, filters, the
// tag-based case detail view, early signals) -- same Investigate logic,
// pinned to the inventory domain. Linked from the Inventory dashboard's
// "Tracked Inventory Issues" section ("See all inventory cases") rather
// than living at /inventory itself, which is now the visual health
// dashboard (routes/Inventory.jsx).
export default function InventoryCases() {
  return (
    <Investigate
      lockedDomain="inventory"
      title="Inventory cases"
      description="Every open inventory case -- stock, transfers, dispatch, stock counts, and damage write-offs -- in one place."
    />
  );
}
