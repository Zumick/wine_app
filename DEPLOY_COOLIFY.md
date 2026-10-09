# Deploy: Coolify na Hostinger VPS

Cíl migrace z Renderu (říjen 2026). Stejný server jako ZUMI, proxy Traefik v Coolify.

## Aplikace

- Zdroj: GitHub `Zumick/wine_app`, větev `main`, build pack **Dockerfile** (root repa).
- Image: stage 1 builduje průvodce `scoretaste/` (Vite, `/guide/`), stage 2 Python 3.12 + gunicorn.
- Port `8000`. Gunicorn běží s **1 workerem a 8 vlákny** (SQLite nesnese víc procesů zapisujících najednou).
- **Persistentní volume** připojit na `/data`:
  - `/data/wine.db` — databáze (`DB_DIR=/data`, nastaveno v image)
  - `/data/guide-media/` — nahraná loga a mapy akcí (`GUIDE_MEDIA_DIR`, nastaveno v image)

Bez `GUIDE_MEDIA_DIR` se uploady ukládají do `scoretaste/dist/assets`, který každý deploy přepíše (na Renderu se tak nahraná loga a mapy ztrácela). Soubory commitnuté v `scoretaste/public/assets` se dál servírují z buildu jako záloha.

## Proměnné prostředí

| Proměnná | Poznámka |
| --- | --- |
| `FLASK_SECRET_KEY` | stejná jako na Renderu (jinak se odhlásí přihlášení) |
| `PUBLIC_BASE_URL`, `PRIMARY_DOMAIN`, `CZECH_DOMAIN` | stejné jako na Renderu |
| `GUIDE_DEMO_DOMAIN`, `SCORE_DEMO_DOMAIN`, `LEGACY_REDIRECT_HOSTS`, `ENABLE_LEGACY_HOST_REDIRECT`, `REDIRECT_DEMO_HOSTS_TO_CANONICAL` | podle Renderu |
| `WINEAPP_PILOT_MONITOR` | volitelné |
| `VITE_PUBLIC_GUIDE_ORIGIN`, `VITE_PILOT_MONITOR` | jen pokud jsou na Renderu; v Coolify jako **Build Variable** |
| `DB_DIR`, `GUIDE_MEDIA_DIR`, `PORT` | nastavené v image, neměnit |

Všechny domény (včetně legacy, např. degus.cz) přidat do domén aplikace v Coolify, aby Traefik vystavil certifikáty a redirect fungoval.

## Přenos dat z Renderu

1. Zastavit zápisy (mimo akci).
2. Z Render disku stáhnout `wine.db` (a `wine.db-wal`, `wine.db-shm`, pokud existují). Postup zálohy viz `docs/db-backup-before-migration.md`.
3. Nahrát do volume `/data` na VPS (vlastník UID 1000), stejně jako případné nahrané `logo_*.png` a `mapa_*.jpg` do `/data/guide-media/`.
4. Spustit aplikaci a ověřit.

## Zálohy

Volume `/data` zálohovat cronem na VPS (kopie `wine.db` přes `sqlite3 .backup`, ne prostým kopírováním za běhu) + `guide-media/`.
