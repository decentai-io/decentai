# Records and files

Agents keep things between conversations: a task with its owner and due date, an invoice with its lines, or a purchase request with its requirements. These appear under **Data → Saved data**, where you can find, inspect, share, and delete them whether or not the agent is still installed.

## Kinds and shapes

Every record belongs to a **kind** an agent declared in its manifest: the Tasks agent keeps *tasks* and *proposed actions*, the Invoicing agent keeps *invoices*, *lines*, *payments* and *allocations*. A kind has a **shape** — its fields, their types, which are plain and which encrypted — and every write, the agent's or yours, is validated against it. That is what lets an agent read back what you wrote by hand.

The Saved data page opens on a card for each agent, with the kinds it keeps and how many records of each. Open an agent and choose a kind under **Data type**: its records appear as a table in that shape, sortable by any column. Open a record to see every field by its label.

## Writing records yourself

Where a manifest allows it, the **+** button offers the kind and generates the form from its fields. An agent that did not allow it keeps that kind to itself — reading and deleting are always yours; creating and editing are only where the agent said so.

## Sharing records

A record an agent stored for you is private. You may share it with your groups, with named people, or organization-wide if you hold that grant. Sharing a record also lets those people's assistant read it when an agent looks there. Only the creator may change sharing.

## Files

**Data → Files** is where uploaded and produced files live: what you uploaded on the page or dropped into a chat, and what agents made — a filled document, a statement, a comparison workbook. The page groups them by where they came from. Each file is stored as bytes with a small metadata record; it can be downloaded, shared and deleted the same way a record can. A file's content is immutable: replacing one is a delete and a create. **Use in a chat** on any file opens a new chat with it attached, ready for you to say what to do with it.

Agents read files by reference — the reference the chat gives them when you attach a file, when you pick one from the card the assistant offers, or the reference another agent returned — and never modify an original.

## Skills

**Data → Skills** holds short instructions your organization wrote for the assistant: how to write a customer quotation, what the expense policy says, how meeting notes are laid out. The assistant reads the catalog of skills in every chat and pulls one in when the task calls for it. Authoring is a grant; reading is baseline.
