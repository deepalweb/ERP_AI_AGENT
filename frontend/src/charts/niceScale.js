function niceNum(range, round) {
  const exponent = Math.floor(Math.log10(range || 1));
  const fraction = range / 10 ** exponent;
  let niceFraction;
  if (round) {
    if (fraction < 1.5) niceFraction = 1;
    else if (fraction < 3) niceFraction = 2;
    else if (fraction < 7) niceFraction = 5;
    else niceFraction = 10;
  } else if (fraction <= 1) niceFraction = 1;
  else if (fraction <= 2) niceFraction = 2;
  else if (fraction <= 5) niceFraction = 5;
  else niceFraction = 10;
  return niceFraction * 10 ** exponent;
}

/** Return { max, ticks } for a 0-based axis covering `maxValue` with ~tickCount steps. */
export function niceScale(maxValue, tickCount = 4) {
  const safeMax = maxValue > 0 ? maxValue : 1;
  const step = niceNum(safeMax / (tickCount - 1), true);
  const niceMax = Math.ceil(safeMax / step) * step;
  const ticks = [];
  for (let v = 0; v <= niceMax + step / 2; v += step) {
    ticks.push(Math.round(v * 100) / 100);
  }
  return { max: niceMax, ticks };
}
