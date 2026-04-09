/**
 * Mapové obrázky akcí — musí být přítomné při buildu (Vite glob).
 * Název: mapa_<eventId>.jpg
 */
const mapModules = import.meta.glob<{ default: string }>("../assets/mapa_*.jpg", {
  eager: true,
});

export function resolveMapAssetUrl(eventId: string): string | undefined {
  const needle = `mapa_${eventId}.jpg`;
  for (const [path, mod] of Object.entries(mapModules)) {
    const norm = path.replace(/\\/g, "/");
    if (norm.endsWith(needle)) {
      return mod.default;
    }
  }
  return undefined;
}

export function hasEventMapImage(eventId: string): boolean {
  return resolveMapAssetUrl(eventId) !== undefined;
}
