# GTM Engine — End-User Onboarding

*How to get your AI go-to-market teammate running, using the desktop app. No technical background needed. Budget ~30 minutes, most of it the engine reading your company.*

> **What this is.** GTM Engine is an AI teammate for selling. You give it your company **once** — website, product docs, ICP, sales materials — and from then on every task (finding prospects, prepping for calls, drafting outreach, building decks and content) already knows your business, your voice, and your buyers. **You approve everything before it goes out. Nothing sends on its own.**

> **The one thing to understand first:** you will **not** open a "terminal", write code, or type commands. You talk to the assistant in plain English — *"download this," "set me up," "find prospects"* — and **it does the technical work for you**. When you see a command in this guide, it's something the assistant runs on your behalf, shown only so you know what's happening.

> **Technical, and you'd rather drive?** [`README.md`](README.md) is the same setup for someone comfortable in a repo — same destination, about 10 minutes.

---

## Your steps at a glance

| Step | What you do | ~Time |
|---|---|---|
| **1** | Install the Claude desktop app | 5 min |
| **2** | Create a GitHub account | 3 min |
| **3** | Get the engine onto your computer *(the Claude desktop app does it)* | 5 min |
| **4** | *(optional)* Already have a company profile? Drop it in | 2 min |
| **5** | **Onboard** — "set me up," hand over your materials, review | 10 min |
| **6** | **Connect your data tools** *(optional — do this later)* | 5 min |
| **7** | Start working | — |

---

## Step 1 — Install the Claude desktop app

**Use the Claude desktop app.** It's the one built to open a folder on your computer and do the setup work for you.

