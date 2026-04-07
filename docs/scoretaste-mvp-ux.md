# ScoreTaste MVP — UX (aktuální stav)

## 1. Screen map

### Server
- `/guide` → seznam akcí + vytvoření akce + správa katalogu

### SPA
- Winery list
- Winery detail
- (My Wines – zatím neimplementováno)

### Admin (debug)
- `/guide/admin/:eventId`
  - správa vinařství
  - správa vín

---

## 2. Navigation

- `/guide` → výběr akce
- `/guide/e/:eventId` → redirect na wineries
- `/guide/e/:eventId/wineries`
- `/guide/e/:eventId/wineries/:wineryId`

Bez bottom navigation (MVP)

---

## 3. Aktuální user flow

1. otevře `/guide`
2. vybere nebo vytvoří akci
3. přejde na `/guide/e/:eventId`
4. zobrazí se seznam vinařství
5. klik → detail vinařství

---

## 4. Winery list

- název vinařství
- (optional) počet vín
- jednoduchý list

---

## 5. Winery detail

- název vinařství
- seznam vín
- víno:
  - název
  - odrůda
  - ročník
  - popis

---

## 6. Admin katalog (dočasný UX)

- přidání vinařství
- přidání vína
- bez validací UX
- bez designu

Cíl:
👉 naplnit data, ne řešit UX

---

## 7. Budoucí (neimplementováno)

- ❤️ liked
- 🛒 want to buy
- My Wines (2 segmenty)

---

## 8. Empty / error states

- invalid event
- žádná data
- offline (později)

---

## 9. Guide entry

- seznam akcí (DB)
- tlačítko vytvořit akci
- link na admin katalog

---

## 10. Simplifications

- žádný onboarding
- žádné filtry
- žádné profily
- žádný contributor flow (zatím)