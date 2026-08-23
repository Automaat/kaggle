# Agent 2.0 live snapshot adapter

## Cel

Przekształcić każdą obserwację pełnej gry offline w spójny
`RollingObservation` i `WholeFarmSnapshot`. Backend ma rozwiązać aktualny stan na
początku każdego z 30 dni. Adapter nie wykonuje akcji i nie zmienia modeli.

## Wejście

- obserwacja `kaggriculture` 1.32.7
- stan publiczny farmy i rynku
- prywatne nasiona, magazyn i inwentarze jednostek
- krok źródłowy 0..718

## Wyjście

- aktualna gotówka i jedna rezerwa gotówki
- zapasy nasion, upraw, produktów, zwierząt i nawozu
- istniejące rośliny, zwierzęta, puste budynki i chwasty
- odblokowane pola, pracownicy i bieżące zatrudnienie
- dzienne limity pól, akcji, magazynu i zleceń do końca gry
- bieżące ceny i stany rynku rozszerzone przez stałą prognozę bazową
- stabilne fingerprinty ekonomii, topologii, tras i postępu

## Kontrakt

`LiveSnapshotAdapter.observe` przyjmuje surową mapę albo obiekt `World`.
Metoda zapisuje dokładnie jeden snapshot pod tożsamością obserwacji i zwraca
`RollingObservation`. `LiveSnapshotAdapter.snapshot` zwraca tylko snapshot
pasujący do przekazanej obserwacji kroczącej. Stan z innego kroku lub epizodu
jest błędem.

Horyzont ma zakres od bieżącego dnia do dnia 29. Krok końcowy to 718. Pole
odblokowane jest dostępne od dziś. Pole zablokowane dostaje znany dzień
odblokowania według kolejności ćwiartek. Bieżące jednostki dają 24 akcje na
pełny przyszły dzień. Dla niepełnego dnia adapter używa pozostałych kroków.

## Walidacja

- prawdziwa obserwacja kroku 0 z replaya
- prawdziwa obserwacja dnia 10 z roślinami, zwierzętami i pustym pastwiskiem
- późny dzień z wieloma ćwiartkami, pracownikami i chwastami
- zgodność sum magazynu, pól i jednostek
- odrzucenie niezgodnego kroku, gracza, planszy i prywatnego stanu
- pełny pytest i Ruff

## Połączenie

Końcowy commit zostanie przeniesiony do worktree całej farmy. Hybrydowy provider
użyje `observe` jako fabryki obserwacji i `snapshot` jako źródła backendu.
