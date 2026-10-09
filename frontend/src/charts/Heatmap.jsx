/** Row x column magnitude matrix, single-hue intensity per cell -- "which
 * combination stands out," read in one glance across both dimensions at
 * once. Plain CSS grid, not Recharts (no primitive fits a matrix cleanly).
 *
 * data: [{ row, col, value }] -- sparse is fine, a missing combination
 * renders as an empty cell rather than 0 (real absence, not a real zero).
 *
 * normalize: "column" (default) scales each column to its own max, so a
 * heatmap mixing columns of very different scale -- counts next to rates,
 * dead-stock value next to a stuck-session count -- still shows real
 * variation in every column, not just whichever one happens to be biggest.
 * "global" scales every cell to the same single max instead, for a matrix
 * where every cell is already the same unit (e.g. shortage qty by reason x
 * outlet) and comparing across columns directly is the point.
 *
 * Intensity is encoded as opacity of ONE hue (`colorVar`), never a rainbow
 * or a second hue -- a heatmap's color means magnitude only.
 *
 * formatValue(value, colId) receives the column id, so a matrix that mixes
 * units across columns (a rate next to a currency total) can format each
 * column correctly instead of guessing from the number's size. */
export default function Heatmap({ rows, columns, data, formatValue = (v) => String(v), normalize = "column", colorVar = "var(--series-1)" }) {
  const byKey = new Map(data.map((d) => [`${d.row}::${d.col}`, d.value]));

  const colMax = {};
  const globalMax = Math.max(0, ...data.map((d) => d.value));
  for (const col of columns) {
    colMax[col.id] = Math.max(0, ...data.filter((d) => d.col === col.id).map((d) => d.value));
  }

  function intensityFor(colId, value) {
    const max = normalize === "global" ? globalMax : colMax[colId];
    if (!max || value == null) return 0;
    return Math.max(0, Math.min(1, value / max));
  }

  return (
    <div className="overflow-x-auto">
      <table className="w-full border-separate" style={{ borderSpacing: 4 }}>
        <thead>
          <tr>
            <th className="pb-1 text-left text-xs font-medium text-foreground/60"> </th>
            {columns.map((c) => (
              <th key={c.id} className="pb-1 text-center text-xs font-medium text-foreground/60">
                {c.label}
              </th>
            ))}
          </tr>
        </thead>
        <tbody>
          {rows.map((r) => (
            <tr key={r.id}>
              <td className="pr-2 text-xs font-medium text-foreground-strong">{r.label}</td>
              {columns.map((c) => {
                const value = byKey.get(`${r.id}::${c.id}`);
                const intensity = intensityFor(c.id, value);
                // Intensity baked directly into the background color's mix
                // ratio (not CSS opacity) -- opacity would fade the cell's
                // text along with its fill, making low-intensity values
                // unreadable instead of just paler.
                const background = value == null
                  ? "var(--surface-sunken)"
                  : `color-mix(in srgb, ${colorVar} ${Math.round(10 + intensity * 80)}%, var(--chart-surface))`;
                return (
                  <td key={c.id} className="p-0">
                    <div
                      className="flex h-11 min-w-[72px] items-center justify-center rounded-md text-xs font-semibold tabular-nums"
                      style={{
                        backgroundColor: background,
                        color: value != null && intensity > 0.55 ? "#fff" : "var(--foreground-strong)",
                      }}
                      title={value == null ? "No data" : formatValue(value, c.id)}
                    >
                      {value == null ? "--" : formatValue(value, c.id)}
                    </div>
                  </td>
                );
              })}
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}
