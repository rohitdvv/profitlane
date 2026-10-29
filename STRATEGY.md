# ProfitLane — Strategy

## The one-line thesis
Every load board ranks by what a load **pays**. Truckers live on what they **keep**. ProfitLane
is the only board that ranks by take-home after fuel, deadhead and the empty drive out, and now
plans the next load too. Everything below follows from that.

## Market facts (researched October 2026)
- **Costs are at a record high, and rates haven't caught up.** ATRI puts 2025 operating cost at
  $2.336/mi, up 3.4% ([Bulk Transporter](https://www.bulktransporter.com/fleet-management/news/55391667/atri-trucking-expenses-hit-record-2336-per-mile)).
  Owner-operator contract rates slipped to $2.08/mi ([Overdrive](https://www.overdriveonline.com/business/article/15830180/2025-trucking-costs-surge-to-alltime-high-will-rates-keep-up)).
  Dry van contract linehaul averaged $2.69/mi in Q3 2026, below Q4 2021, while non-fuel costs are
  about 30% higher ([FreightWaves](https://freightwaves.com/news/why-trucking-rates-are-rising-without-more-freight)).
  **Margin is the pain point.** A product that finds margin sells itself.
- **Empty miles are the biggest controllable leak.** At ATRI's 16.5% deadhead, a carrier needs
  about $2.80 per loaded mile to cover all miles ([Summar](https://summar.com/atri-trucking-costs/)).
- **What competitors charge.** DAT One carrier plans run $59–$339/mo after a 2026 increase, with no
  free plan ([DAT](https://dat.com/pricing/), [CostBench](https://costbench.com/software/trucking-tms/dat-freight/free-plan/)).
  AI dispatch tools charge $49–$499/mo, or 5% per booked load (Trucker Path dispatch)
  ([Numeo comparison](https://numeo.ai/blog/truckbase-alternatives), [HDT](https://www.truckinginfo.com/news/trucker-path-launches-dispatch-feature-for-small-carriers)).
- **Fintech pays well for carrier referrals.** Outgo pays $500/truck (up to $2,500 per carrier) or
  10% of factoring revenue for life ([Outgo](https://outgo.com/affiliate-referral-program)).
  Summar pays 10–15% recurring ([Summar](https://summar.com/freight-factoring-referrals)).
  TAFS explicitly lists load boards as eligible partners ([TAFS](https://www.tafs.com/?p=55)).
- **Voice AI that calls brokers is being sold now** (Numeo, Bubba, HappyRobot), mostly for routine
  lanes with human escalation ([Numeo](https://numeo.ai/blog/can-ai-negotiate-freight-rates-what-works-what-does-not-and-where-humans-stay-in-control)).
  It's the obvious next feature, and a trust risk if it's done badly.

## Three lenses, one set of decisions

**Jobs: focus, one promise, show the magic.**
- One brand. The UI said "LoadIQ" and the README said "ProfitLane". It's now **ProfitLane: *See what you actually keep.***
- One hero number: *how much more ProfitLane's pick earns than the highest-gross load.* It appears
  on the board, it's tallied per user ("Found you $X"), and it's the pricing page's argument.
- Free users still see the magic (the #1 pick vs. highest-gross). The paywall sits on *depth*
  (the full board and chains), never on the "aha".

**Bezos: customer obsession, a flywheel, many revenue streams, APIs, metrics.**
- Flywheel: free searches → users → money-found proof → Pro conversions → and later anonymized
  lane outcomes → better market scores → better picks.
- Revenue streams, in order of speed: (1) manual invoicing + `/admin/grant` (today), (2) Stripe
  subscriptions, (3) factoring and fuel referrals on every #1 pick, (4) the Fleet API for TMS and
  dispatch services.
- An internal metrics endpoint from day one: MRR, searches, leads, partner clicks, money found.

**Musk: first principles, delete the guess, move fast.**
- A trucker's money leaks in two places: **empty miles** and **payment delay** (30–60 days).
  Profit Chain attacks the first: it replaces the *guessed* empty-out with the *actual* next load.
  Factoring links attack the second.
- Fastest path to cash: Payment Links instead of a billing system, SQLite instead of a database
  server, and lead capture whenever Stripe isn't configured. No feature waits on infrastructure.

## Pricing rationale
| Plan | Price | Why |
|---|---|---|
| Free | $0 | DAT has no free plan. Free + "aha" is the acquisition wedge. 5 searches/day keeps it useful but limited. |
| Pro | $39/mo | Below DAT Standard ($59) and TruckSmarter ($49). One better load (often $500+ more) pays for a year. |
| Fleet | $29/truck/mo, min 3 | Per-truck pricing grows with the customer; API access targets dispatch services and TMS vendors. |

Next pricing experiment: **success fee**. Charge 1% of the extra net found once loads can be
booked through ProfitLane (compare Trucker Path's 5% per booked load).

## 30-day revenue plan
1. **Days 1–3:** Deploy. Set the admin token and sign up with factoring and fuel referral programs
   (Outgo, Summar, TAFS; ask AtoB and Mudflap about terms). Create Stripe Payment Links with a 7-day trial.
2. **Week 1:** Make 50 direct sales calls to owner-operators in the top-10 markets from the Markets tab.
   Offer founder pricing ($29 Pro for life) to the first 100.
3. **Week 2:** Post "pays more, earns less" examples from real searches on trucker Facebook groups,
   TikTok and YouTube. The side-by-side card is designed to screenshot.
4. **Week 3:** Pitch the Fleet API to 10 dispatch services. They resell, ProfitLane gets per-truck revenue.
5. **Week 4:** Review `/admin/metrics`: free → Pro conversion, clicks → partner sign-ups. Raise or
   lower the free limit based on conversion.

## Shipped in this change
Rebrand · Free/Pro/Fleet plans enforced server-side · daily metering · money-found ledger ·
Profit Chain (2-load lookahead) · Stripe Payment Links + verified webhook · lead capture ·
manual grant · factoring/fuel referral CTAs with click attribution · Fleet REST API with keys ·
admin metrics · model upgraded to `claude-sonnet-5-5` · tests for every money path.

## Next (not built yet)
- **Live load feed.** The demo uses a 4-day static snapshot. Revenue at scale needs DAT/Truckstop
  API partnerships or broker integrations. This is the biggest dependency.
- **Account recovery by magic link** (email). Today an account lives on the device it was created on.
- **Book + negotiate.** Voice/email agent that contacts the broker for the #1 pick within the
  driver's floor rate, with a human approving. Unlocks the success-fee model.
- **Alerts:** a text message when a load above $X net appears near the truck.
- **Mobile PWA:** drivers live on phones.
- **Risks:** stale data hurting trust, partner terms changing, LLM cost on free traffic (the
  explanation is optional and could be limited to Pro).
