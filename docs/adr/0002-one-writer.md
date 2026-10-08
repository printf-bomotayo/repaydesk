# 2. One component writes to core banking and the ledger

Date: 2026-10-08. Status: accepted.

## Context

Agents investigate payments and draft messages. If an agent, or anything an agent can
influence, could post a repayment or send a message, a bad model output becomes a wrong
balance or a message to a customer.

## Decision

- Only the posting gateway (.NET, milestone 2) holds write credentials for Fineract, Odoo
  and the messaging provider.
- It posts only with an approval record from a person, checks the approver's limit, and
  writes the audit entry in the same transaction as its own posting record.
- Every write carries an idempotency key: Fineract's transaction external id, Odoo's entry
  reference, the message key. A retry can never post twice.
- Agents get read-only MCP tools. They can propose; they can't post.
- The seeder also writes, but only to set up a demo, with its own credentials. It is not
  part of the running system.

## Consequences

- The gateway is the component that must not fail, so it is written in a typed language
  with its own tests, and nothing else needs write access to be reviewed.
- Adding a new write path means changing the gateway, which is deliberate friction.
