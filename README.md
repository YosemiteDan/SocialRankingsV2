# Aussie Hoops Social

A website that ranks every Australian in the NBA by Instagram: followers, growth over 7 days / 30 days / 1 year, posts, and engagement. GitHub collects the numbers every morning and republishes the site for free.

## What's in this folder

| File | What it does |
|---|---|
| `data/athletes.csv` | The list of players and their Instagram handles. Edit this to add or remove players. |
| `scripts/collect.py` | Visits each profile and saves today's numbers to `data/history.csv`. |
| `scripts/build_site_data.py` | Works out rankings and growth, and writes `site/data.json` for the website. |
| `scripts/make_sample_data.py` | Makes fake numbers so you can preview the design. Not used once real data exists. |
| `site/index.html` | The website itself. |
| `.github/workflows/daily.yml` | Tells GitHub to run everything every morning at about 7:17am. |

You don't need to install anything on your computer. Everything below happens on the GitHub website.

## Setting it up (about 20 minutes)

### 1. Create a GitHub account and a repository
1. Sign up at github.com if you haven't already.
2. Click **+** (top right) → **New repository**.
3. Name it something like `aussie-hoops-social`, choose **Public**, and click **Create repository**. (Public repositories get the website hosting and daily runs for free.)

### 2. Upload the files
1. On the new repository page, click **uploading an existing file**.
2. Drag in everything from this folder **except** the `.github` folder, then click **Commit changes**.
3. The `.github` folder is hidden on most computers, so add the schedule by hand:
   - Click **Add file** → **Create new file**.
   - In the name box type exactly `.github/workflows/daily.yml` (the slashes create the folders).
   - Open `daily.yml` from this folder in a text editor (Notepad or TextEdit), copy everything, and paste it into the big box.
   - Click **Commit changes**.

### 3. Turn on the website and give the schedule permission to save
1. Go to **Settings** → **Pages**. Under **Source**, choose **GitHub Actions**.
2. Go to **Settings** → **Actions** → **General**. Scroll to **Workflow permissions**, choose **Read and write permissions**, and click **Save**.

### 4. Run it for the first time
1. Go to the **Actions** tab. If asked, click the button to enable workflows.
2. Click **Daily update** on the left, then **Run workflow** → **Run workflow**.
3. Wait a few minutes. A green tick means it worked; a red cross means something failed (click it to see the log).
4. Your site is at `https://YOUR-USERNAME.github.io/aussie-hoops-social/`. The link also appears in **Settings** → **Pages**.

After this it runs by itself every morning.

## Checking the results

Open `data/last_run.json` in your repository after each run. It lists which players worked and which failed, and why.

- **"profile not found"** means the Instagram handle is wrong. Most handles in `athletes.csv` are best guesses (see the `handle_checked` column), so check each player's real handle on Instagram, then edit the file on GitHub (click the file → pencil icon → fix → **Commit changes**).
- **"blocked"** means Instagram refused the requests. See below.

Growth figures appear once there's enough history: the 7-day numbers after a week, 30-day after a month, and 1-year after a year.

## If Instagram blocks it

This is likely at some point, because Instagram is wary of requests from cloud servers like GitHub's. The site keeps showing the last good numbers, and the script tries again the next morning. If it keeps failing, there are two options. Both use GitHub **secrets**: **Settings** → **Secrets and variables** → **Actions** → **New repository secret**.

**Option A: Apify (reliable, paid).** Sign up at apify.com, copy your API token from its settings, and save it as a secret named `APIFY_TOKEN`. The script switches to Apify's Instagram profile scraper automatically. Check Apify's current pricing first; tracking a dozen profiles a day is a small job.

**Option B: a logged-in session (free, riskier).** Instagram blocks logged-in visitors less often. Log into Instagram in a browser with a spare account (not your personal one, as it could be flagged or banned), copy the `sessionid` cookie value, and save it as a secret named `IG_SESSIONID`. It stops working when that login expires, so you'll need to replace it occasionally. Automated collection breaks Instagram's terms of use either way, so keep this small.

## Adding players or sports

Add a row to `data/athletes.csv` with the name, team, position and Instagram handle. To track a different sport, start a new copy of this repository with a different list.
