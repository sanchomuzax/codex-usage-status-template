# Quota-aware orchestration prompt

Paste the block below into an orchestrator agent's instructions (or a project
`AGENTS.md`) to make it budget its own subagent launches.

It depends on `budget_check.py` from this repo, which returns a verdict and an
exit code (`0` GO, `1` CAUTION, `2` STOP, `3` UNKNOWN).

---

## Kvótatudatos munkavégzés

Ez a gép figyeli a Codex-előfizetés terhelését. Mielőtt nagy feladatba kezdesz,
kérdezd meg a keret állását, és igazodj hozzá. A parancs nem kerül tokenbe és egy
másodperc alatt lefut:

```sh
python3 ~/codex-usage-status/budget_check.py --brief
```

Példa válasz a jelenlegi, weekly-only Codex állapotban:
`CAUTION | live | session — (not exposed) | weekly 31% | burn 1.6x (…)`

### Az 5 órás session ablak jelenleg hiányozhat

A Codex jelenleg sok fióknál csak a 7 napos weekly ablakot közli; az 5 órás
session mező ilyenkor `null`. Ez **nem 0%**, és nem lekérdezési hiba. A
`budget_check.py --brief` ezt `session — (not exposed)` formában jelzi, és a
döntést a valóban elérhető weekly értékből és annak burn rate-jéből hozza meg.
Ha OpenAI később ismét visszaadja a 300 perces ablakot, a monitor és ez a
policy automatikusan figyelembe veszi.

### Ellenőrizd, hogy a verdikt a TE fiókodról szól-e

**Ha tudod, melyik előfizetést költöd, add meg:**

```bash
python3 ~/codex-usage-status/budget_check.py --account <fiók> --brief
```

(vagy állítsd be egyszer a `CODEX_USAGE_EXPECT_ACCOUNT` környezeti változót.)

Ha a mérés nem arról a fiókról szól, a válasz **UNKNOWN**, 3-as kilépési kóddal,
és számokat sem ad — mert a másik fiók számai semmit nem mondanak a tiédről.
Ilyenkor ne indíts nagy munkát a monitorra hivatkozva; nézd meg a keretet ott,
ahol az a fiók be van jelentkezve, vagy kérdezd meg a felhasználót.

Ha nem adsz meg fiókot, minden marad a régiben — egyetlen fiókkal dolgozva
nincs is mit megkülönböztetni.

A kimenet `acct <név>` formában mindig megmondja, melyik előfizetésről szól. A
gyűjtő azt a fiókot olvassa, amelyikre a `~/.codex/auth.json` épp mutat — ez nem
feltétlenül az, amelyikben te dolgozol:

* a Codex **desktop app** saját munkamenetet tart a `~/.config/Codex` alatt, és
  azt egy fiókváltó nem írja át;
* egy **fiókváltó** menet közben átállíthatja a CLI hitelesítését alattad.

Ilyenkor a verdikt egy másik előfizetés keretéről beszél, és a kettő
megkülönböztethetetlen, ha nem nézed meg a nevet. **Ha az `acct` nem az a fiók,
amelyikben dolgozol, a szám nem rád vonatkozik** — akkor se GO-nak, se STOP-nak
ne vedd, hanem kérj emberi megerősítést.

### Mindig friss adatból dolgozz

A parancs **kétféle forrásból** tud dolgozni, és a válaszban mindig kiírja, melyikből:

- `live` — élő lekérdezés a szervertől. Ez a jó eset, nincs teendőd.
- `CACHED ... m old` — nincs élő hozzáférés (jellemzően ha nem azon a gépen
  futsz, ahol a bejelentkezés van), ezért a repóba commitolt pillanatképet
  olvassa.

**Ha `CACHED`-et látsz, előbb frissíts, és csak utána dönts:**

```sh
git -C ~/codex-usage-status pull --quiet
python3 ~/codex-usage-status/budget_check.py --brief
```

A pillanatkép ötpercenként frissül a forrásgépen és felkerül a repóba, tehát egy
`git pull` szinte mindig friss adatot hoz. Ha a `git pull` után is `CACHED`
marad, az rendben van — a lényeg, hogy ne órákkal korábbi számból dolgozz.

A parancs magától is véd: ha egy korábban ténylegesen közölt 5 órás ablak
pillanatképe időközben lejárt, nem mondja meg a régi százalékot, hanem
`session unknown (stale)` jelzést ad, és sosem enged GO-t ilyenkor. Ez más eset,
mint a jelenlegi `session — (not exposed)`. Ha a pillanatkép 90 percnél régebbi, a verdikt
`UNKNOWN` lesz. **Ne értelmezd a régi számot friss adatként** — ha bizonytalan
vagy, kérdezd meg a felhasználót, mit mutat nála a `/usage`.

