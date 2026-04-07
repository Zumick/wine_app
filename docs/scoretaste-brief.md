# ScoreTaste — produktový brief (MVP)

## Kontext a brand

- **ScoreTaste** je nový produktový směr pro část „průvodce degustací“.
- ScoreTaste **není rozšíření současné bodované aplikace Degus**.
- ScoreTaste bude nasazen **v tomtéž repozitáři a stejném Flask serveru**, ale jako **samostatná frontendová aplikace**.
- **Sdílený je pouze vstupní rozcestník `/guide`**.
- Samotná návštěvnická aplikace ScoreTaste poběží **pod namespace `/guide/...`**, odděleně od stávajících obrazovek Degus.
- ScoreTaste **není scoring systém**.

---

## Produkt (vlastními slovy)

ScoreTaste je **mobilní web (PWA)** pro jednu konkrétní akci (např. otevřené sklepy), který:

- pomáhá návštěvníkům **zapamatovat si vína**
- pomáhá jim **rozhodnout, co koupit na konci akce**
- pomáhá organizátorům **zvýšit konverzi do nákupu**

**Není to:**
- bodování / scoring
- soutěž
- sociální síť
- globální databáze
- e-shop

**Je to:**
👉 degustační paměť + nákupní shortlist

---

## Primární uživatelé

1. **Návštěvník**
   - vstup přes QR
   - bez loginu
   - mobil, často v pohybu
   - chce 1 tap interakci
   - chce jasný seznam na konci

2. **Organizátor**
   - chce minimum práce
   - chce více prodaných lahví

3. **Vinařství (contributor)**
   - není plnohodnotný user
   - používá jednoduchý link bez účtu
   - zadává data pouze pro jednu akci

---

## Jádrový flow

1. QR → `/guide/e/:eventId`
2. Seznam vinařství
3. Detail vinařství
4. Ukládání vín
5. My Wines
6. Nákupní pohled

---

## MVP scope

- jedna akce (eventId)
- seznam vinařství
- detail vinařství
- vína (název, odrůda, ročník, popis)
- akce:
  - Liked
  - Want to buy
- My Wines
- bez loginu

---

## Out of scope

- účty
- scoring
- sociální funkce
- historie
- e-shop
- `/degustace/{id}` jako UI

---

## Datový model

- Event
- Winery
- Wine

- VisitorWineAction:
  - wineId
  - liked
  - wantToBuy
  - updatedAt

- WinerySubmissionLink:
  - eventId
  - wineryId
  - token

---

## Storage

- localStorage per eventId
- bez sync

---

## KPI

- % users with ≥3 liked wines
- % users opening shopping view

---

## Architektura

- `/guide` = vstup
- `/guide/...` = app
- Flask = host
- žádné UI ve Flasku