---
id: agent_runtime.compaction.seller
version: 1.0.0
status: active
owner: agent-runtime
model_policy: composition_rich
output_schema: HermesToolLoopOutput
cache_policy: stable_system_prompt
---
## Sales Objective
[Where this conversation is in the sale: what the customer wants, what stage we
reached (discovery, pitch, objection, close), and the single next move that
advances it. If the customer is just browsing, say so plainly.]

## Customer
[Who they are: name, phone number, preferred language, and their situation or
pain in their own words. Keep every concrete detail they shared about
themselves or their business.]

## What's Been Offered
[What we presented: the product or course, the exact price and currency, payment
terms, dates, seats, and any discount or bonus mentioned. Copy numbers verbatim.]

## Objections and Decisions
[Concerns the customer raised, how we answered them, and what they decided.
Note anything still unresolved.]

## Handoff and Next Step
[What happens next and who owns it: a follow up, a payment link sent, a handoff
to a human, or a scheduled call. Include the status if a handoff was made.]

## Critical Facts
[Specific values that must survive verbatim: phone numbers, prices, dates,
addresses, order or payment references. Never include passwords, tokens, or
credentials; write [REDACTED] instead.]
