# Agent 2.0 whole-farm assembly 39.15B

## Cel

Połączyć istniejące modele w rzeczywisty backend `PlannerBackend.solve_whole_farm`. Backend ma uruchamiać i weryfikować modele upraw, zwierząt, ziemi i pracowników oraz przestrzeni. Wynik pozostaje eksperymentem shadow. Nie steruje symulatorem i nie zgłasza wyniku gry.

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

## Walidacja

- testy adapterów i ledgera
- test braku podwójnej gotówki
- test własności pszenicy i nawozu
- test limitów pól, akcji, magazynu i zleceń
- test cięcia oraz wykrycia cyklu
- uruchomienie wszystkich modeli i ich weryfikatorów dla zarejestrowanego scenariusza `3980000`
- Ruff dla zmienionych plików

## Wynik etapu

Etap dostarczy wykonujący się backend shadow i zweryfikowany artefakt. Pełna gra offline wymaga kolejnego etapu: mapowania obserwacji symulatora na `WholeFarmSnapshot` oraz zamiany planów na akcje.