### Mikor ellenőrizd

- **Minden munkamenet elején**, mielőtt tervet készítesz.
- **Minden subagent-indítás előtt**, különösen ha többet indítanál párhuzamosan.
- **Fázisok között** (pl. felderítés → implementáció → tesztelés → review).
- **Ha egy subagent hibával tér vissza** — lásd lentebb, ez a legfontosabb pont.

### Mit jelentenek a verdiktek

**GO** — Nincs korlátozás. Indíthatsz párhuzamos subagenteket, futtathatsz teljes
repó-átvizsgálást, mehet a szokásos több-ágenses munkafolyamat.

**CAUTION** — Van keret, de fogy. Ilyenkor:
- Ne indíts párhuzamos fan-outot; egyszerre egy subagent dolgozzon.
- Szűkítsd a hatókört: konkrét fájlok a teljes repó helyett, célzott grep a
  mindent-beolvasás helyett.
- Mechanikus munkára (formázás, átnevezés, egyszerű teszt-scaffold) válassz
  olcsóbb modellt.
- Ami már fut, azt fejezd be — ne szakítsd félbe pánikszerűen.
- Halaszd a ráérős dolgokat (nagy refaktor, dokumentáció-átírás, teljes
  tesztlefedettség-emelés) a keret nullázódása utánra.

**STOP** — Ne kezdj új munkát. Ehelyett:
1. Hozd az éppen futó változtatást **konzisztens állapotba** — félbehagyott
   refaktor, importálatlan új fájl, felében átírt teszt ne maradjon.
2. Commitold a munkát egy WIP branchre beszédes üzenettel.
3. Írj egy rövid átadó összefoglalót: mi készült el, mi maradt hátra, mi a
   következő lépés.
4. Mondd meg a felhasználónak, mikor nullázódik a keret (a parancs kiírja), és
   állj meg. Ne indíts új subagentet, ne kezdj új fájlt.

**UNKNOWN** — A kvóta nem volt lekérdezhető, vagy a pillanatkép túl régi.
Először próbáld a fenti `git pull`-t. Ha utána is UNKNOWN, dolgozz CAUTION
szabályok szerint, és mondd meg a felhasználónak, hogy a keretfigyelés épp nem
lát — ilyenkor ő tud pontos számot adni a `/usage` parancsból.

### Ha egy subagent hibával tér vissza — FONTOS

Rate limit hiba **nem kódhiba**. Ha egy subagent elszáll, mielőtt bármit
javítanál, futtasd le a `budget_check.py`-t.

- Ha a verdikt **STOP**, vagy a hibaüzenetben `rate limit`, `usage limit`, `429`
  vagy `quota` szerepel: a kód valószínűleg **rendben van**. Ne írd át a
  forrást, ne lazíts a teszteken, ne kezdj újrapróbálkozási ciklusba — azzal
  csak tovább égeted a keretet, és elrontasz működő kódot egy nem létező hiba
  miatt. Ehelyett: checkpointolj a fenti STOP eljárás szerint, és szólj.
- Csak akkor kezdj hibakeresésbe, ha a keret rendben van, tehát a hiba tényleg a
  kódból jön.

### Menet közbeni megszakítás

Hosszú futásnál a keret elfogyhat menet közben. Ez megengedett, sőt elvárt:
**jobb rendezetten megállni, mint hibára futni.** Ha egy fázis végén STOP-ot
kapsz, ne kezdd el a következő fázist — zárd le tisztán a fentiek szerint. A
felhasználó a nullázódás után folytatni tudja onnan, ahol abbahagytad.

### Profil-címke a kimenetben

Ha a válaszban látsz egy `[conserve]` vagy `[greedy]` címkét, az a felhasználó
által beállított **költési politika**, nem az, hogy a fiók kimerülőben van. A
`conserve` szándékosan korán mond STOP-ot (a felhasználó tartalékot akar hagyni),
a `greedy` szándékosan sokáig enged. Címke nélkül a kiegyensúlyozott alapértelmezés
fut. A verdiktet mindig kövesd — a címke csak azt magyarázza, miért ott a küszöb.

Ne kérdezz rá minden ellenőrzés eredményére; csak akkor jelezz, ha a verdikt
CAUTION-re vagy STOP-ra vált, vagy ha emiatt megváltoztatod a tervet.
