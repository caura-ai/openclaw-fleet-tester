.PHONY: load stress spike soak latency correctness k6-all

# ─── k6 load tests ───────────────────────────────────────────────────────────

load:
	k6 run k6/load.ts

stress:
	k6 run k6/stress.ts

spike:
	k6 run k6/spike.ts

soak:
	k6 run k6/soak.ts

latency:
	k6 run k6/latency.ts

correctness:
	k6 run k6/correctness.ts

# the common trio — runs all regardless of individual failures
k6-all:
	-k6 run k6/latency.ts
	-k6 run k6/load.ts
	-k6 run k6/correctness.ts
