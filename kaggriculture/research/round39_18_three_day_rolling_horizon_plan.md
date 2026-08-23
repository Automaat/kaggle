# Round 39.18: trzydniowy horyzont kroczący

## Status

Plan zapisany. Eksperyment nie został uruchomiony. Punktem odniesienia będzie ukończony wynik obecnego ramienia `strategy-2.0-execution-1.14` z pełnym horyzontem.

## Hipoteza

Pełny model do dnia 29 poświęca dużo czasu na decyzje, które następny dzienny solve zmieni. Dokładny model trzech najbliższych dni zmniejszy czas i niestabilność planu. Wartość końcowa zachowa długoterminową opłacalność inwestycji.

## Ramiona

- A: obecny pełny horyzont od bieżącego dnia do dnia 29, wykonanie 1.14.
- B: dokładnie trzy dni bez ogona strategicznego, wykonanie 1.14.
- C: trzy dokładne dni oraz strategiczny ogon do dnia 29, wykonanie 1.14.

Każde ramię będzie osobnym eksperymentem, artefaktem i replayem. Najpierw porównamy A, B i C bez innych zmian. Dopiero potem połączymy zwycięski horyzont z najlepszym niezależnym planerem tras, przestrzeni lub ekonomii.

## Ryzyko krótkowzroczności B

Czyste trzy dni mogą odrzucić decyzje, których zwrot jest późniejszy:

- truskawki z cyklem około 10 dni
- zwierzęta, budynki oraz regularna obsługa
- zakup ziemi
- produkcja pszenicy przed przyszłym karmieniem
- nasiona i zapasy potrzebne po końcu krótkiego okna

Ramię B jest kontrolą krótkowzroczności. Nie zostanie domyślnym agentem tylko dlatego, że liczy się szybciej.

## Model bliskiego terminu

Każdy dzienny solve obejmie bieżący dzień oraz dwa kolejne dni. Zachowa wspólny ledger gotówki, pól, akcji, magazynu, zleceń, pszenicy i nawozu. Uwzględni aktualne uprawy, zwierzęta, budynki, działki, pracowników, nasiona, produkty, sklepy, chwasty i pozycje wykonawców.

Znane otwarcia sklepów w oknie wejdą do cen, popytu i limitów rynku. Otwarcie sklepu, nowe chwasty, zmiana topologii, utrata zasobu albo niespełniony warunek wykonania unieważni plan i wymusi nowy solve.

Agent przeliczy plan na krokach `0, 24, ..., 696`. Z planu zatwierdzi tylko zlecenia, cele przestrzenne i zadania bieżącego dnia. Dni drugi i trzeci są prognozą i nie tworzą trwałego zobowiązania.

## Wartość końcowa i ogon C

Na końcu trzeciego dnia ramię C doda wartość dalszego stanu do dnia 29:

- gotówka: wartość nominalna bez dyskonta
- magazyn: przewidywana wartość sprzedaży pomniejszona o ryzyko popytu, limity zleceń i koszt dostawy
- nasiona: maksymalna realna wartość przyszłego zasiewu, nie cena zakupu
- rosnące uprawy: oczekiwany plon i kolejne zbiory pomniejszone o podlewanie, nawóz, zbiór, transport i ryzyko końca gry
- zwierzęta: zdyskontowana produkcja do dnia 29 pomniejszona o pszenicę, opiekę, zbiór, magazyn i transport
- budynki dla zwierząt: wartość tylko wtedy, gdy ogon może wykorzystać ich pojemność
- ziemia: krańcowa wartość pól ograniczona przyszłymi akcjami, gotówką i liczbą dni; koszt zakupu nie może być policzony drugi raz
- pszenica: wartość sprzedaży albo uniknięty koszt zakupu paszy, zależnie od przyszłego zapotrzebowania
- nawóz: dodatkowa wartość realnych przyszłych plonów, ograniczona liczbą upraw i akcji

Ogon użyje znanego harmonogramu sklepów. Wartości będą konserwatywne i nie przekroczą wykonalnej wartości pełnego modelu dla tego samego stanu. Współczynniki zostaną skalibrowane na zapisanych stanach A, bez użycia wyników seedów testowych do strojenia.

## Przeliczanie i zdarzenia

- pełny solve raz dziennie
- dodatkowy solve po otwarciu sklepu lub zdarzeniu ekonomicznym
- naprawa przestrzeni po zmianie topologii
- naprawa trasy po zmianie wykonawców lub niespełnionym warunku
- maksymalnie pięć iteracji cięć i jawne wykrycie cyklu
- brak cichego powrotu do ekonomii 1.14 po błędzie
- zapis failure artifact z obserwacją, ostatnim poprawnym planem, cięciami i wyjątkiem

## Metryki

Dla każdego dziennego solve zapiszemy:

- czas całkowity, czas modeli i percentyle p50, p95 oraz maksimum
- wynik, końcową gotówkę i różnicę względem A oraz frozen 1.14
- wykonalność modeli, ledgerów, przestrzeni, tras i zleceń
- błąd prognozy gotówki po 1, 2 i 3 dniach oraz na końcu gry
- zmianę planu między dniami: zakupy, uprawy, zwierzęta, ziemia, pracownicy i cele pól
- wykorzystanie akcji, rezerwy tras, pól, magazynu i zleceń
- liczbę dodatkowych przeliczeń po sklepach i zdarzeniach
- liczbę cięć, cykli, timeoutów i odrzuconych kandydatów

## Zestaw porównawczy

Ramiona A, B i C użyją tych samych seedów, miejsc i komparatora. Minimalny zestaw to 30 wcześniej ustalonych seedów na obu miejscach, czyli 60 sparowanych gier na ramię. Strojenie i test końcowy użyją rozłącznych seedów. Każda gra ma 30 dni i 720 kroków.

## Bramki promocji

Ramię może przejść dalej tylko wtedy, gdy:

- kończy 100% gier i ma dokładnie 30 dziennych epochów na grę
- wszystkie weryfikatory i wspólny ledger są czyste
- nie wykonuje ekonomicznych zleceń spoza planu
- maksymalny dzienny solve jest krótszy niż 300 sekund
- p95 czasu dziennego jest nie większy niż A
- mediana sparowanej różnicy wyniku względem A jest dodatnia
- dolna granica 95% bootstrap dla średniej różnicy nie jest ujemna
- współczynnik zwycięstw względem A wynosi co najmniej 55%
- błąd prognozy trzydniowej nie jest większy niż A
- plan churn i niewykorzystane akcje nie rosną bez poprawy wyniku

B nie może przejść dalej, jeżeli ogranicza truskawki, zwierzęta, ziemię lub pszenicę i traci wynik końcowy. C nie może przejść dalej, jeżeli ogon zawyża wartość niewykonalnych zasobów.

## Artefakty

Każde ramię zapisze osobno konfigurację, wynik, 30 dziennych trace, progress JSON, replay, czasy modeli, prognozy i failure artifacts. Nazwy będą zawierać ramię, seed i miejsce. Po osobnych wynikach powstanie jedna tabela sparowanych porównań. Kombinacja zostanie uruchomiona dopiero po wyborze zwycięskiego ramienia.

## Nierozstrzygnięte

- dokładne współczynniki salvage przed zamrożeniem testowych seedów
- ostateczna lista 30 seedów strojenia i 30 rozłącznych seedów testowych
