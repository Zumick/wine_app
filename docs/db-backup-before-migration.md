# Záloha SQLite před migrací / rizikovou změnou schématu

Cesta k databázi je v [`db.py`](../db.py):

- **`DB_DIR` není nastavený:** soubor `wine.db` v **aktuálním pracovním adresáři** procesu (typicky kořen projektu při `python app.py` z `WineApp`).
- **`DB_DIR` je nastavený** (např. na Renderu): `{DB_DIR}\wine.db` (na Windows použijte skutečnou hodnotu proměnné).

Před zálohou **zastavte Flask** (nebo jakýkoli proces, který DB drží otevřenou), aby nevznikaly nekonzistentní kopie. Pak zkopírujte i `wine.db-wal` a `wine.db-shm`, **pokud existují** (WAL režim).

---

## Windows (PowerShell)

Nastavte `$Db` na plnou cestu k `wine.db` (upravte podle umístění projektu):

```powershell
# Příklad: DB v kořeni repozitáře WineApp
$Db = "C:\DEV\WEB\WineApp\wine.db"
$DestDir = "C:\DEV\WEB\WineApp\backups"
$ts = Get-Date -Format "yyyyMMdd_HHmm"
New-Item -ItemType Directory -Force -Path $DestDir | Out-Null
Copy-Item -LiteralPath $Db -Destination (Join-Path $DestDir "wineapp_backup_$ts.db")
if (Test-Path "$Db-wal") { Copy-Item -LiteralPath "$Db-wal" -Destination (Join-Path $DestDir "wineapp_backup_$ts.db-wal") }
if (Test-Path "$Db-shm") { Copy-Item -LiteralPath "$Db-shm" -Destination (Join-Path $DestDir "wineapp_backup_$ts.db-shm") }
Write-Host "Hotovo: $DestDir\wineapp_backup_$ts.db"
```

Jednoradý základ (bez `-wal`/`-shm`, z adresáře kde leží `wine.db`):

```powershell
$ts = Get-Date -Format "yyyyMMdd_HHmm"; Copy-Item .\wine.db ".\wineapp_backup_$ts.db"
```

---

## Linux / macOS (bash)

```bash
DB="/cesta/k/wine.db"
DEST_DIR="/cesta/k/wineapp/backups"
TS=$(date +%Y%m%d_%H%M)
mkdir -p "$DEST_DIR"
cp "$DB" "$DEST_DIR/wineapp_backup_${TS}.db"
[ -f "${DB}-wal" ] && cp "${DB}-wal" "$DEST_DIR/wineapp_backup_${TS}.db-wal"
[ -f "${DB}-shm" ] && cp "${DB}-shm" "$DEST_DIR/wineapp_backup_${TS}.db-shm"
echo "Hotovo: $DEST_DIR/wineapp_backup_${TS}.db"
```

---

## Pro agenty / workflow

1. Ověřit cestu k `wine.db` (`db.py`, případně `echo $env:DB_DIR`).
2. Zastavit aplikaci používající DB.
3. Spustit kopírování včetně `-wal`/`-shm`, pokud jsou přítomny.
4. **Teprve potom** měnit `init_db`, migrace nebo rizikový kód.
5. **Volitelně:** migraci neprovádět, dokud uživatel nepotvrdí, že záloha existuje a cesta je správná.

Obnova: zkopírovat záložní `wineapp_backup_*.db` zpět na `wine.db` (při zastavené aplikaci); pokud jste zálohovali i `-wal`/`-shm`, obnovte je stejně pojmenované vedle `wine.db`, nebo je smažte a nechte SQLite znovu vytvořit (může ztratit necommitnuté změny z WAL).

---

## Lokální vývoj (testovací data): čistá DB bez zálohy

Pro lokální SQLite (`wine.db` dle `db.py`) stačí při rozbitém schématu nebo experimentech s migracemi smazat soubor databáze a nechat aplikaci znovu spustit `init_db`.

1. Zastavte Flask / proces, který drží `wine.db` otevřenou.
2. V kořeni projektu (nebo tam, kde leží `wine.db`) smažte `wine.db` a případně `wine.db-wal`, `wine.db-shm`.
3. Spusťte znovu aplikaci (`python app.py` nebo váš příkaz) — při startu se vytvoří nová konzistentní databáze.

**PowerShell (příklad cesta k repozitáři):**

```powershell
Set-Location "C:\DEV\WEB\WineApp"
Remove-Item -LiteralPath .\wine.db -ErrorAction SilentlyContinue
Remove-Item -LiteralPath .\wine.db-wal -ErrorAction SilentlyContinue
Remove-Item -LiteralPath .\wine.db-shm -ErrorAction SilentlyContinue
# pak znovu: python app.py
```

Tím neřešíte produkční zálohování — jde jen o rychlé zotavení vývojového prostředí.
