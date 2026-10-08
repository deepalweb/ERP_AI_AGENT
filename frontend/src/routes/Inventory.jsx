import Investigate from "./Investigate";

// Dedicated Inventory page: the same case workspace Investigate renders
// (family drill-down, filters, the tag-based case detail view, early
// signals), pinned to the inventory domain -- the first of a planned
// per-business-area page set, reusing Investigate's logic rather than
// forking a second copy of it. See Investigate.jsx's lockedDomain prop.
export default function Inventory() {
  return (
    <Investigate
      lockedDomain="inventory"
      title="Inventory"
      description="Stock, transfers, dispatch, stock counts, and damage write-offs -- every open inventory case in one place."
    />
  );
}
