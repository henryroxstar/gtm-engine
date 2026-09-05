# Travel Policy & Rate Card Framework

A market-aware cost framework for the events-tracker skill. The skill reads the active profile's `home_base`, `nearest_hub`, and `travel_policy` from PROFILE and applies this framework to that profile's specific city — nothing here is hardcoded for a specific user or country.

**These are defaults.** Wherever a profile's `travel_policy` specifies its own rules or caps, the PROFILE wins. The rates below are the fallback when the PROFILE only references "defaults".

Edit the PROFILE `travel_policy` field to change how costs are computed for a given profile. Edit this reference to change the underlying default rate-card structure.

---

## Part 1 — General Policy Principles (defaults — overridable by PROFILE)

### Airfare
- Travel up to 8 hours: **Economy class.**
- Travel exceeding 8 hours: **Premium Economy** (or Economy if unavailable).
- All short-haul conference travel is under 8 hours → Economy applies.

### Lodging
- Use preferred hotels where available. Otherwise: reasonable rate, **must not exceed the regional cap**.
- **USA:** USD 300 / night max
- **Singapore:** SGD 300 / night max (~USD 225)
- **India:** INR 15,000 / night max (~USD 180)
- **UAE/MENA:** USD 250 / night max
- **UK/Europe:** GBP/EUR 175 / night max
- **Other markets:** ≤ equivalent of SGD 300 / night
- Hotel preference: Hilton family (Hilton Garden Inn, Hampton Inn) where available — cost-efficient and loyalty-program eligible.

### Meals
- **USA:** USD 75 / day max
- **Singapore:** SGD 80 / day max (~USD 60)
- **India:** INR 3,000–4,000 / day (~USD 36–48)
- **UAE/MENA:** USD 60 / day max
- **UK/Europe:** GBP/EUR 75 / day max
- Short evening meetup (not a full conference day): use ~40% of the daily meal cap.

### Ground transport
- Full reimbursement for airport ↔ hotel and hotel ↔ event.
- Rail (business/1st class): only when rail leg > 3 hours.

---

## Part 2 — Rate Card Framework (apply to the profile's home base)

The events-tracker computes costs per event using the active profile's city as the anchor. The following logic applies generically; adapt amounts to the specific city's market rates.

### Home base to event — transport

**Local commute (event within commute range of home_base):**
Determine the commute method from `nearest_hub` in PROFILE:
- LIRR / commuter rail: round-trip fare for the applicable zone.
- MRT / metro: round-trip fare for the applicable route.
- Local subway / bus: estimate a round-trip based on standard local fare.
- Include local transit at the destination for getting from the transit terminus to the venue.

**Out-of-city (requires flight per travel_policy):**
- Estimate economy round-trip flight using current market rates for the route.
- Add ground transport at the destination (airport → venue area and back).
- If a significantly cheaper rail option exists and the profile's policy allows it, note it as an alternative.

### Overnight rule

Apply the overnight rule from PROFILE `travel_policy`. Default (if not explicitly set):
1. **2+ events on the same day** → book a hotel
2. **Back-to-back events on consecutive days** → book a hotel
3. **Event ends after 8:00 PM AND next-morning commitment** → book a hotel

Single event ending after 8 PM with nothing the next day → **"Overnight: Optional"**: budget as a day trip, note the optional hotel cost.

### Per-event cost formula

```
Total = Ticket (cheapest available tier)
      + Transport (local commute round-trip  OR  round-trip flight + airport ground)
      + Local transit at destination (if not included above)
      + Lodging (nights × regional_nightly_cap, only if overnight rule triggers)
      + Meals (conference days × daily_meal_cap, OR ~40% cap for short evening meetup)
      + Incidentals (late-night taxi, etc., where relevant; $0 if not applicable)
```

### Within policy flag

- **Yes** — all line items at or under regional caps.
- **Hotel over cap** — realistic local hotel rate exceeds the nightly cap; overage needs pre-approval. Budget shows the cap; a note records the likely true cost.

---

## Part 3 — Rates Tab structure for the spreadsheet

When creating or updating the Rates tab in the events spreadsheet, use this structure:

| Row | Label | Value | Source / notes |
|---|---|---|---|
| B1 | Home base | [from PROFILE home_base] | |
| B2 | Nearest hub | [from PROFILE nearest_hub] | |
| B3 | Local transit — round trip (day) | [derive from hub] | e.g. LIRR Zone 10 peak RT; MRT round-trip |
| B4 | Local transit — round trip (evening) | [off-peak rate if applicable] | |
| B5 | Local subway at destination | [city rate × 2 taps] | |
| B6 | Flight to secondary city | [estimate RT economy] | |
| B7 | Flight to primary marquee city | [estimate RT economy] | |
| B8 | Ground transport — secondary city | [estimate for a trip] | |
| B9 | Ground transport — marquee city | [estimate for a trip] | |
| B10 | Meals — full conference day | [daily meal cap for region] | per policy |
| B11 | Meals — evening meetup | [~40% of daily cap] | |
| B12 | Lodging — local city | [nightly cap for region] | note if market typically exceeds cap |
| B13 | Lodging — secondary city | [nightly cap for region] | |
| B14 | Incidentals (when applicable) | [estimate] | taxi, etc. |

Populate from PROFILE and local market rates. Events tab cost columns should reference these cells by formula where possible so changing a rate automatically updates all event estimates.
