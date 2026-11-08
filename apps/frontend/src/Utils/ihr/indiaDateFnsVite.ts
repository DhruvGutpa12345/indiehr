// IndieHR (P1-T7b): Vite plugin (used in vite.config.mts) that resolves "date-fns" imported
// from our app source (src/) to the India shim src/Utils/ihr/indiaDateFns.ts.
// Not redirected: the shim itself (it needs the real package), anything outside src/
// (node_modules such as react-day-picker, local plugin apps) and virtual modules.
// A plain resolve.alias cannot do this, because it would also catch node_modules.
// No imports, so it type-checks in both the app and the Vite config programs.

export const DATE_FNS_SPECIFIERS = [
  "date-fns",
  "date-fns/format",
  "date-fns/formatDate",
];

const normalise = (p: string) => p.split("?")[0].replace(/\\/g, "/");

/** The shim path for this import, or null when the real date-fns must be used. */
export function dateFnsShimFor(
  srcDir: string,
  source: string,
  importer: string | undefined,
): string | null {
  if (!DATE_FNS_SPECIFIERS.includes(source) || !importer) return null;
  const root = normalise(srcDir).replace(/\/+$/, "");
  const shim = `${root}/Utils/ihr/indiaDateFns.ts`;
  const from = normalise(importer);
  if (from.startsWith("\0") || !from.startsWith(`${root}/`)) return null;
  if (from === shim) return null;
  return shim;
}

export function indiaDateFnsPlugin(srcDir: string) {
  return {
    name: "ihr-india-date-fns",
    enforce: "pre" as const,
    resolveId(source: string, importer?: string) {
      return dateFnsShimFor(srcDir, source, importer);
    },
  };
}
