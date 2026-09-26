# gtm-engine (Open Source)

<p align="center">
  <strong>Sprache / Language:</strong>
  <a href="README.md">English</a> |
  <a href="README.zh-CN.md">简体中文</a> |
  <a href="README.ja.md">日本語</a> |
  <a href="README.es.md">Español</a> |
  <strong>Deutsch</strong> |
  <a href="README.ko.md">한국어</a>
</p>

<p align="center">
  <a href="https://github.com/henryroxstar/gtm-engine/stargazers"><img src="https://img.shields.io/github/stars/henryroxstar/gtm-engine?style=flat&label=Stars" alt="Stars" /></a>
  <a href="LICENSE"><img src="https://img.shields.io/badge/License-Apache_2.0-blue.svg" alt="License: Apache 2.0" /></a>
  <a href="https://www.python.org/"><img src="https://img.shields.io/badge/python-3.11+-3776AB.svg" alt="Python 3.11+" /></a>
  <a href="https://docs.anthropic.com/en/api/agent-sdk/overview"><img src="https://img.shields.io/badge/built%20with-Claude%20Agent%20SDK-d97757.svg" alt="Built with Claude Agent SDK" /></a>
  <a href="https://modelcontextprotocol.io/"><img src="https://img.shields.io/badge/connectivity-MCP--first-6E56CF.svg" alt="MCP-first" /></a>
  <a href="#warum-das-system-so-aufgebaut-ist"><img src="https://img.shields.io/badge/human%20gates-verbindliche%20Freigaben-2ea44f.svg" alt="Human Gates" /></a>
  <a href="#unterstützte-workspaces-und-umgebungen"><img src="https://img.shields.io/badge/workspaces-Claude%20%7C%20Antigravity%20%7C%20Cursor%20%7C%20Codex-orange.svg" alt="Harness Support" /></a>
</p>

![GTM Content OS & Outbound-Pipeline](docs/assets/gtm-pipeline-flow.png)

*Das Open-Source-Agent-Harness für B2B-Software-Startups. Entwickelt, um Vertrieb, Pre-Sales und Marketing in der Frühphase um das Zehnfache zu beschleunigen.*

```
      .-"""-.
     /  o o  \        Ein Gehirn, viele umsichtige Hände —
     \   ^   /        Jeder Kontakt und Post erfordert deine Freigabe
      )-----(
     / /| |\ \
    ( ( | | ) )
     \_/ | \_/
        `-`
```

### Du bist der Gründer. Du bist auch das gesamte Go-To-Market (GTM)-Team.

In der Frühphase eines Startups trägst du jeden Hut, arbeitest mit knappen Ressourcen und suchst unermüdlich nach dem Product-Market-Fit (PMF). Deals abzuschließen war nie die einzige Aufgabe: Du musst die Vertriebspipeline eigenhändig aufbauen, das ungefilterte Feedback der Kunden zurück in die Produktentwicklung tragen, bei Kundenterminen ein noch nicht fertiges Produkt überzeugend präsentieren (ohne jedes Mal einen Entwickler hinzuziehen zu müssen) und Woche für Woche neue Botschaften testen, um Zielkunden anzuziehen.

Das sind fünf Vollzeitstellen. Der klassische Rat lautet: "Stell fünf Leute ein." Doch alles, was du hast, sind dein Laptop, dein bestehendes KI-Abonnement (Claude Desktop, Google Antigravity, Cursor oder Codex) und deine Zeit.

**gtm-engine ist das System, das diese fünf Aufgaben gemeinsam mit dir meistert.** Gezielte Kaltakquise, Vorbereitung auf Kundengespräche, strategische Account-Pläne, Pitch-Präsentationen, wöchentliche Marktradar-Analysen und markenkonformer Multichannel-Content (LinkedIn-Beiträge, Fachartikel, Podcast-Skripte, Infografiken) — alles gesteuert durch einen einzigen Satz im Chat, in deiner unverwechselbaren Markensprache und fundiert auf deiner realen Unternehmenswissensbasis.

Entscheidend ist: **Das System ist architektonisch so konstruiert, dass der Agent strukturell unfähig ist, eigenmächtig E-Mails zu versenden, Beiträge zu veröffentlichen oder sensible Daten nach außen zu leiten.** Es verlangt kein blindes Vertrauen, sondern schließt Überschreitungen technisch aus.

Drei unverrückbare Prinzipien:
1. **Kein Versand und keine Veröffentlichung ohne deine explizite Freigabe.**
2. **Die Daten jedes Unternehmens bleiben strikt getrennt im eigenen Profilverzeichnis.**
3. **Der Agent besitzt keine unkontrollierten Raw-HTTP- oder Shell-Rechte.** Sämtliche Interaktionen laufen ausschließlich über abgesicherte MCP-Werkzeuge (Model Context Protocol).

