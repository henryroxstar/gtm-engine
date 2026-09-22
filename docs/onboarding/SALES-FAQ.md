# GTM Engine — Sales FAQ

*Plain-English answers to the questions salespeople actually ask. For the step-by-step setup, see the **[End-User Onboarding](../../END-USER-ONBOARDING.md)**.*

---

## Getting started

**Do I need to know how to code?**
No. You never open a "terminal" or type commands. You talk to the assistant in plain English — *"set me up," "find prospects," "prep me for my call with Acme"* — and it does the technical work for you. If you can hold a chat conversation, you can run this.

**Which app do I use?**
For this non-technical guide, the **Claude desktop app** ([claude.ai/download](https://claude.ai/download)). Technical users and developers can also run GTM Engine in **Google Antigravity**, **Cursor**, or **Codex**.

**Can I use ChatGPT, Gemini, or Copilot instead?**
For general non-technical chat, the guide is written for Claude desktop Cowork mode because it requires zero configuration. If you work in developer environments, **Gemini (via Google Antigravity)** and **Copilot/Codex** are supported out of the box using the repository's `.agents/` configuration and tool translation rules.

**How long until I'm actually using it?**
About 30 minutes, and most of that is the engine reading your website and materials. The "set me up" conversation itself is a couple of minutes of your attention.

**Do I need to pay for anything?**
You need a Claude Pro or Max subscription (the "brain"). The engine itself is free. Data tools like Vibe, RocketReach, and Apollo are optional paid add-ons — the engine works on free web search without them, just with less verified data. (Worth knowing about Apollo specifically: you can *connect* it on Apollo's free plan, but Apollo blocks its data API on that plan, so it returns errors rather than contacts until you're on a paid Apollo plan.)

**Does it work on Windows?**
Yes — Mac and Windows both. You'll do everything inside the desktop app.

**I keep seeing "MCP," "API," "VPS," "CLI." Do I need those?**
No. Those are developer terms for advanced setups (custom tool plumbing, servers, command-line tools). They're **out of scope** — your admin handles anything like that. If a step ever asks *you* to open a terminal and type commands, stop and ask your admin.

---

## How it works day-to-day

**What can it actually do for me?**
Find and score prospects, get verified contact details, prep you for calls, draft outreach and LinkedIn content, build account plans and decks, run a weekly market scan, and feed the voice of the customer back into your messaging. Full list in the onboarding guide, Step 7.

**Does it send emails or post to LinkedIn for me?**
**No — and it can't.** It drafts everything and stops. Actually sending an email, enrolling a sequence, or publishing a post is always a button *you* press in the real tool. That's a deliberate safety design, not a limitation to work around.

**How does it already know my company?**
During "set me up" it reads your website and any materials you give it, and builds your **profile** — your ICP, personas, voice, products, competitors, and proof stories. Every task reads that profile, so you never re-explain your company in a fresh chat.

**My product docs are out of date / my ICP changed. What do I do?**
Give it the new material (drag a file in, paste it, or point it at a folder) and say *"learn from my new material."* You can also ask it to *"review my materials for inconsistencies, conflicts, and gaps"* — it flags contradictions and proposes exact edits for you to approve.

**Will it make things up?**
It's grounded in your profile and cites sources for time-sensitive facts (funding, leadership, launches). But **you're the editor** — review before anything leaves your hands. If it can't verify something, it marks it "unverified" rather than stating it as fact.

**Can my whole team use it?**
Yes. Each person installs the desktop app and runs "set me up." If your team shares one company copy of the engine, you share the same profile and knowledge.

**When I ask for a video, why does it decide things before writing anything?**
Because the expensive part is rendering, and the cheapest moment to be wrong is before it starts. It settles nine questions first — what the cover promises, the structure it is committing to, what stays constant across every shot, whether a carousel would land the same point for less. Most of those it answers from your profile and labels as defaults, so a routine piece costs you one question at most. Ask *"show me the brief"* to read all nine back, each with where the answer came from.

**Is that another thing I have to approve?**
No. The nine decisions are shown to you with the plan, never as a separate step, and the list of things you sign off never grows. A video has one checkpoint in the middle, and it is there to save you money rather than to add a step: you approve the frames before anything renders, or, if you are filming it yourself, the file before anything uploads. Nothing external ever happens without the plan and the post itself being approved.

**What is the animatic, and why do I get one before the video?**
It is the still frames held for exactly as long as each shot will run, with the voice-over laid over the top — a rough cut made entirely out of pictures you have already seen, at no cost, because nothing is generated to make it. A row of thumbnails tells you whether the frames are good. It cannot tell you whether the piece has the right pace, and pace is most of why one video holds someone and another loses them in three seconds. The animatic answers that in the time it takes to watch it, before any money is spent. It also reports three things it measured rather than leaving you to spot them: whether the opening line lands inside the first shot, whether any line finishes so close to a cut that it will sound clipped, and whether the frame you chose as the cover is actually the one people see first.

**Can it help if I want to film the video myself rather than generate it?**
Yes, and it writes a different document for that. A generated video needs prompts; a filmed one needs a shoot list — how to set each shot up, whether the camera has to stay locked down, which part of the frame to keep empty so captions have somewhere to sit, how many takes to get, and what to keep consistent between shots so the edit actually cuts together. You get it as a page you can read on your phone on the day. The point is blunt: a generated video that comes out wrong is regenerated for pennies, and a filmed one that comes out wrong is a second filming session, after the light has changed and the shirt is in the wash.

**It checked my solution design and gave me a list. What blocks and what doesn't?**
Two kinds of finding, and they are different. **Errors** are structural — a missing section, a part
in the wrong order, a sentence saying "three things" above a list of two. They stop the document,
and they are not opinions about quality: the document is not the shape it claims to be. **Warnings**
are judgement calls, and they come to you with the reason so you can fix them or say why not. There
is also a **coverage line** every time, which is not a finding at all — it counts how many of the
twelve questions a solution design should answer this one actually answers. It prints every run
because coverage is the one thing you cannot see by scrolling: a design reads complete right up
until their architect asks the question it never answered.

**Why did it refuse to answer part of my security questionnaire?**
Because nothing you have written down backs that answer, and it will not write one that sounds
right. A security questionnaire answer is a written representation to somebody's risk function, and
the person who signs it is not the person who drafted it. You get the question, why nothing backs
it, and who should own writing the entry — which is a to-do list, not a failure. Answer it once,
into your security answers, and every future questionnaire that asks it in different words gets the
same answer automatically.

**It says a CRM isn't connected. Did something break?**
No. The pipeline and CRM-hygiene tasks check for a connected CRM before they run, and this copy of
the engine does not ship one. It stops and says so rather than producing a confident pipeline
report built over nothing — which would look exactly like one built from real data. Same idea
behind *"no baseline from the customer, so here are the questions to ask"* on a value case.

**Where are my files and drafts?**
On your own computer, inside the engine folder. Ask *"where did you save that?"* and the assistant points you to the exact file.

---

## Prospecting & data tools

**What's the difference between Vibe, RocketReach, and Apollo?**
**Vibe** finds the right *companies* (discovery, firmographics, buyer-intent, events). **RocketReach** finds the right *person* at those companies — verified email and direct phone, plus hiring/news signals. **Apollo** is a backup for finding that person's email when RocketReach doesn't have them, plus its own company buying-intent signals. Vibe answers "who to target," RocketReach answers "how to reach them," Apollo fills the gaps.

**Do I have to connect data tools to get value?**
No — prospecting works on free web search. But web-search contacts are **unverified**, and you won't get buyer-intent signals. Connect Vibe (and RocketReach and/or Apollo if they're available as connectors) when you want verified contacts and better-timed outreach.

**How do I connect them?**
In the desktop app: open **Settings → Connectors**, search the tool's name, click **Connect**, and sign in. No files, no commands. If a particular tool asks you to edit a settings file or run a command, that's the advanced path — leave it to your admin, and the engine falls back to web search without it.

**Where do the email addresses come from — is this compliant to email?**
Contact data comes from third-party providers (RocketReach, Vibe, Apollo). They operate on a "legitimate interest" basis for B2B data — importantly, **they do not collect opt-in consent, and they do not check your do-not-contact list for you.** That responsibility is yours. The engine manages suppression (do-not-contact) on your side and steers sends toward markets with clear B2B rules (like the US and Singapore). **Before your first send, read the built-in email compliance checklist** (`docs/email-compliance.md`) — it covers opt-out, your postal address, and market rules. Bottom line: you are responsible for the legality of what you send.

**Will it blast a cold-email list automatically?**
No. It can *build* an email sequence, but it leaves it **paused**. A human reviews it and flips it live in the sending tool. There is no way for the engine to send on its own.

**How much will the paid tools cost me?**
You set a monthly cap and a per-run cap during setup. Every paid step is checked against your cap *before* it runs — the engine trims or falls back to free web search rather than overspending, and it never auto-buys credits.

---

## Privacy & safety

**Where does my data live?**
On your computer, in the engine folder. Your profile, prospect lists, and drafts are local files. Nothing goes to a shared server unless someone deliberately sets up the advanced self-hosted mode (out of scope here).

**Are my logins and keys safe?**
Yes. They're stored in the app's secure connector store, never written into the engine's files. If you try to paste a key into chat, the engine stops you.

**Can it delete my files or do something I can't undo?**
It asks before anything irreversible, and it can't send, publish, or permanently delete on its own. You're always the final step for anything that leaves your machine or can't be undone.

**Is my prospect/customer data (PII) protected?**
It's kept in a per-account folder on your machine and only leaves through a send you approve. Treat it like any customer data — don't share the folder publicly.

---

## Troubleshooting

**The assistant asked to install something (Node, Python) or run a step.**
That's normal during setup — approve it. These are the helpers the engine needs, and the assistant handles the details.

**A tool isn't showing up when I try to use it.**
Data tools connect separately (onboarding Step 6). Ask *"what data tools do I have connected?"* to check. If one isn't there, connect it in **Settings → Connectors**, or, if it needs advanced setup, ask your admin — the engine will use free web search in the meantime.

**It says I've hit my budget cap.**
That's the safety guard doing its job. Either raise your cap (*"increase my monthly budget cap"*), or let it continue on the free web-search path — it offers that automatically.

**The engine couldn't read my website.**
Some sites block automated reading. Just paste your About text or drag in a deck/PDF instead — it works the same from any source.

**A connector won't sign in.**
Try it again from **Settings → Connectors**. If it still won't connect, it may need advanced setup — ask your admin, and use web-search fallback for now.

**How do I get my prospects into my CRM?**
Prospecting produces a ready-to-import CSV. Ask *"where's my prospect list?"* and the assistant points you to the file to import into HubSpot (or your CRM).

**I think something's set up wrong / I want to start over.**
Say *"set me up again."* It offers to review and update your existing profile rather than wiping it, confirming each change. Nothing goes live without your OK.

**A guide or step told me to open a "terminal" and type commands.**
That's the advanced/developer path — not something everyday users do. Stop and ask your admin. In the desktop app, you always ask the assistant instead.

---

## Getting help

- **Setup walkthrough:** [End-User Onboarding](../../END-USER-ONBOARDING.md)
- **Read before your first email send:** `docs/email-compliance.md`
- **Ask the engine itself:** it knows its own abilities — try *"what can you help me with?"* or *"how do I prep for a call?"*
- **Anything mentioning MCP, API, VPS, or a server:** that's for your admin — out of scope for this guide.
