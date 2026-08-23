# Agent 2.0 whole-farm assembly 39.15B

## Cel

Połączyć istniejące modele w rzeczywisty backend `PlannerBackend.solve_whole_farm`, a następnie rozegrać pełną grę offline. Backend ma uruchamiać i weryfikować modele upraw, zwierząt, ziemi i pracowników oraz przestrzeni. Strategia 2.0 ma sterować ekonomią. Frozen 1.14 ma wykonać ruchy i akcje.

## Źródła

- baza koordynatora i prognozy sklepów: `1b0c9f5`
- końcowy model upraw: `6c7e587`
- końcowy model zwierząt: `0b1c433`
- końcowy model ziemi i pracowników: `27225bd`
- końcowy planer przestrzeni: `c029420`
- zamrożony komparator 1.14.0: `b74a3ea`

Kod modeli zostanie przeniesiony z commitów implementacyjnych. Dokumentacja eksperymentów i stare artefakty nie zostaną połączone.

## Kontrakt danych

`WholeFarmSnapshot` opisze jeden obserwowany stan: gotówkę, zapasy, rośliny, zwierzęta, komórki planszy, odblokowaną ziemię, pracowników oraz wspólne limity zasobów.

`SharedResourceLedger` będzie jedynym wynikiem zasobowym. Dla każdego dnia zapisze:

- jedno saldo gotówki
- produkcję i zużycie pszenicy
- produkcję i zużycie nawozu
- użycie pól przez uprawy i zwierzęta
- użycie akcji przez uprawy, zwierzęta i rezerwę tras
- wspólne użycie magazynu
- wspólne użycie zleceń rynku

Ledger odrzuci ujemne salda i przekroczenia limitów.

## Łączenie modeli

1. Rozwiązać cztery warianty ziemi i pracowników. Zweryfikować każdy wynik.
2. Wybrać najlepszy poprawny wariant według wyniku własnego modelu. Odjąć koszt inwestycji tylko raz.
3. Rozwiązać model zwierząt z pozostałą gotówką, polami, akcjami, magazynem i zleceniami.
4. Przekazać liczbę karmień do modelu upraw jako popyt na pszenicę.
5. Usunąć zakup pszenicy z przepływu zwierząt. Model upraw będzie jedynym właścicielem podaży i zakupu pszenicy.
6. Przekazać zebrany nawóz do modelu upraw. Usunąć sprzedaż nawozu z przepływu zwierząt. Model upraw będzie jedynym właścicielem nawozu.
7. Odjąć od modelu upraw faktyczne użycie akcji, pól, magazynu i zleceń przez zwierzęta oraz stałą rezerwę tras.
8. Rozwiązać i zweryfikować model upraw. Jego saldo po wspólnych przepływach będzie jedynym saldem końcowym.
9. Utworzyć zamiary zakupu zwierząt i rozwiązać planer przestrzeni.
10. Gdy przestrzeń odrzuci zakup lub ledger wykryje konflikt, dodać cięcie i ponowić obliczenie. Limit to pięć iteracji. Powtórzony podpis cięcia kończy cykl błędem.
11. Utworzyć zależne `EconomicPlanRef`, `SpacePlanRef` i `RoutePlanRef`. Trasy pozostają konserwatywną rezerwą shadow.
12. Wyeksportować jawny `ExecutionHandoff`. Frozen 1.14 wykona kroki i akcje przez istniejące seam'y `Agent2Policy` i `BaselinePolicy`.
13. Zbudować adapter `observation → WholeFarmSnapshot` z aktualnej obserwacji symulatora.
14. Wywołać `RollingCoordinator.prepare` dla każdej obserwacji.
15. Wymusić pełny solve na krokach `0, 24, ..., 696`. Brak dowolnego dziennego solve zakończy grę błędem.
16. Przeliczyć plan także po zmianie podpisu sklepów i po zdarzeniu, które unieważnia ekonomię, przestrzeń lub trasę.
17. Zachować ostatni poprawny handoff między przeliczeniami. Frozen 1.14 wykona go krokowo.
18. Uruchomić dwa ramiona wykonania: kontrolne `strategy-2.0-execution-1.14` oraz drugie z planerem tras 2.0.
19. Rozegrać 30-dniową grę offline przeciwko frozen 1.14 i zapisać osobny replay.

Pierwsza grywalna wersja będzie oznaczona `strategy-2.0-execution-1.14`. Nie będzie zgłoszona jako pełny agent 2.0.

## Ślad decyzji

Każdy epoch i dzień zapisze kompaktowy, deterministyczny `DecisionTrace`:

- powody przeliczenia
- obserwowany wspólny ledger zasobów
- wszystkie kandydaty ziemi i zatrudniania z wynikiem lub powodem odrzucenia
- wybrane plany upraw, zwierząt, inwestycji i przestrzeni
- zlecenia rynku i cele przestrzenne przekazane wykonawcy 1.14
- ograniczenia, cięcia i stabilne fingerprinty

Replay gry będzie osobnym plikiem.

## Walidacja

- testy adapterów i ledgera
- test braku podwójnej gotówki
- test własności pszenicy i nawozu
- test limitów pól, akcji, magazynu i zleceń
- test cięcia oraz wykrycia cyklu
- uruchomienie wszystkich modeli i ich weryfikatorów dla zarejestrowanego scenariusza `3980000`
- test adaptera obserwacji dla stanu początkowego, zmian dziennych, sklepów i zdarzeń
- test 30 obowiązkowych dziennych epochów
- pełna gra offline z osobnym replayem i śladem minimum 30 dziennych decyzji
- porównanie wyniku gry z frozen 1.14
- Ruff dla zmienionych plików

## Wynik etapu

Etap dostarczy pełny hybrydowy agent offline, zweryfikowany artefakt, replay, komplet minimum 30 dziennych śladów decyzji oraz wynik przeciwko frozen 1.14. Wynik będzie oznaczony `strategy-2.0-execution-1.14`. Nie będzie zgłoszony jako pełny agent 2.0.
