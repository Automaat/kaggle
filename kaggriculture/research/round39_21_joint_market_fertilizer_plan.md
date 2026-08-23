# Round 39.21: wspólny rynek, zwierzęta i nawóz

## Status

Plan na następny etap. Implementacja i gry nie zostały uruchomione.

## Cel

Zastąpić błędne założenia ekonomii 2.0 wspólnym modelem, który łączy:

- własną podaż z przyszłą ceną rynku
- widoczną podaż przeciwnika z trzema scenariuszami
- pszenicę, karmienie, zwierzęta i produkcję nawozu
- zebranie, przeniesienie i użycie nawozu
- podwojony plon konkretnej uprawy
- sprzedaż plonu po cenie zależnej od łącznej podaży

Model nie dostanie ręcznego zakazu melonów. Musi odrzucić nadmierną produkcję
przez realną cenę, koszt pracy, czas do gotówki i ograniczenia wykonania.

## Architektura

Ekonomia zachowa pełny horyzont do dnia 29. Zatwierdzi tylko decyzje bieżącego
dnia. Planer tras użyje krótkiego okna 3-5 dni i będzie naprawiał plan po
zdarzeniu lub niespełnionym warunku.

Model zwierząt pozostanie generatorem 5-10 zweryfikowanych profili zasobów.
Główny model upraw i rynku wybierze dokładnie jeden profil. Profil zwierząt
przekaże dla każdego dnia:

- koszt zakupu zwierząt i budynków
- zajęte pola, akcje, magazyn i zlecenia
- zużycie pszenicy
- mleko, wełnę, jajka i nawóz
- cele budowy, umieszczenia, karmienia, opieki i zbioru

Pierwszy zestaw profili obejmie brak zwierząt, 1-4 owce oraz mały zestaw
mieszany z krowami, gęsiami i owcami. Profil nie sprzeda zasobów samodzielnie.
Wspólny model wybierze użycie lub sprzedaż każdego produktu.

## Endogeniczna cena rynku

Dla każdego produktu i dnia model wyliczy:

`zapas jutro = zapas dziś + nasza sprzedaż + podaż przeciwnika - popyt miasta`

Każda kolejna nasza jednostka dostanie cenę dla kolejnego poziomu zapasu.
Wpływ podaży pozostanie w następnych dniach. Po osiągnięciu ceny minimalnej
dalsze jednostki zachowają cenę 1 zgodnie z regułami środowiska.

Model użyje segmentów równych cen zamiast jednej zmiennej na każdą możliwą
jednostkę. Test referencyjny z jednostkowymi rangami sprawdzi dokładność
segmentacji.

Prognoza przeciwnika użyje tylko publicznie widocznych upraw. Nie odczyta
ukrytego magazynu ani przyszłych decyzji. Powstaną trzy scenariusze:

- `low`: późna lub mała sprzedaż widocznego plonu
- `base`: sprzedaż po dojrzeniu i jednodniowym opóźnieniu trasy
- `high`: szybka sprzedaż całego widocznego plonu

## Wspólny przepływ nawozu

Dzienny bilans nawozu będzie równy:

`zapas poprzedni + zakup + zebranie od zwierząt - użycie - sprzedaż`

Uprawy jednorazowe WHEAT, CARROT i MELON dostaną nawożone warianty. TOMATO i
STRAWBERRY zachowają warianty wielokrotnego nawożenia. Generator opcji usunie
harmonogramy zdominowane.

Jedna akcja `FERTILIZE` zużywa jedną sztukę nawozu. Obejmuje bieżący dzień oraz
dwa następne dni. Podwaja tylko produkcje przypadające w tym okresie. Wartość
nawozu wynika z dodatkowego sprzedanego plonu po nowej cenie rynku, a nie ze
stałego współczynnika.

## Chodzenie i akcje

Każda sztuka nawozu musi mieć wykonalną ścieżkę pochodzenia i użycia:

1. `COLLECT_FERTILIZER` przy zwierzęciu
2. ruch od zwierzęcia do wybranej uprawy
3. `FERTILIZE` przy uprawie

Jeżeli ten sam pracownik nie przenosi nawozu bezpośrednio, plan doda:

1. ruch do szopy
2. `DROP`
3. `PICKUP FERTILIZER` przez innego pracownika
4. ruch do uprawy
5. `FERTILIZE`

Nawóz istniejący w szopie wymaga `PICKUP`, ruchu i `FERTILIZE`. Model policzy
osobno akcje biologiczne, ruch, `PICKUP` i `DROP`. Nie policzy tej samej akcji
dwa razy.

