"""
Creates data/sample_history.csv: made-up numbers so you can preview the
website before any real data has been collected. Not used once
data/history.csv exists. The site labels sample data clearly.
"""

import csv
import random
from datetime import date, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
random.seed(7)

with (ROOT / "data" / "athletes.csv").open(newline="") as f:
    handles = [r["instagram"].lower() for r in csv.DictReader(f)]

end = date.today()
days = 400
rows = []
for h in handles:
    followers = int(10 ** random.uniform(4.3, 6.6))  # roughly 20K to 4M
    growth = random.uniform(-0.0002, 0.0009)        # daily drift
    posts = random.randint(80, 900)
    for d in range(days, -1, -1):
        day = end - timedelta(days=d)
        followers = max(1000, int(followers * (1 + growth + random.gauss(0, 0.0004))))
        if random.random() < 0.06:
            posts += 1
        avg_likes = int(followers * random.uniform(0.02, 0.06))
        avg_comments = int(avg_likes * random.uniform(0.005, 0.02))
        rows.append({
            "date": day.isoformat(), "instagram": h, "followers": followers,
            "following": random.randint(300, 1500), "posts": posts,
            "avg_likes": avg_likes, "avg_comments": avg_comments,
            "engagement_rate": round(100 * (avg_likes + avg_comments) / followers, 3),
            "posts_last_30d": random.randint(0, 8), "source": "sample",
        })

with (ROOT / "data" / "sample_history.csv").open("w", newline="") as f:
    w = csv.DictWriter(f, fieldnames=list(rows[0]))
    w.writeheader()
    w.writerows(rows)
print(f"Wrote {len(rows)} sample rows")