---

### Warum GTM Engine? (Architekturvergleich)

| Kriterium | Intransparente "AI SDR"-Plattformen (11x, Artisan etc.) | Reine Prompt-Chats (ChatGPT / Claude) | Generische Agent-Frameworks (CrewAI / LangChain) | **GTM Engine (Dieses System)** |
|---|---|---|---|---|
| **Kosten** | Abo pro Nutzer oder pro Kontakt | \$20 / Monat (verbunden mit hohem manuellem Kopieraufwand) | Token-Verbrauch + Serverkosten | **Dein bestehender KI-Plan**, plus jeder Datenanbieter, den du verbindest — kostenpflichtige Aufrufe werden vorher gegen dein Limit geprüft |
| **Outbound-Sicherheit** | Automatischer Kaltversand (hohes Reputationsrisiko) | Manuelles Kopieren & Prüfen | Weitgehende Werkzeug-Berechtigungen | **Verbindliche menschliche Freigabegates** (kein Auto-Versand) |
| **Unternehmenskontext** | Oberflächliches Web-Scraping | Kontext muss ständig neu eingefügt werden | Aufwendige Vektor-DB-Infrastruktur nötig | **Profil als "Second Brain"** (einmal anlegen, in allen Skills verfügbar) |
| **Workflows** | Ausschließlich Kaltakquise per E-Mail | Nur reiner Text | Erfordert individuelles Programmieren komplexer Graphen | **78 Skills & 11 Workflows** (Video, Decks, Social Media, SDR) |
| **Datenschutz & Souveränität**| Liegt in der Cloud des Anbieters | Wird in jeden Chat kopiert | Stark abhängig vom Setup | **Lokale Dateien, Git-ignoriert** — Daten verlassen den Rechner nur über Tools, die du verbindest |

---

### 30-Sekunden-Schnellstart

```bash
# 1. Repository klonen
git clone https://github.com/henryroxstar/gtm-engine.git && cd gtm-engine

# 2. Ordner im bevorzugten KI-Workspace öffnen (Claude Desktop, Google Antigravity, Cursor oder Codex)

# 3. Im Chat eingeben:
"set me up — here's our site: deinedomain.de"
```
*Für das erste Ergebnis brauchst du weder Docker noch Hintergrundserver noch API-Schlüssel. Versandfertige Outreach braucht zusätzlich einen Kontaktdaten-Anbieter.*

---

## Funktionsweise in der Praxis (See it work)

Keine komplizierten Installationsbefehle, keine manuellen Konfigurationsdateien vorab. Du öffnest das Projekt und formulierst eine kurze Anweisung. So wird aus einem typischen To-do am Montagmorgen ein fertiges Arbeitsergebnis:

![Funktionsweise in der Praxis: Vom Prompt zur Freigabe](docs/assets/see-it-work-workflows.png)

Vor sechzig Sekunden standest du vor einem leeren Editor; jetzt hast du einen fundierten, professionellen Beitrag, dessen Wortlaut du vor Verlassen deines Rechners persönlich bestätigt hast.

Das gleiche Prinzip steuert deine gesamte Woche:

---

## Der passende Einstieg

