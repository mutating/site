export const GiB = 1024 ** 3;
export function number(value: number, locale: string) {
  return new Intl.NumberFormat(locale, { maximumFractionDigits: 2 }).format(
    value,
  );
}
export function stepPath<Field extends string>(
  points: readonly ({ at: string } & Record<Field, number>)[],
  field: Field,
  start: number,
  end: number,
  max: number,
  width = 800,
  height = 240,
) {
  if (!points.length || end <= start || max <= 0) return "";
  const x = (p: { at: string }) =>
    (((new Date(p.at).getTime() - start) / (end - start)) * width).toFixed(2);
  const y = (p: Record<Field, number>) =>
    (height - (Math.max(0, p[field]) / max) * height).toFixed(2);
  return points
    .map((p, i) => (i === 0 ? `M${x(p)},${y(p)}` : `H${x(p)}V${y(p)}`))
    .join("");
}
