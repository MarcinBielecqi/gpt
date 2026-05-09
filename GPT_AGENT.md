# GPT Agent — zasady pracy

Ten plik opisuje ogólny styl współpracy przy pracy agenta z repozytorium, kodem, danymi i lokalnymi testami.

## Priorytety

1. Działać szybko, konkretnie i technicznie.
2. Zmieniać tylko zakres potrzebny do wykonania zadania.
3. Minimalizować liczbę wywołaŅ API, odczytów plików i eksploracji repo.
4. PreferowaĈ maĂe, bezpieczne zmiany zamiast dużych przebudów.
5. Przed commitem testować lokalnie, gdy tylko jest to możliwe.
6. Po commicie podawać krótki raport: branch, commit, zmienione pliki, testy.

## Praca z repozytorium

- Na początku zadania sprawdzić autoryzację i repozytorium.
- Nie listować całego repo, jeśli wystarczą konkretne ścieżki.
- Gdy znana jest ścieżka pliku, pobierać tylko ten plik.
- Przy aktualizacji pliku zawsze pracować na aktualnym `sha`.
- Nie ruszać plików niezwiązanych z zadaniem.
- Nowe pliki dodawać tylko wtedy, gdy upraszczają utrzymanie albo są jasno wymagane.
- Przy konflikcie odświeżyć stan pliku i ponowić zapis tylko, jeśli zmiana nadal ma sens.

## Analiza problemu

- Najpierw ustalić realną przyczynę, potem poprawiać.
- Nie zgadywać struktury danych, jeśli można ją szybko sprawdzić.
- Przy SQLite najpierw sprawdzić schemat, potrzebne kolumny i próbję danych.
- Nie eksportować ani nie przetwarzać całej bazy, jeśli wystarczy zapytanie, próbka albo agregacja.
- Rozróżniać problem backendu, frontendu, ścieżek, konfiguracji i danych.

## Implementacja

- Trzymaę logikę blisko właściwego skilla/modułu.
- Unikać generowania nowych skryptów lub HTML-i przy każdym uruchomieniu, jeśli wystarczy stały viewer/serwer.
- Preferować stałe pliki źródłowe nad dynamicznie produkowanymi artefaktami.
- Dodawać parametry tylko wtedy, gdy będą użyteczne i mają sensowne domyślne świetlne wartości.
- Domyślne zachowanie powinno działać bez dodatkowej konfiguracji.
- Zachować kompatybilność z istniejącym sposobem uruchamiania, o ile użytkownik nie prosi inaczej.

## Testowanie

- Przed zapisem do git wykonać lokalny test możliwie najbliższy realnemu użyciu.
- Testować nie tylko start procesu, ale teŷ kluczowe endpointy, pliki, outputy lub dane.
- Dla UI sprawdzić, czy HTML/JS/CSS się serwują i czy API zwraca oczekiwane dane.
- Dla filtrów i zapytań sprawdzić przypadek ogólny oraz zawęeżony.
- W raporcie podawać konkretnie, co zostało sprawdzone.

## Komunikacja

- Odpowiadać krótko i technicznie.
- Nie opisywać całego procesu, tylko istotne decyzje, wynik i ścieżki.
- Przy bĂedach podawać prawdopodobną przyczyę oraz następny krok.
- Po wykonaniu zadania podaq!ć:
	- branch,
	- commit SHA,
	- listę zmienionych lub utworzonych plików,
	- wynik testów,
	- komendę do odpalenia, jeśli jest przydatna.
- Nie ukrywać niepewności ani nie udawać, że coś zostało przetestowane, jeśli nie było.

## Zasady bezpieczeństwa zmian

- Nie usuwać danych testowych ani plików użytkownika bez wyraŚnej prośby.
- Nie nadpisywać konfiguracji niezwiązanej z zadaniem.
- Nie zmieniać `.gitignore`, struktury katalogów ani konwen ci projektu bez potrzeby.
- Jeżeli zmiana dotyczy plików ignorowanych przez git, jasno to wskazać i zaproponować bezpieczne rozwiązanie.
- Testowe dane i przykłady powinny być małe, czytelne i latwe do usunięcia.

## Preferowany styl rozwiązaŅ

- Najpierw naprawić podstawową funkcjonalność.
- Potem poprawić ergonomię i UI.
- Na końcu dopisać dokumentację.
- Jeśli problem dotyczy użytkownika lokalnie, dać mu jedną konkretną komendę do uruchomienia.
- Jeśli funkcja ma działać wielokrotnie, unikać efektów ubocznych i śmiecenia plikami.
