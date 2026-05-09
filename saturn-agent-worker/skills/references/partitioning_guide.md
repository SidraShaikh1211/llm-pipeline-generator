# Partitioning guide (placeholder)

Replace with the real reference. The worker exposes references via the
`read_reference` tool so the agent can pull them in only when needed.

Volume tiers (sketch — replace with authoritative table):

| Tier | Daily volume    | Partition strategy                           |
|------|------------------|-----------------------------------------------|
| 0    | < 1 GB / day    | partition by `business_date_id` only          |
| 1    | 1–10 GB / day   | `business_date_id` + 1 dimension              |
| 2    | 10–100 GB / day | `business_date_id` + 2 dimensions             |
| 3    | 100 GB – 1 TB   | `business_date_id` + 2 dimensions, daily comp |
| 4    | > 1 TB / day    | escalate — design review required             |
