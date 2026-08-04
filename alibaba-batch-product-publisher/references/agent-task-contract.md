# Cross-agent task contract

## Stage ownership

The upstream product-planning/image workflow and this publishing workflow are separate.

### Upstream planning and asset stage

1. Generate a marketing-plan draft.
2. Clearly label inferred prices, SKUs, specifications, packaging, MOQ, and other facts.
3. Pause for the user to correct and confirm those values.
4. Generate product images only after that confirmation.
5. Produce a final handoff package: completed Excel workbook, image folders, and any
   specification/SKU plan files referenced by the workbook.

The upstream stage must not call Alibaba image-upload or product-create APIs.

### Publishing stage (this skill)

1. Accept only the final handoff package.
2. Validate files and values; do not improve or infer marketing content.
3. Run read-only preparation and show the result.
4. Obtain explicit approval for writes.
5. Upload images, build/validate XML, submit exactly once, then initialize and read back
   real SKU inventory exactly to the requested targets.
6. Stop at `已上传` only after inventory verification. Use `已上传（库存待同步）` when SKU
   mapping is temporarily unavailable and resume with `reconcile-inventory`. Platform-review
   verification remains a later optional task.

## Handoff rule

An agent that runs both stages in one conversation must still pause at the confirmation
boundary. User approval of the marketing plan happens before image generation. User
approval of platform writes happens after publisher preflight. These are two different
approvals and neither implies the other.

If prices, SKUs, attributes, titles, packaging, MOQ, or images change after approval,
invalidate the previous publisher preflight and rebuild it before submission.

## Cost-control rule

Use the agent for judgment and user communication. Use bundled programs for repetitive
work. A low-cost agent may execute the publisher safely if it can:

- read the complete `SKILL.md` and relevant reference;
- run Python commands and preserve the work directory;
- parse the JSON result and report blocked/ready rows;
- respect both approval boundaries and the no-retry rule.

It does not need to understand or regenerate the full Schema XML itself.
