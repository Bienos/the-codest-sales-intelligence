# Jakie dane zbierać i jak prognozować: sprzedaż usług IT

Stan na 28.11.2026. Oparte na tym, jak to robią firmy usługowe (software house, outsourcing, staff augmentation) i na tym, co dziś jest w naszym Pipedrive.

## 1. Najważniejsza różnica: sprzedaż to nie przychód

W firmie produktowej wygrany deal to od razu przychód. U nas wygrany deal za 120 tys. EUR to zwykle:

- start za 2–6 tygodni od podpisu (szukanie ludzi, onboarding),
- rampa (najpierw 1 osoba, po miesiącu 3),
- fakturowanie co miesiąc przez 6–12+ miesięcy,
- ryzyko, że T&M skończy się wcześniej albo przedłuży.

Dlatego dojrzałe firmy usługowe prognozują dwie liczby ([Kantata](https://www.kantata.com/blog/article/how-to-forecast-revenue-in-a-project-based-professional-services-firm), [Deltek](https://www.deltek.com/resources/articles/revenue-forecasting-models-professional-services-organizations/), [NetSuite](https://www.netsuite.com/portal/resource/articles/financial-management/revenue-forecasting-professional-services.shtml)):

| | Co to jest | Skąd |
|---|---|---|
| **Bookings** (sprzedaż) | wartość podpisanych umów w okresie | lejek sprzedażowy (to, co dziś liczy zakładka Prognoza) |
| **Revenue** (przychód) | ile zafakturujemy w danym miesiącu | **backlog** (istniejący klienci) + **ważony pipeline rozłożony w czasie** + **nowe deale z lejka**, sprawdzone z **dostępnością ludzi** |

Prognoza przychodu ma trzy warstwy, od najpewniejszej:

1. **Backlog / run-rate**: osoby, które już pracują u klientów × stawka × dni robocze do końca umowy, z prawdopodobieństwem przedłużenia. To najpewniejsza i zwykle największa część.
2. **Rozszerzenia u obecnych klientów**: otwarte role (u nas „OPEN ROLES” w Delivery Clients). Wygrywa się je częściej i szybciej niż nowych klientów.
3. **Nowi klienci**: lejek MQL → Discovery → SQL → Proposal → Commercials (to już mamy w modelu).

Na końcu: **czy mamy ludzi**. Deal bez kandydata nie zamieni się w przychód. Benchmark SPI za 2025: średnia utylizacja billable 66,4% (najniższa w historii raportu), a najlepsze firmy mają ok. 75% ([Deltek / SPI](https://www.deltek.com/resources/articles/5-kpis-from-the-spi-professional-services-maturity-benchmark-report-every-pso-must-track-for-sustainable-growth/), [Certinia](https://www.certinia.com/blog/analyzing-the-2026-spi-research-professional-services-maturity-benchmark-report/)).

## 2. Co dziś jest w Pipedrive (fakty)

- **Business Pipeline**: Marketing Nurturing → Webform MQL → Discovery → SQL → Proposal/Shortlist → Commercials/NDA → Active Delivery, plus On-Hold i Passive.
  - „Active Delivery” pełni rolę wygranej, ale deal zostaje otwarty, a Pipedrive daje mu 75% prawdopodobieństwa.
- **Delivery Clients** (pipeline 9): 25 dealów. Etapy to nazwy klientów (MUFG, ALLEGRO, OTHERS), a nie kroki procesu, plus OPEN ROLES i On hold.
  - Wartość ma tylko 8 z 25 dealów.
  - Nie wiadomo, czy 77 000 przy „QA” to kwota miesięczna, roczna czy za cały kontrakt.
  - Żaden z tych dealów nie ma daty zamknięcia.
  - **Prognoza w aplikacji w ogóle nie widzi tego pipeline’u.**
- Pola MRR, ARR i ACV istnieją, ale są puste. Pipedrive liczy je sam z produktów z cyklem rozliczeń, a produktów nie używamy.
- **Lead source** wypełniony jest w ok. 5% dealów. **Lost reason** jest w większości pusty: 336 z 370 przegranych odpadło z Passive bez powodu.
- **Etykiety mieszają dwie rzeczy**:
  - technologię (Java, Python, Vue, DevOps, Data Engineer, Android),
  - źródło (Inbound, Outband, Relation, Agency).
- Nie ma pól na:
  - datę startu,
  - czas trwania,
  - liczbę osób,
  - stawkę,
  - typ dealu,
  - model rozliczenia,
  - koniec umowy.

## 3. Jakie dane zbierać

### 3a. Na dealu: minimum, które zmienia prognozę

Najlepiej użyć **produktów Pipedrive** z cyklem rozliczeń zamiast własnych pól. Jeden produkt to jedna rola, na przykład „Senior Java Developer – miesiąc”:

- ilość = liczba osób (FTE),
- cena = stawka miesięczna,
- cykl = miesięcznie,
- liczba cykli = czas trwania w miesiącach,
- data startu rozliczeń = planowany start.

Pipedrive policzy wtedy sam MRR, ACV i wartość całkowitą, a aplikacja odczyta z tego, kiedy przychód faktycznie wpłynie.

| Pole | Typ | Po co | Wymagane od etapu |
|---|---|---|---|
| **Typ dealu**: Nowy klient / Nowa rola u klienta / Przedłużenie / Nowy projekt u klienta | lista | inne szanse i czas zamknięcia, liczenie NRR | utworzenie |
| **Źródło leada** (jest) | lista | prognoza wg źródła, koszt pozyskania | utworzenie |
| **Model rozliczenia**: T&M / Dedykowany zespół / Fixed price / Retainer | lista | fixed price rozkłada się wg kamieni milowych, T&M miesięcznie | SQL |
| **Role / FTE / stawka / start / czas trwania** | produkty (lub pola) | **kiedy i ile przychodu** | Proposal |
| **Data zamknięcia** (jest) | data | kiedy deal się rozstrzygnie | SQL |
| **Kategoria prognozy**: Pipeline / Best case / Commit | lista | ocena handlowca niezależna od etapu ([Clari](https://clari.com/blog/defining-sales-forecast-categories-to-drive-reliable-revenue)) | SQL |
| **Konkurent** (jest) | tekst | zakładka Konkurencja | Proposal |
| **Powód przegranej** | lista, wymagana | czego się uczyć | przy przegranej |
| **Technologie / kompetencje** | wielokrotny wybór | popyt wg kompetencji porównany z benchem i rekrutacją | Discovery |

Handlowca pytamy o kategorię, a nie o procent prawdopodobieństwa. Procent liczy model z historii. Ludzie słabo szacują prawdopodobieństwa, ale dobrze odpowiadają na pytanie „czy dasz za to głowę w tym kwartale” ([Forrester](https://www.forrester.com/blogs/stop-asking-salespeople-to-estimate-probabilities-in-their-forecast-process/)).

Powody przegranej dopasowane do usług IT:

- stawka za wysoka,
- wybrali innego dostawcę,
- **nie mieliśmy kandydata na czas**,
- kandydat odrzucony na rozmowie,
- projekt wstrzymany / brak budżetu,
- robią in-house,
- brak odpowiedzi,
- inne.

„Nie mieliśmy kandydata” to strata po naszej stronie, nie po stronie sprzedaży, i trzeba ją widzieć osobno.

### 3b. Kwalifikacja: MEDDICC w wersji lekkiej, 4 pola

Pełny MEDDPICC ma 8 elementów ([meddicc.com](https://meddicc.com/meddpicc), [Pipedrive](https://pipedrive.com/en/blog/meddic-sales-process)). Na nasz rozmiar wystarczą 4. Każde z nich to nie „tak/nie”, tylko krótki dowód, np. „mail od X z 12.11”:

1. **Problem i budżet potwierdzone** (Pain + Metrics): wymagane przed Proposal.
2. **Champion**, czyli osoba po stronie klienta, która nas pcha: wymagane przed Proposal.
3. **Decydent znany** (Economic buyer): wymagane przed Commercials.
4. **Proces i data decyzji** (Decision process, Paper process: zakupy, NDA, umowa ramowa): wymagane przed Commercials.

Pipedrive (plan Professional i wyżej) pozwala ustawić pola wymagane przy przejściu na dany etap. Tak etap przestaje być opinią, a staje się faktem.

### 3c. Dla otwartych ról (staff augmentation)

Pipeline Delivery Clients powinien mieć etapy procesu, a klienta zapisanego w polu Organizacja:

**Zapotrzebowanie → Profile wysłane → Rozmowy → Zaakceptowany → Start**

Mierzymy wtedy:

- czas do wysłania pierwszego profilu,
- ile profili przypada na jedną rozmowę i ile rozmów na jedną akceptację,
- odsetek obsadzonych ról.

Te liczby mówią więcej o przyszłym przychodzie z obecnych klientów niż cokolwiek innego.

### 3d. Na kliencie (organizacji)

| Pole | Po co |
|---|---|
| Status: aktywny / były / prospekt | segmentacja |
| Osoby u klienta teraz (FTE) i średnia stawka | backlog, run-rate |
| Koniec umowy / zamówienia, data przeglądu | przedłużenia, czyli alarm 90 dni przed końcem |
| Przychód ostatnich 12 mies. (z księgowości) | NRR, GRR, koncentracja |

Kluczowe wskaźniki na poziomie klientów ([McKinsey](https://www.mckinsey.com/industries/technology-media-and-telecommunications/our-insights/the-net-revenue-retention-advantage-driving-success-in-b2b-tech)):

- **NRR** (net revenue retention): przychód od tych samych klientów rok do roku, z rozszerzeniami.
- **GRR** (gross revenue retention): to samo, ale bez rozszerzeń; pokazuje, ile tracimy.
- **Koncentracja**: jaki udział w przychodzie mają MUFG i Allegro. Utrata jednego klienta to największe ryzyko prognozy.

## 4. Jak prognozować: rytm pracy

- **Co tydzień (poniedziałek, 30 min)**:
  - tylko dealy w kategoriach Commit i Best case oraz to, co zmieniło się od zeszłego tygodnia;
  - porównujemy liczbę z modelu z sumą Commit handlowców. Duża różnica to temat do rozmowy, a nie błąd.
- **Co miesiąc**:
  - prognoza z początku miesiąca kontra to, co się wydarzyło (aplikacja loguje prognozę codziennie, więc to porównanie jest automatyczne);
  - przegląd kończących się umów.
- **Co kwartał**:
  - przegląd założeń (konwersje, czas na etapie, średnia wartość) i powodów przegranych;
  - porządek z Passive: dealy starsze niż 90 dni zamykamy z powodem „brak odpowiedzi”, żeby nie zaśmiecały statystyk.

Podpisaną umowę oznaczamy w Pipedrive jako **Won**. Realizacja idzie do Delivery Clients, a w Business Pipeline nie trzymamy dealu otwartego w „Active Delivery”.

## 5. Co zmienić w aplikacji (propozycja, w kolejności)

1. **Prognoza przychodu miesiąc po miesiącu**, nie tylko sprzedaży:
   - wykres warstwowy: backlog (obecni klienci), rozszerzenia, nowi klienci;
   - timing z produktów, a gdy ich brak: data zamknięcia + zmierzony czas do startu + typowy czas trwania.
2. **Włączenie Delivery Clients**: backlog i otwarte role, z osobnym lejkiem dla ról.
3. **Kategoria prognozy**: obok liczby modelu suma Commit / Best case handlowców i lista dealów, gdzie model i handlowiec się nie zgadzają.
4. **Strona jakości danych**: per handlowiec i per deal, czego brakuje (wartość, data, źródło, powód przegranej). Dziś wartość ma tylko 1 z 27 aktywnych dealów w EUR.
5. **Alarm przedłużeń**: umowy kończące się w ciągu 90 dni.
6. **Polecenie w aplikacji, które samo zakłada pola w Pipedrive** (typ dealu, model rozliczenia, kategoria prognozy, powody przegranej, 4 pola kwalifikacji) przez API, tym samym tokenem.

## Źródła

- Prognozowanie przychodu w firmach usługowych:
  - [Kantata](https://www.kantata.com/blog/article/how-to-forecast-revenue-in-a-project-based-professional-services-firm)
  - [Deltek](https://www.deltek.com/resources/articles/revenue-forecasting-models-professional-services-organizations/)
  - [NetSuite](https://www.netsuite.com/portal/resource/articles/financial-management/revenue-forecasting-professional-services.shtml)
  - [Forrester o usługach technologicznych 2025](https://www.forrester.com/blogs/the-ai-effect-what-q2-2025-technology-services-earnings-mean-for-technology-executives)
- Kategorie prognozy:
  - [Forrester](https://www.forrester.com/blogs/stop-asking-salespeople-to-estimate-probabilities-in-their-forecast-process/)
  - [Clari](https://clari.com/blog/defining-sales-forecast-categories-to-drive-reliable-revenue)
  - [Rework](https://resources.rework.com/libraries/pipeline-management/forecast-categories)
- MEDDICC / MEDDPICC:
  - [meddicc.com](https://meddicc.com/meddpicc)
  - [Pipedrive](https://pipedrive.com/en/blog/meddic-sales-process)
  - [Umbrex](https://umbrex.com/?p=242683)
  - [Siemens](https://blogs.sw.siemens.com/partners/expose-meddpicc-qualification-gaps/)
- Benchmarki SPI:
  - [Deltek](https://www.deltek.com/resources/articles/5-kpis-from-the-spi-professional-services-maturity-benchmark-report-every-pso-must-track-for-sustainable-growth/)
  - [Rocketlane](https://www.rocketlane.com/blogs/professional-services-maturity-index-2026)
  - [Certinia](https://www.certinia.com/blog/analyzing-the-2026-spi-research-professional-services-maturity-benchmark-report/)
- Retencja przychodu:
  - [McKinsey](https://www.mckinsey.com/industries/technology-media-and-telecommunications/our-insights/the-net-revenue-retention-advantage-driving-success-in-b2b-tech)
  - [Certinia](https://certinia.com/blog/new-public-market-pulse-report-shows-impact-of-customer-success-and-net-revenue-retention-on-enterprise-psos/)

## 6. Co już działa w aplikacji (28.11.2026)

- **Pola w Pipedrive**:
  - Przy pierwszej godzinnej synchronizacji aplikacja zakłada brakujące pola dealu: Deal type, Billing model, Forecast category, Planned start, Duration (months), People (FTE), Monthly value, cztery pola Qualification i Skills.
  - Zakłada też pola organizacji: Client status, Contract end, People at client (FTE).
  - Pole o tej samej nazwie, które już istnieje, jest używane zamiast tworzenia nowego.
  - Później tylko je odczytuje: pola usuniętego celowo nie odtworzy. Wyjątkiem jest przycisk „Załóż pola w Pipedrive” (dla managerów), który tworzy brakujące pola ponownie.
  - Kod: `backend/src/ri/crm/fields.py`.
- **Produkty dealu** z cyklem rozliczeń (`/deals/products`): z nich brana jest stawka miesięczna, start, liczba miesięcy i liczba osób.
- **Prognoza → „Przychód miesięcznie”**: przychód fakturowany od bieżącego miesiąca do grudnia roku prognozy, w pięciu warstwach:
  - obecne umowy,
  - przedłużenia,
  - otwarte role u klientów,
  - otwarte deale sprzedażowe,
  - deale jeszcze nieutworzone.

  Pod wykresem: umowy kończące się w 90 dni, otwarte role, lista tego, co fakturujemy teraz, oraz udział największego klienta. Kod: `forecast/revenue.py`.
- **Delivery Clients**:
  - Pipeline jest szukany po nazwie.
  - Etapy „OPEN ROLES” to rozszerzenia; „On hold” nie jest liczony.
  - Wartość dealu domyślnie oznacza całą umowę; w Założeniach można zmienić na „jeden miesiąc”.
  - Podpisany deal sprzedażowy klienta prowadzonego w Delivery Clients nie jest liczony drugi raz.
- **„Kategorie prognozy”**: suma Commit / Best case / Pipeline obok szansy z modelu oraz lista rozbieżności (Commit z niską szansą; późny etap z wysoką szansą bez Commit).
- **„Jakość danych”**:
  - wypełnienie pól wymaganych od danego etapu, liczone dla handlowca i dla każdego dealu;
  - deale, które utknęły;
  - przegrane bez powodu z ostatnich 90 dni.
- **Rzeczy do zrobienia ręcznie w Pipedrive**, bo API ich nie obsługuje:
  - lista powodów przegranej;
  - pola wymagane per etap;
  - przebudowa etapów Delivery Clients na proces.