1. Download it from **[claude.ai/download](https://claude.ai/download)** (Mac or Windows).
2. Install it and sign in.
3. You'll need a **Claude Pro or Max** subscription — this is the "brain." No separate technical key required.

> **A word you'll see in the other docs:** running the engine this way — a folder open in the Claude desktop app, you typing prompts — is called **"Cowork mode"**. It's the default and it's what this guide sets up. The only alternative is a self-hosted server, which is an admin's job, not yours.

> **Why Claude for this guide?** For someone who wants zero terminal commands, the Claude desktop app is the easiest turn-key chat experience. For technical users and developers, GTM Engine also runs natively in **Google Antigravity**, **Cursor**, and **Codex** via the in-repo `.agents/` configuration and tool translation layer.

---

## Step 2 — Create a GitHub account

**GitHub** is the website where the engine's code lives (think of it as the shelf the engine sits on). You need an account to get a copy.

1. Go to **[github.com/signup](https://github.com/signup)**.
2. Enter your email, pick a username and password, and verify your email. The free plan is all you need.

---

## Step 3 — Get the engine onto your computer

You don't download anything by hand — **the Claude desktop app does it for you.**

1. Open the Claude desktop app and start a new project, pointing it at an **empty folder** on your computer (for example, make a folder called `gtm-engine` in your Documents and choose that).
2. In the chat, paste this:

   > **Download the GTM Engine from https://github.com/henryroxstar/gtm-engine into this folder and get it ready to run.**

3. Claude will copy the code down and prepare it. If it asks permission to run a step, or to install a helper (like Node or Python), **say yes** — that's normal and safe.

> **Jargon check:** copying the code down is called *"cloning a repository."* You'll see Claude mention it. You don't have to do anything — just approve when asked.

When it's done, you have the engine on your computer. That folder **is** the engine.

### Then ask it to finish the setup — and check the answer

Once the code is down, paste this:

> **Run the bootstrap script for my operating system, then tell me what the interpreter probe printed.**

That installs the engine's toolchain and runs a one-second self-check — on Windows, `scripts\bootstrap.ps1`; on Mac or Linux, `scripts/bootstrap.sh`. Both end with the same probe. **Wait for it to report a real answer** — it should print a line reading `==> content root: ` followed by a folder path ending in `content`. If it can't, stop here and fix it before doing anything else (Step 5 onward will *look* like it's working while quietly producing nothing).

> **⚠️ Windows: the "install Python" trap.** Windows ships a fake `python` — a zero-byte stub that opens the Microsoft Store instead of running anything. It's on your PATH by default and it **hides a real Python installed afterwards**, so "just install Python" does not fix it. If you ever see *"Python was not found; run without arguments to install from the Microsoft Store"*, that's this.
>
> The bootstrap script handles it for you (it installs its own private Python), so there's nothing to do in advance — but if the probe still fails, turn the stub off: **Settings → Apps → Advanced app settings → App execution aliases**, and switch **off** both `python.exe` and `python3.exe`. Then open a new window and ask Claude to run the bootstrap again.
>
> **Why this matters more than it sounds.** The engine's safety rails — the spending cap, the record of what it did, the checks that stop a bad prospect list from being used — are all small programs that need a real Python. Without one they fail **one at a time and silently**, and the assistant will happily hand you a polished-looking result with nothing behind it. This has already cost one real user ~209 paid contact lookups on a list that no check ever validated.

---

## Step 4 — Already have a company profile? Drop it in *(optional)*

**First time setting this up?** Skip straight to Step 5.

If a colleague already ran setup for your company, or your admin handed you a folder, you don't need to start from scratch:

1. Find the folder your admin/colleague gave you — it's named after your company (e.g. `acme`) and contains a file called `PROFILE.md` plus a few other folders (`knowledge`, `products`, `output`).
2. Open the `gtm-engine` folder from Step 3, then open the `profiles` folder inside it.
3. Drag your company's folder in there, so you end up with `gtm-engine/profiles/acme/` — sitting next to a folder called `_template`.
4. In the chat, say: **"I already have a profile for [your company name] — activate it."**

Claude will confirm it found your profile and switch into it. You can skip Step 5 (onboarding) entirely and go straight to Step 6.

### Keeping your profile safe across updates

When you or your team update GTM Engine by pulling new releases from GitHub, **your company data is never deleted or replaced**:
- **Automatic Protection:** The engine automatically ignores customer profile folders (`profiles/<your-company>/`) and generated deliverables (`content/`). Updates download around your data, leaving your materials and history intact.
- **Pro-Tip (Out-of-Tree Storage):** For complete peace of mind, you can store your company data entirely outside the engine folder. In your `.env` file, specify:
  ```bash
  GTM_PROFILES_ROOT=~/.gtm/profiles
  GTM_CONTENT_ROOT=~/.gtm/content
  ```
  The engine will read your profile and save all deliverables to `~/.gtm/`, completely insulating your assets even if you ever re-download or move the engine repository.

---


## Step 5 — Onboard: "set me up"

This is the important one — it teaches the engine who you are. It happens in three quick parts, all in plain English.

### Part A — Set me up

In the chat, type (with your real website):

> **Set me up — our website is yourcompany.com**

The engine will:
1. **Read your website** and draft your whole company profile — who you are, your ICP and buyer personas, your voice, competitors, content themes, products, brand.
2. **Ask only what a website can't tell it** — your monthly budget cap for paid tools, which markets you're focused on, and how you sign your outreach.
3. **Show you everything to review** before it goes live, then switch it on.

> **What's a "profile"?** It's the engine's memory of your company. Every task reads it, so you never have to re-explain your business again.

### Part B — Hand over your materials

The more real material you give it, the more it sounds like you and sticks to your facts. Give it any of:

- Company / product background — decks, one-pagers, website copy, FAQs, product specs
- Your **ICP, buyer personas, GTM plan**, positioning and messaging
- Case studies, customer stories, testimonials
- A few of **your own emails or posts** so it learns your writing voice

**Easiest ways to hand it over** (use whichever is simplest — no formatting needed):
- **Drag a file** into the chat, or **paste** the text, and say *"add this to my knowledge."*
- Or say *"read the files in this folder"* and point it at where your materials live.

*(Behind the scenes, your raw files are kept in a "source inbox" folder inside your profile as a record; the engine condenses them into its working knowledge. You don't need to manage that folder — the assistant handles it.)*

### Part C — Review for gaps and conflicts

Now ask the engine to sanity-check your materials against what it drafted. Paste this:

> **Review everything I gave you against my current knowledge. Tell me: (1) any inconsistencies — where a document contradicts what you have on file, like an old metric or a renamed product; (2) any conflicts — where two of my documents disagree with each other; (3) any gaps — important facts in my materials you haven't captured yet. Then show me the exact changes you'd make to fix each one, and let me approve them before you save anything.**

**Read what it proposes and approve the changes you agree with.** This is your chance to catch anything it got confidently wrong before it becomes your engine's memory.

> Any time you get new material later, just drop it in and say *"learn from my new material."*

---

## Step 6 — Connect your data tools *(optional — later is fine)*

The engine works right away using free web search. Connecting real data tools upgrades it from "educated guesses" to **verified contacts and live buyer-intent**. You can skip this now and come back when you feel the need.

**Two things for each tool below: sign up on that company's own website first, then connect it in Claude.** Your login stays with that service (or in Claude's secure connector store) — it's never written into the engine's files.

### The tools worth knowing

| Tool | What it gives you | 1. Sign up here | 2. Then connect via |
|---|---|---|---|
| **Vibe Prospecting** | Finds *companies* that fit your ICP, plus firmographics, buyer-intent, and events | [vibeprospecting.explorium.ai](https://vibeprospecting.explorium.ai) | Claude **Settings → Connectors → Add custom connector** (see exact address below) |
| **Higgsfield** | AI-generated visuals for carousels, infographics, and short-form video | [higgsfield.ai](https://higgsfield.ai) | Claude **Settings → Connectors** → search "Higgsfield" |
| **HeyGen** | An AI presenter that speaks your script on camera, for videos where someone has to be on screen | [heygen.com](https://heygen.com) | Claude **Settings → Connectors** → search "HeyGen" |
| **Apollo** | Backup for finding a person's email, plus company buying-signals, when RocketReach doesn't have them | [apollo.io](https://apollo.io) | Claude **Settings → Connectors** → search "Apollo" |
| **RocketReach** | Finds the *person* — verified email + direct phone, plus hiring/news signals | [rocketreach.co](https://rocketreach.co) | Needs an account key and a bit more setup — **ask your admin** |

> **Vibe Prospecting's exact connector address** (it won't show up in a name search — use "Add custom connector" instead):
> ```
> https://vibeprospecting.explorium.ai/mcp
> ```

**Advanced (your admin sets these up — not a self-serve connect):**

| Tool | What it gives you | Website |
|---|---|---|
| **Syften** | Live social-listening — finds people online asking about problems your product solves | [syften.com](https://syften.com) |
| **Email Sequencer** (e.g. Saleshandy) | Multi-step outbound email campaigns — always staged **paused**; a human reviews and activates each one | [saleshandy.com](https://saleshandy.com) |

> **Prefer fewer, more personal emails over an automated sequencer?** Connect your own **Gmail** account instead (Claude **Settings → Connectors** → search "Gmail") and ask the assistant to draft an email to a specific prospect. It writes the draft; you copy or forward it from your own inbox to actually send. This suits reps doing smaller, highly targeted outreach rather than big campaigns — and Gmail is still never sent from automatically, same rule as everything else.

**Want to check what's connected?** Just ask: *"What data tools do I have connected right now?"*

---

## Step 7 — Start working

Just say these in plain English:

**Find & research buyers**
- *"Run my prospecting"* — find, score, and enrich accounts that fit your ICP
- *"Prep me for my call with [company]"* — the buyer, their persona, matched proof stories
- *"Make a dossier for [account]"* — a ~4-page meeting-prep brief
- *"Build an account plan for [company]"*
- *"Write a commercial proposal for [company]"* — what we offer, what it costs over the whole term, and what agreement it becomes; plus a separate internal brief for your approvers that is never sent. It is a draft for discussion, never a contract

**Reach out**
- *"Draft outreach to [name] at [company]"*
- *"Reply to this LinkedIn post"* (paste it in)

**Create content**
- *"Draft my LinkedIn post about [topic]"*
- *"Build a carousel about [topic]"* · *"add visuals to my carousel"*
- *"Make a one-pager for [account]"*

**Make a short video**
- *"Make a short video about [topic]"* — the engine asks which kind, then writes the script and a shot-by-shot storyboard
- Before it writes anything it settles nine questions about **how to make it** — the cover, the structure, what stays the same across every shot, whether a carousel would do the job cheaper. It answers what it can from your profile and labels those answers as defaults, so on a routine piece it asks you at most one thing. You can read all nine back as nine lines
- *"Turn my last post into a video"* · *"cut clips out of this recording"* (drop the file in)
- *"Record a demo of [product] and cut it into clips"*
- Before you approve, it builds an **animatic** — the still frames held for exactly as long as each shot will run, with the voice-over over the top — and sends it to you as a video. It costs nothing: the pictures already exist and nothing is generated. Watching thirty seconds of it answers the question a row of thumbnails cannot, which is whether the thing has the right *pace*: does the opening line land before someone scrolls past, does the read fit the cuts, is the third beat twice as long as it looked on paper. Until this existed, the first chance to notice any of that was after you had paid to render it
- You approve the **storyboard** before anything renders — rendering is the part that costs money, so it never happens on a guess. The **cover** comes with it: where there is more than one candidate you get them side by side on a single sheet and pick by looking, because the cover is a different picture from the first frame and has a different job. One gets tapped, the other starts the film. If the video uses an AI presenter or a cloned voice, it must carry your disclosure line before it can be staged; that's a legal requirement, not a preference
- *"I'll film this one myself"* — the engine writes it as a **shoot list** instead of a render list, and hands you a page you can read on your phone on the day: how to set up each shot, whether the camera has to stay still, which part of the frame to leave empty for captions, how many takes to get, and what to keep the same between shots so the edit works. It is the difference between one filming session and two. This lane has no storyboard and no animatic, because nothing is being generated to preview. It waits for you instead, and when you come back it asks you to approve the exact file before it uploads anything

**Plan & stay current**
- *"Run my market scan"* — this week's signals in your space
- *"Build a deck for [company]"*
- *"Plan my quarter"*
- *"Schedule my weekly market scan and prospecting"* — runs automatically each Monday

**Not sure what to ask?** Type *"what can you help me with?"* — the engine knows its own abilities.

---

## The golden rules (read these once)

- **You're always the last step.** The engine drafts; nothing is emailed, posted, or published until *you* press the button in the real tool. There is no auto-send — by design.
- **Your logins stay yours.** No password, key, or token is ever written into the engine's files. If you try to paste one into chat, it stops you.
- **You talk; it does the technical work.** If a step ever seems to want *you* to type commands in a terminal, that's the advanced path — ask your admin.
- **Start free, add tools when you feel the gap.** Every data tool is optional and falls back to web search.
- **Feed it and it sharpens.** The more real material you give it, the more it sounds like you.

---

## Mini-glossary

| Term | In plain English |
|---|---|
| **Desktop app** | The Claude program on your computer — where you do everything |
| **GitHub** | The website that stores the engine's code |
| **Repository ("repo")** | The folder of engine code you download |
| **Clone** | To download your own copy of that folder (the assistant does this) |
| **Profile** | The engine's memory of your company — built during "set me up" |
| **Connector** | A plug in the app that lets the assistant use an outside tool (like Vibe) |
| **MCP / API / VPS / CLI** | Developer plumbing — **out of scope**; you never touch these |

---

## Status words

Ask the engine "where do I stand?" at any point and it answers in one of six words — never a raw
count, never a lane name, never a column header. Each word says the same thing: whose move it is
next. It is never stored anywhere — asking for it re-derives it fresh from today's route and today's
ledger every time.

| Word | Whose move |
|---|---|
| **Waiting on you** | yours — one decision |
| **Ready to send** | nobody's — it is done |
| **Being fixed** | the machine's — no action |
| **In the sending tool** | already loaded — do not load again |
| **Not emailing** | closed |
| **Needs an address** | accounts in the ledger — the machine's, then yours if it misses |

---

*Questions a salesperson would ask? See the companion **[Sales FAQ](docs/onboarding/SALES-FAQ.md)**.*
