# AliExpress Coins – Docker

Haalt elke dag automatisch zoveel mogelijk AliExpress-coins op, vanuit een echte Chromium in een
Docker-container. Je bedient alles via een webpaneel: status, handmatig starten, inloggen en
screenshots.

> **Onofficieel.** Dit project heeft niets met AliExpress te maken. Het automatiseert pagina's in je
> eigen ingelogde account. Automatisering kan tegen de voorwaarden van AliExpress ingaan, en
> AliExpress kan de pagina's op elk moment veranderen. Gebruik is op eigen risico.

## Wat het doet

Elke run doet, in deze volgorde:

| Stap | Wat | Coins (indicatie) |
| --- | --- | --- |
| **Mobiele check-in** | De dagelijkse check-in van de app (`m.aliexpress.com/p/coin-index`), met telefoon-emulatie (Pixel 7). | 10 → 50 per dag, oplopend met je reeks |
| **Taken** | De taken onder "Earn more coins" die alleen een pagina openen of bekijken: Super discounts (3×), Recently viewed, Coins Savings Recap, Search, Coupons & credits, de +1 check-in-bonus. Na elke taak wordt de lijst opnieuw geladen, want een afgeronde taak ontgrendelt vaak een nieuwe. | ± 30–45 per dag |
| **Dagelijkse quiz** | Beantwoordt de quiz. Een fout antwoord wordt onthouden, zodat de volgende keer een ander antwoord gekozen wordt. | 1–5 |
| **Water to win** | Opent Prize Land en tikt één keer op "Water". Koopt nooit iets en kiest nooit een prijs. | 5 |
| **Coins shortcut** | Opent de link die de snelkoppeling van de app opent. | 5 |
| **Website check-in** | De aparte check-in op de PC-site (`www.aliexpress.com/p/coin-pc-index`). Dit is de wens uit [issue #2](https://github.com/mouad73/AliexpressCollecteCoins/issues/2), maar dan andersom: de app-check-in en de website-check-in zijn twee losse beloningen, en deze container doet ze allebei. Bij sommige accounts en regio's toont de PC-pagina alleen een QR-code. Dan meldt het paneel "niet beschikbaar". | wisselt |

Elke stap telt pas als geslaagd als AliExpress het zelf bevestigt: het API-antwoord van de
pagina (`sign.execute`, `task.process.complete`), of de pagina die laat zien dat vandaag al is
ingecheckt. Runs zijn idempotent: een tweede run op dezelfde dag doet alleen wat nog openstaat.

### Wat het bewust níet doet

- **Advertentietaken** ("Explore sponsored items", "Browse surprise items"). Een bot die
  advertenties bekijkt of aanklikt is nepverkeer waar adverteerders voor betalen. Dat zijn ± 20
  coins per dag die je zelf kunt doen via de loginbrowser in het paneel.
- **Spellen en bestellingen** (Merge Boss, "Items at $0.1", taken die een aankoop vragen). Die
  werken alleen in de app of kosten geld. Alleen het water geven in Prize Land doet de container wel.
- **Delen met vrienden, widget, meldingen.** Delen telt pas als vrienden je link openen. Widget en
  meldingen zijn eenmalig en werken alleen in de app op je telefoon (samen +25 coins).
- **Captcha's oplossen of je wachtwoord opslaan.** Je logt één keer zelf in. De container bewaart
  alleen de browsersessie (cookies) in `./data`.

## Starten

Je hebt Docker met Compose nodig. Het image `ghcr.io/terrorsource/aliexpresscoins` is gebouwd op
Microsofts Playwright-image (± 2 GB) en werkt op amd64 en arm64 (Raspberry Pi 4/5, Apple Silicon,
de meeste NAS'en). Tags: `latest` (laatste stand van main), `X.Y.Z` en `X.Y` (releases, zie
[CHANGELOG.md](CHANGELOG.md)).

```yaml
services:
  aliexpress-coins:
    container_name: aliexpress-coins
    image: ghcr.io/terrorsource/aliexpresscoins:latest
    init: true
    restart: unless-stopped
    network_mode: bridge
    shm_size: 1gb                         # Chromium crashes with Docker's default 64 MB
    environment:
      - TZ=Europe/Amsterdam               # otherwise times are shown in UTC
      - SCHEDULE=10:30,20:30              # check-in resets around 09:00 Dutch time
      - PANEL_USER=admin
      - PANEL_PASSWORD=                   # set this: whoever reaches the panel can use your AliExpress session
      - NOTIFY_URLS=                      # optional, Apprise URLs, e.g. ntfy://ntfy.sh/my-topic
      - NOTIFY_LEVEL=errors               # errors | summary | none
      # All other settings: see .env.example
    ports:
      - 8098:8080
    volumes:
      - /mnt/ssd/docker/aliexpress-coins/data:/data
```

Dit staat ook in [docker-compose.yml](docker-compose.yml). Pas het pad van de datamap en de poort aan
je eigen systeem aan, en start met:

```bash
docker compose up -d
```

Open daarna `http://<host>:8098`. Bijwerken doe je met `docker compose pull && docker compose up -d`.

**Zet `PANEL_PASSWORD`.** Wie bij het paneel kan, kan je ingelogde AliExpress-sessie gebruiken.

**Privé-repo?** Dan is het image op ghcr.io ook privé. Log op de NAS eenmalig in met
`docker login ghcr.io` (gebruikersnaam: je GitHub-naam; wachtwoord: een token met `read:packages`),
of zet het package op GitHub op *Public*.

**Zelf bouwen** (voor ontwikkeling): `cp .env.example .env` en daarna
`docker compose -f docker-compose.dev.yml up -d --build`. Het paneel staat dan alleen op
http://localhost:8080 open.

## Inloggen (eenmalig)

**Optie A – loginbrowser in het paneel (aangeraden).** Klik op *Browser openen om in te loggen*. Er
opent een tabblad met een gewone Chromium uit de container (via noVNC). Log in zoals je gewend
bent, inclusief captcha en verificatiecode. Klik daarna in het paneel op *Browser sluiten en
check-in testen*.

**Optie B – cookies uit je eigen browser.** Log in op aliexpress.com in je eigen browser. Exporteer
de cookies met de extensie [Cookie-Editor](https://cookie-editor.com) (*Export → JSON*), of kopieer
in DevTools → Network de `Cookie`-header van een aliexpress.com-verzoek. Plak dat in het paneel
onder *Of: cookies uit je eigen browser plakken*. Een `cookies.txt` werkt ook.

Het paneel toont tot wanneer de login geldig is (meestal ongeveer een jaar). Verloopt de login
eerder, bijvoorbeeld na een wachtwoordwijziging, dan krijg je een melding (als je meldingen hebt
ingesteld) en staat het paneel op *Login nodig*.

## Instellingen

Zet ze onder `environment:` in je compose-bestand, of in `.env` bij de dev-compose.

| Variabele | Standaard | Uitleg |
| --- | --- | --- |
| `TZ` | `Europe/Amsterdam` | Tijdzone voor het schema. |
| `SCHEDULE` | `10:30,20:30` | Tijden voor de automatische runs. De check-in reset rond 09:00 Nederlandse tijd (middernacht Pacific-tijd). |
| `JITTER_MINUTES` | `20` | Willekeurige vertraging per run. |
| `RUN_ON_START` | `true` | Bij het opstarten meteen een run als vandaag nog niet is ingecheckt. |
| `MOBILE_CHECKIN` / `PC_CHECKIN` / `TASKS` / `QUIZ` | `true` | Onderdelen aan of uit zetten. |
| `PRIZE_LAND_WATER` / `SHORTCUT_TASK` | `true` | De taken "Water to win" en "Visit the page via coins shortcut" aan of uit. |
| `TASK_BUDGET_MINUTES` | `30` | Maximale tijd voor de taken per run. |
| `TASK_BLOCKLIST` | leeg | Regex: taken met een titel die hierop lijkt worden overgeslagen. |
| `EXTRA_TASK_TYPES` | leeg | Voor gevorderden: extra tasktypes (getallen uit het runlog) die als "pagina bekijken" gelden. |
| `COIN_REGION` | leeg | Ship-to-land alleen in de containerbrowser, bijvoorbeeld `KR`. Sommige projecten (zoals mouad73) zetten Korea voor meer coins. Dat is niet geverifieerd, en het verandert ook de regio waarvoor je coins gelden. Leeg = de instelling van je account. |
| `FORCE_ENGLISH` | `true` | Toont de AliExpress-pagina's in het Engels, zodat de knoppen betrouwbaar herkend worden. Dit geldt alleen voor de containerbrowser. |
| `NOTIFY_URLS` | leeg | [Apprise](https://github.com/caronc/apprise/wiki)-URL's, komma-gescheiden: `ntfy://…`, `tgram://…`, `hassio://…`, `discord://…`, `mailto://…`. |
| `NOTIFY_LEVEL` | `errors` | `errors` = alleen problemen · `summary` = na elke run een samenvatting · `none`. |
| `PANEL_USER` / `PANEL_PASSWORD` | `admin` / leeg | Basic-auth voor het paneel. |

Herstart na een wijziging met `docker compose up -d`.

## API (bijvoorbeeld voor Home Assistant)

| Endpoint | |
| --- | --- |
| `GET /api/status` | Status als JSON: `version`, `state`, `balance`, `streak`, `task_coins_today`, `login_expires`, `last_run`, `history`. |
| `POST /api/run` | Start een run. `?only=checkin` of `?only=tasks` voor een deel. Geeft `409` als er al een run loopt. |
| `GET /api/last_run.json` | Volledig log van de laatste run: paginatekst en AliExpress-API-antwoorden. |

Voorbeeld van een REST-sensor in Home Assistant:

```yaml
rest:
  - resource: http://<host>:8098/api/status
    scan_interval: 600
    sensor:
      - name: AliExpress coins
        value_template: "{{ value_json.balance }}"
        unit_of_measurement: coins
        json_attributes: [state, streak, task_coins_today, login_expires]
```

## Problemen oplossen

- **Paneel → Screenshots** toont de mobiele pagina, de takenlijst en de PC-pagina van de laatste run.
- **Captcha blijft wit bij het inloggen**: sluit het venster en probeer het opnieuw. Blijft het wit, kijk
  dan of je netwerk (firewall, Pi-hole, AdGuard, geo-blokkade) `ynuf.aliapp.org` blokkeert. Dat is een
  captcha-server van Alibaba in China. Of sla de captcha over en plak je cookies in het paneel (optie B).
- **Website check-in "niet beschikbaar"**: toont de PC-pagina alleen "Earn more coins on app" met
  QR-codes, dan heeft je account geen check-in op de website. Dat is normaal (bijvoorbeeld in Nederland).
  De check-in van de app doet de container wel. Wil je de stap overslaan, zet dan `PC_CHECKIN=false`.
- **`api/last_run.json`** bevat de paginatekst en elk AliExpress-API-antwoord. Verandert AliExpress
  iets, dan is daar meestal te zien wat.
- **"Knop niet gevonden" of "Niet bevestigd"**: AliExpress heeft waarschijnlijk de pagina veranderd.
  Kijk naar de screenshot en het runlog.
- **Taak "Niet gelukt: geopend, maar AliExpress telde het niet"**: die taak werkt waarschijnlijk
  alleen in de app. Na twee pogingen wordt hij voor die run overgeslagen.
- **Meerdere accounts**: start per account een tweede container met een eigen `data`-map en poort.

## Gebaseerd op

Ideeën en lessen uit [mmzanes/ha-aliexpress-coins](https://github.com/mmzanes/ha-aliexpress-coins)
(echte Chromium, noVNC-login, taken via de takenlijst-API), [Prismaria/AliExpress-Coin-Harvester](https://github.com/Prismaria/AliExpress-Coin-Harvester)
(takencatalogus, quiz), [1khvled/aliexpress-coin-bot](https://github.com/1khvled/aliexpress-coin-bot)
(taak-URL's direct openen levert niets op; saldo uit de API), [yeonkyun/aliexpress-coin-claimer](https://github.com/yeonkyun/aliexpress-coin-claimer)
(PC-check-in, resettijd) en [mouad73/AliexpressCollecteCoins](https://github.com/mouad73/AliexpressCollecteCoins)
plus [issue #2](https://github.com/mouad73/AliexpressCollecteCoins/issues/2) (app- én website-check-in).