| Dein Hintergrund | Empfohlener Pfad | Zeitaufwand |
|---|---|---|
| **Nicht-technisch** —— Fokus auf Vertrieb und Business, nicht auf Code | [`END-USER-ONBOARDING.md`](END-USER-ONBOARDING.md) (ohne Terminal oder Konsolenbefehle) | ca. 30 Min. |
| **Entwickler / Techniker** —— Direkte Steuerung über Chat oder Konsole | [Erste Schritte (Chat-Modus)](#erste-schritte-chat-modus) | ca. 10 Min. |
| **Architektur-Evaluierer** —— Fokus auf Sicherheit, Steuerung und Design | [Warum das System so aufgebaut ist](#warum-das-system-so-aufgebaut-ist) | ca. 10 Min. |

---

## Vier Betriebs- und Integrationsmodi

Ein gemeinsamer Kern (`gtm_core`), vier Integrationsflächen. **Wählen Sie einen — nicht mischen.**

| Modus | Für wen | Wie Sie ihn betreiben | Was Sie NICHT tun sollten |
|---|---|---|---|
| **1 · Chat-Modus** *(Standard, keine Infrastruktur)* | Gründerinnen und Gründer, Vertrieb und Marketing, die aus dem Chat heraus arbeiten. Das ist es, was die meisten brauchen | Öffnen Sie diesen Ordner in **Claude Desktop**, **Google Antigravity**, **Cursor** oder **Codex** → sagen Sie `"set me up"`. Alle Skills laufen lokal, gegen Ihr Profil und in Ihrer Stimme → [Erste Schritte](#erste-schritte-chat-modus) | **Starten Sie kein** Docker, führen Sie `./scripts/stack.sh` nicht aus und deployen Sie keinen VPS. Der Chat-Modus braucht überhaupt keinen Hintergrunddienst |
| **2 · Selbstgehosteter Agent** | Teams, die unbeaufsichtigte Graph-Ausführung rund um die Uhr wollen | Die **Claude Agent SDK**-Laufzeit, lokal oder auf Ihrem eigenen VPS, führt jeden aktivierten Pack aus und pausiert an den menschlichen Gates in Telegram. Ein Lauf startet per Uhr, per Signal oder per Nachricht von Ihnen. Benötigt Docker und einen Secret-Manager → [`docs/DEPLOY.md`](docs/DEPLOY.md) | **Erwarten Sie hier keinen** interaktiven Chat; der Modus läuft unbeaufsichtigt hinter den Telegram-Gates |
| **3 · Client-REST-API** | Engineers, die ein eigenes Frontend, Dashboard oder einen Mobile-Client bauen | Lokales FastAPI-Backend mit Postgres + Redis auf `:8000` (`./scripts/stack.sh start`), gegen die OpenAPI-Routen `/v1/runs`, `/v1/packs`, `/v1/gates` | **Führen Sie das nicht aus**, nur um die Skills im Chat zu nutzen — Modus 1 ist serverlos |
| **4 · Eingehender MCP-Server** | Anbindung externer Agenten von Drittanbietern (andere Claude-Instanzen, LangChain, AutoGen, CrewAI) an die GTM-Tools | Ausgewählte GTM-Engine-Tools über streamable-HTTP-FastMCP (`deploy/Dockerfile.mcp` auf `:8001`) mit `sk-...`-API-Key-Authentifizierung. Für öffentliche Deployments läuft ein Edge-MCP-Gateway (`deploy/mcp-gateway/`) auf Cloudflare Workers mit Abo-Prüfung und KV-Caching | **Exponieren Sie das nicht** öffentlich ohne API-Key-Authentifizierung, Budgetgrenzen und Edge-Rate-Limiting |

### Unterstützte Workspaces und Umgebungen

| Workspace / Harness | Support-Status | Skill-Ladeverfahren | Anmerkungen |
|---|---|---|---|
| **Claude Desktop / Code** | Nativ | Plugin-System (`plugin/`) | Vollständige Unterstützung aller 78 Skills, MCPs und interaktiver Gates |
| **Google Antigravity** | Nativ | Automatisch via `.agents/` | Multi-Agent-Workflows, native Befehlsausführung und Dateiverwaltung |
| **Cursor / Codex** | Voll kompatibel | `.agents/AGENTS.md` + Regeln | Interaktiver Dialog; Aufruf der Skills über Standard-Prompts |
| **Headless VPS (Agent SDK)** | Dedizierte Umgebung | Containerisierte Agent-Schleife | Autonomer 24/7-Betrieb gesteuert über Telegram-Freigaben |

---

## Erste Schritte (Chat-Modus)

**Voraussetzungen:** Python 3.11+ und [`uv`](https://docs.astral.sh/uv/). Im Chat-Modus unterstützt dich der Agent im ersten Schritt bei der automatischen Einrichtung.

**Schritt 1 — Initialisierung von Engine und Unternehmensprofil (Profile)**
Gib im Chat `"set me up"` ein. Das System führt eine Umgebungsprüfung durch und erstellt anhand deiner Website-URL automatisiert das Unternehmensprofil (Markenidentität, ICP, Wettbewerber, Produkte).

**Schritt 2 — Werkzeuge und API-Schlüssel konfigurieren (Optional)**
Alle externen Integrationen sind optional (standardmäßig greift das System auf schlüssellose Web-Suchen zurück):
- **Akquise-Konnektoren** (Vibe, RocketReach, Apollo): Für verifizierte geschäftliche Kontaktdaten und Kaufsignale.
- **Web-Scraping-Konnektor** (Firecrawl): Für strukturierte Extraktion dynamischer Webseiten.
- **Budget-Obergrenzen festlegen**: Setze Monats- und Pro-Run-Limits. Jede kostenpflichtige Anfrage wird vor der Ausführung verifiziert.

#### Diagnosebefehl (`check_env`)
Überprüfe mit folgendem Befehl den Zustand deines Profils, der Konnektoren und der Budgets:
```bash
uv run python -m gtm_core.check_env
```

---

## Häufige Aufgaben (Schnellzugriff)

| Gewünschtes Ziel | Eingabe im Chat | Aufgerufene Skills | Generierte Ergebnisse |
|---|---|---|---|
| **Zielkunden und Entscheider identifizieren** | `"find prospects in [Branche/Markt]"` | `prospect`, `draft-outreach` | Bewertete Zielliste, CRM-kompatible CSV, geprüfte Kontakte |
| **Wichtige Kundentermine vorbereiten** | `"prep me for my call with [Firma]"` | `call-prep`, `account-dossier` | 5-Minuten-Briefing, SPIN-Fragenkatalog, passende Referenzen |
| **Fundierte Social-Media-Beiträge erstellen**| `"draft my LinkedIn post about [Thema]"` | `content-radar`, `content-studio` | 3 Hook-Optionen (Gate 1) $\rightarrow$ formal geprüfter Post (Gate 2) |
| **Fachlich fundiert in Diskussionen antworten** | `"reply to this post: [URL]"` | `linkedin-reply`, `reddit-reply` | Konstruktiver Fachbeitrag ohne Werbeton (zur Freigabe) |
| **Technisches Lösungskonzept ausarbeiten** | `"design the solution for [Firma]"` | `solution-discovery`, `solution-design`| Formelles Solution Architecture Document (SAD) mit Diagrammen |
| **Strategischen Account-Plan aufstellen** | `"build an account plan for [Firma]"` | `account-plan` | MEDDPICC-Analyse, Stakeholder-Matrix, Meilensteinplan |
| **System- und Profilzustand überprüfen** | `"run environment check"` | `check_env` CLI | Statusbericht zu Profilen, API-Schlüsseln und Budgetschutz |

> Eine vollständige Aufstellung aller 78 Skills findest du in [`docs/SKILLS.md`](docs/SKILLS.md).

---

## Warum das System so aufgebaut ist

![Warum der schlimmste Fall ein Entwurf ist, den Sie ablehnen: Veröffentlichen und Senden stehen gar nicht im Tool-Schema des Modells](docs/assets/capability-boundary.png)

| Eigenschaft | Was es bedeutet | Warum es existiert |
|---|---|---|
| **Menschliche Gates sind nicht umgehbar** | Nichts veröffentlicht, sendet oder importiert sich selbst. `autopublish` ist überall `false`, das Ziel ist serverseitig fixiert und für den Agenten unerreichbar, und ein Pack, das ein Gate *deklariert*, pausiert strukturell — nicht weil der Skill mitspielt. **Nicht jeder Workflow hat zwei Gates**: 5 der 11 Packs erzeugen nur Dokumente und haben gar kein externes Gate | GTM-Ergebnisse tragen Ihren Namen und die Daten Ihrer Kundschaft. Ein Mensch genehmigt die exakten Bytes |
| **Mandantenzustand ist von Grund auf isoliert** | Jedes Unternehmen hat ein eigenes Profil mit eigenem Zustand, eigenen Ledgern und Kundendaten; die Pfadauflösung bindet jeden automatischen Lese- und Schreibzugriff an den aktiven Mandanten. Das gehostete Backend ergänzt Row-Level Security in der Datenbank | Eine Engine bedient viele Unternehmen, ohne dass sich ihre Daten unbemerkt vermischen. Der riskanteste Fehler in der GTM-Automatisierung ist *richtiger Inhalt, falsches Unternehmen* |
| **Das Modell ist das Gehirn; MCP sind die einzigen Hände** | Der Agent macht keinen einzigen rohen HTTP-Aufruf — jedes Scraping, jede Abfrage, jedes Rendering und jede Veröffentlichung läuft über ein MCP-Tool | Least Privilege by Construction. Zugangsdaten liegen beim Tool, nicht im Kontext des Modells. Eine bösartige Anweisung in einer gescrapten Seite kann also weder einen Schlüssel exfiltrieren noch einen Endpunkt erreichen, den die Tool-Oberfläche nicht anbietet |
| **Alles ist profilgesteuert** | Marke, ICP, Personas, Tonalität, Märkte und Budget werden zur Laufzeit aus dem aktiven Profil geladen. Keine fest kodierten Unternehmensnamen im Code, CI-geprüft | Eine Engine für viele Unternehmen — und der Fehler „falsches Unternehmen" wird strukturell schwer, unbemerkt zu machen |
| **Neue Workflows sind Daten, kein Code** | Einen Workflow hinzuzufügen heißt, ein Pack zu schreiben — einen Graphen aus Knoten, der bestehende Skills verdrahtet und den die Engine unverändert validiert und ausführt. Keine Engine-Änderung, kein Deployment | Die Domänenlogik, die Sie am häufigsten ändern, bleibt in überprüfbarer, versionierter Konfiguration, während die Governance, die nie brechen darf, fest in der Engine sitzt |

## Star History

[![Star History Chart](https://api.star-history.com/svg?repos=henryroxstar/gtm-engine&type=Date)](https://star-history.com/#henryroxstar/gtm-engine&Date)

---

## Lizenz

Dieses Projekt ist unter der **Apache License 2.0** lizenziert — siehe [`LICENSE`](LICENSE) für weitere Einzelheiten.
