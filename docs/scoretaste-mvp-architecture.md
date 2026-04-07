# ScoreTaste MVP — Architecture

## 1. Overview

- SPA (React + Vite)
- Flask host (single server)
- `/guide` = vstupní rozcestník (server-side)
- `/guide/...` = ScoreTaste aplikace (SPA)

---

## 2. Routing

### Server (Flask)
- `/guide` → seznam akcí (DB)
- `/guide/data/events/<eventId>.json` → katalog akce (JSON)

### Frontend (React)
- `/guide/e/:eventId`
- `/guide/e/:eventId/wineries`
- `/guide/e/:eventId/wineries/:wineryId`
- `/guide/e/:eventId/my`
- `/guide/contribute/:eventId/:wineryId?t=...` (později)

---

## 3. Identity

- `eventId = degustace.id` (DB)
- stejné ID se používá:
  - v URL (`/guide/e/:eventId`)
  - v JSON (`events/{eventId}.json`)

---

## 4. Data model (MVP)

### DB (Flask)
- tabulka `degustace`
- slouží pouze pro:
  - seznam akcí (`/guide`)
  - metadata (název, datum, místo)

### JSON (source of truth pro katalog)
- umístění:
  - `scoretaste/public/guide/data/events/{eventId}.json`
- obsah:
  - event
  - wineries
  - wines

---

## 5. JSON lifecycle (důležité)

- vznik:
  - při vytvoření `pruvodce` akce v `/guide`
- správa:
  - přes interní admin (`/guide/admin/<eventId>`)
- čtení:
  - frontend fetch `/guide/data/events/{eventId}.json`

---

## 6. State

- katalog:
  - načten jednou per event
  - držen v memory (frontend)

- user actions:
  - localStorage (per eventId)
  - zatím neimplementováno

---

## 7. Flask boundary

Flask:
- render `/guide`
- hostuje SPA
- servuje JSON katalog

Flask NESMÍ:
- řídit UI SPA
- obsahovat business logiku frontend flow

---

## 8. Integration pravidla

- nepoužívat `/degustace/{id}` pro ScoreTaste UI
- nepoužívat DB jako zdroj katalogu
- nepoužívat jiné ID než `degustace.id`

---

## 9. Implementation order (aktuální realita)

1. routing ✔
2. event JSON loader ✔
3. `/guide` integrace ✔
4. vytvoření JSON při založení akce ✔
5. interní správa katalogu ✔
6. visitor actions (next)

---

## 10. Locked decisions

- SPA běží pod `/guide/...`
- Flask = host + data provider
- JSON = source of truth katalogu (MVP)
- eventId = jednotná identita napříč systémem