/**
 * Jednojazyčný slovník (cs). Klíče odpovídají `t("segment.key")`.
 * Později lze přidat další jazyky paralelně k tomuto souboru.
 */
export const cs = {
  common: {
    loading: "Načítání akce…",
    error: "Chyba",
    hintRetry:
      "Zkontrolujte odkaz nebo zkuste stránku načíst znovu.",
    hintCheckLink: "Zkontrolujte odkaz.",
    backToWineryList: "← Seznam vinařství",
  },
  guide: {
    title: "ScoreTaste",
    skeletonNote: "Skeleton — base path /guide/",
  },
  visitor: {
    infoAria: "Informace o ScoreTaste",
    navAria: "Hlavní navigace",
    navWineries: "Vinařství",
    navMyWines: "Moje vína",
    filterPlaceholder: "Filtrovat vinařství…",
    filterAria: "Filtrovat seznam vinařství",
    filterClearAria: "Zrušit filtr",
    infoTitle: "O aplikaci",
    infoBody:
      "ScoreTaste vám pomáhá zapamatovat si vína během akce a na konci se rozhodnout, co koupit.\n\nVaše označená vína ukládáme anonymně jen v tomto zařízení pro potřeby této akce.\n\nUložení mezi zařízeními a trvalé uložení doplníme později po registraci.",
    modalCloseAria: "Zavřít",
    modalOk: "Rozumím",
    wineCountLabel: "vín",
    filterNoResults: "Žádné vinařství neodpovídá filtru.",
  },
  winery: {
    title: "Vinařství",
    addWine: "Přidat víno",
    cellarWord: "Sklep",
    noneInEvent: "Žádná vinařství v této akci.",
    notFoundStrong: "Vinařství nenalezeno.",
    winesHeading: "Vína",
    noWinesHere: "Žádná vína u tohoto vinařství.",
  },
  wine: {
    liked: "♥ Liked",
    like: "♡ Like",
    wantToBuy: "Koupit",
    wantToBuyActive: "✓ Koupit",
  },
  myWines: {
    title: "Moje vína",
    link: "Moje vína",
    segmentSaved: "Uložené",
    segmentBuy: "K nákupu",
    buyTitle: "K nákupu",
    fieldVariety: "Odrůda",
    fieldPredicate: "Přívlastek",
    fieldVintage: "Ročník",
    empty:
      "Zatím nemáte uložené žádné záznamy — použijte Like nebo Nákup u vín u vinařství.",
    emptySavedCta: "Přejít na seznam vinařství",
    tablistAria: "Přepínání mezi nákupem a uloženými víny",
    buyEmptyShort: "Zatím nemáte žádná vína k nákupu.",
    buyEmptyCta: "Přejít na vinařství",
  },
  errors: {
    notFound: "Akce nenalezena.",
    invalidEvent: "Neplatná data akce.",
    missingEventId: "Chybí identifikátor akce.",
    loadFailed: "Akci se nepodařilo načíst.",
    generic: "Nelze načíst akci.",
    missingUrlParams: "Chybí parametry v URL.",
  },
  contributor: {
    title: "Příspěvek vinařství",
    eventIdLabel: "eventId:",
    wineryIdLabel: "wineryId:",
    missing: "(chybí)",
  },
} as const;
