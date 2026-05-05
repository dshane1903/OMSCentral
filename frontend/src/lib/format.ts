export function formatNumber(value: number | null, digits = 1): string {
  if (value === null || Number.isNaN(value)) {
    return "—";
  }
  return value.toFixed(digits);
}

export function formatDate(value: string | null): string {
  if (!value) {
    return "Undated";
  }
  return new Intl.DateTimeFormat(undefined, {
    month: "short",
    day: "numeric",
    year: "numeric",
  }).format(new Date(value));
}

export function compactSource(source: string | null): string {
  if (!source) {
    return "Source";
  }
  if (source === "omscentral") {
    return "OMSCentral";
  }
  if (source === "reddit") {
    return "Reddit";
  }
  return source;
}
