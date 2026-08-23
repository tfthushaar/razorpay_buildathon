export const rupees = (paise: number): string =>
  `₹${(paise / 100).toLocaleString("en-IN", { minimumFractionDigits: 2, maximumFractionDigits: 2 })}`;

export const pct = (fraction: number, digits = 1): string => `${(fraction * 100).toFixed(digits)}%`;

export const categoryLabel = (category: string): string =>
  category
    .split("_")
    .map((w) => w[0].toUpperCase() + w.slice(1))
    .join(" ");
