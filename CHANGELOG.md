# Versiegeschiedenis

Alle wijzigingen aan AliExpress Coins staan hier, nieuwste bovenaan.
Versienummers volgen [semver](https://semver.org/lang/nl/): `MAJOR.MINOR.PATCH`.
Het versienummer staat op één plek: `app/version.py`.

## [0.1.1] - 2026-11-16

Na de eerste run met een ingelogd account.

### Opgelost
- **Check-in-reeks bleef leeg.** De reeks komt nu uit de check-in-kalender van AliExpress (`coin.channel.sign.list`). Lukt dat niet, dan uit de tekst "N days streak" op de pagina. Die kalender zegt ook of er vandaag al is ingecheckt, zodat dat niet meer alleen van de paginatekst afhangt.
- **Website-check-in wachtte 2 minuten op een knop die er niet is.** Toont de PC-pagina alleen "Earn more coins on app" met QR-codes, dan heeft je account op de website geen check-in. De run stopt dan na een paar seconden, met een duidelijke uitleg in het paneel. De check-in van de app doet de container al.
- **Lege captcha in de loginbrowser.** De loginbrowser had geen WebGL (er is geen GPU in de container). Het script achter de captcha gebruikt dat, en zonder WebGL kon de captcha leeg blijven. De loginbrowser gebruikt nu SwiftShader (`--use-angle=swiftshader`). Blijft het venster toch wit, dan kan je netwerk `ynuf.aliapp.org` blokkeren (een captcha-server van Alibaba). Zie README.

### Gewijzigd
- In `api/last_run.json` staan de takenlijst en de check-in-kalender nu volledig, in plaats van afgekapt na 2500 tekens.
- Bij "Vandaag al ingecheckt" wordt nu ook een screenshot van de mobiele pagina gemaakt.

## [0.1.0] - 2026-11-15

Eerste versie.

### Toegevoegd
- **Mobiele check-in**: de dagelijkse check-in van de app (`m.aliexpress.com/p/coin-index`), met telefoon-emulatie (Pixel 7). Pas geslaagd als AliExpress het zelf bevestigt (`sign.execute`).
- **Website check-in**: de aparte check-in op `www.aliexpress.com/p/coin-pc-index` ([mouad73/AliexpressCollecteCoins#2](https://github.com/mouad73/AliexpressCollecteCoins/issues/2)). Toont de pagina alleen een QR-code, dan meldt het paneel "niet beschikbaar".
- **Taken** die alleen een pagina openen of bekijken, gestart vanaf de coin-pagina zelf. Na elke taak wordt de lijst opnieuw geladen. Elke taak telt pas als AliExpress hem meetelt; na twee mislukte pogingen wordt hij voor die run overgeslagen.
- **Dagelijkse quiz**, die van een fout antwoord leert (`/data/quiz_answers.json`).
- **Bewust niet**: advertentietaken, spellen, bestellingen en captcha's.
- **Webpaneel** (poort 8080): saldo, reeks, coins uit taken, stappen en taken van de laatste run, screenshots, geschiedenis, het volledige runlog en de versie.
- **Inloggen** via een gewone Chromium in de container (noVNC in je browser), of door cookies uit je eigen browser te plakken (Cookie-Editor-JSON, `cookies.txt`, Cookie-header). Het paneel toont hoelang de login geldig is.
- **Schema** (`SCHEDULE`, standaard 10:30 en 20:30) met willekeurige vertraging. Runs doen alleen wat nog openstaat.
- **Meldingen** via Apprise (`NOTIFY_URLS`, `NOTIFY_LEVEL`).
- **Basic-auth** voor het paneel (`PANEL_PASSWORD`). `/health` blijft open voor de healthcheck.
- **API** voor Home Assistant: `/api/status`, `/api/run`, `/api/last_run.json`.
- **Image op GitHub**: `ghcr.io/terrorsource/aliexpresscoins` voor amd64 en arm64. CI draait eerst de tests, pip-audit en een Trivy-scan.

### Bekend
- Getest zonder ingelogd account: de herkenning van een verlopen login op de mobiele en de PC-pagina, de loginbrowser, de cookie-import en het paneel. Het tikken op de check-in-knoppen, de taken en de quiz zijn nog niet getest met een ingelogd account.