Planer tras utworzy zależność od konkretnego tokenu nawozu. Nie pozwoli użyć
ani sprzedać nawozu przed zebraniem i dostarczeniem. Produkcja zwierzęcia z
końca dnia będzie dostępna najwcześniej następnego dnia.

Jeżeli dokładna trasa nie mieści się w 24 krokach, backend doda ograniczenie
zasobu albo zakaz tej kombinacji i ponownie rozwiąże model.

## Osobne eksperymenty

### 39.21A: własna podaż

Porównać bieżące egzogeniczne ceny z trwałym wpływem własnej podaży. Bez zmian
zwierząt i nawozu.

### 39.21B: podaż przeciwnika

Dodać scenariusze `low/base/high` dla widocznych upraw przeciwnika. Bez zmian
zwierząt i nawozu.

### 39.21C: nawóz z istniejącego zapasu

Dodać nawożone warianty wszystkich upraw, sprzedaż nawozu oraz dokładne akcje
tras. Użyć stałego, zadanego zapasu nawozu. Bez nowych zwierząt.

### 39.21D: nawóz ze zwierząt

Dodać profile zwierząt i wspólny bilans pszenicy, produktów oraz nawozu. Ceny
pozostawić jak w zwycięskim ramieniu rynku.

### 39.21E: pełne połączenie

Połączyć zwycięskie ramiona rynku i nawozu. Dopiero to ramię rozegra pełne gry
przeciw 1.14 na obu miejscach.

Każdy eksperyment dostanie osobny commit, konfigurację, wynik, trace i replay.
Podejścia zostaną połączone dopiero po osobnej walidacji.

## Testy obowiązkowe

- sprzedaż dnia 0 obniża cenę dnia 1
- podaż przeciwnika obniża naszą prognozowaną cenę
- popyt miasta może ponownie podnieść cenę
- cena minimalna i zapas rynku są zgodne z symulatorem
- nawożenie WHEAT, CARROT i MELON daje dokładny plon
- nawożenie TOMATO i STRAWBERRY obejmuje właściwe produkcje
- nawóz ze zwierzęcia jest dostępny dopiero po produkcji i zebraniu
- model wybiera między użyciem nawozu i sprzedażą
- bezpośrednie przeniesienie wymaga `COLLECT_FERTILIZER` i `FERTILIZE`
- inny pracownik wymaga `DROP`, `PICKUP` i `FERTILIZE`
- brak zależności tokenu nawozu jest błędem weryfikatora
- przekroczenie 24 kroków odrzuca plan
- zaakceptowana ilość zakupu nigdy nie przekracza planowanej ilości
- seed 3980000 nie wybiera zalania rynku melonami w scenariuszu wysokiej podaży
- trace pokazuje wybrany profil zwierząt, nawóz i krańcowe ceny

## Metryki i bramki

Zapisać dla każdego dnia:

- stan rynku przed i po własnej oraz przewidywanej sprzedaży przeciwnika
- krańcową cenę każdej sprzedanej grupy
- plon bazowy i dodatkowy plon z nawozu
- nawóz zebrany, użyty, sprzedany i pozostawiony
- akcje `COLLECT_FERTILIZER`, `DROP`, `PICKUP`, `FERTILIZE` i ruch
- koszty paszy, zwierząt, budynków i zatrudnienia
- prognozowaną i rzeczywistą gotówkę
- czas każdego modelu, liczbę zmiennych, ograniczeń, cięć i napraw tras

Ramię przechodzi dalej tylko wtedy, gdy wszystkie weryfikatory są czyste,
kończy 100% gier, nie powtarza zleceń i poprawia sparowany wynik. Cel czasu to
p95 poniżej 60 sekund na dzienny solve przy luce 1-2%. Jeżeli rynek przekroczy
limit, dokładny wpływ ceny obejmie 7-10 dni, a dalszy wpływ pozostanie w
konserwatywnym ogonie. Sprzężenie nawozu nie zostanie wyłączone.

## Kolejność pracy

1. Zarejestrować negatywny wynik próby 7 i przerwane przebiegi pełne.
2. Dokończyć ACK zleceń i naprawę handoffu.
3. Wdrożyć oraz zmierzyć 39.21A.
4. Wdrożyć oraz zmierzyć 39.21B.
5. Wdrożyć oraz zmierzyć 39.21C.
6. Wdrożyć oraz zmierzyć 39.21D.
7. Wdrożyć 39.21E i wykonać przegląd adversarialny.
8. Rozegrać pełne gry przeciw 1.14 i zapisać HTML.

## Nierozstrzygnięte

- liczba profili mieszanych zwierząt przed przekroczeniem celu czasu
- wagi scenariuszy podaży przeciwnika
- dokładna granica segmentacji równych cen
