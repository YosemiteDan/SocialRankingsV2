# Aussie Hoops Social

A website that ranks Australian athletes by Instagram followers, with growth over 7 days, 30 days and a year, and a profile page for each athlete. GitHub collects the numbers every morning and republishes the site for free.

**Phase 1:** basketball, followers only. Every Australian in the NBA and WNBA, plus every player in the NBL and WNBL (imports included and labelled). Engagement (likes, comments, posts) comes in a later phase.

## What's in this folder

| File | What it does |
|---|---|
| `data/athletes.csv` | The list of athletes, their teams and Instagram handles. Edit this to add or remove people. |
| `scripts/collect.py` | Each morning, gets every athlete's follower count from Meta's official API and saves it to `data/history.csv`, plus their Instagram name, bio and photo (`data/profiles.json` and `site/img/`). |
| `scripts/player_details.py` | Once a week, gets height, birthdate, draft, jersey and so on from the NBA and WNBA's official stats, saved to `data/details.json`. |
| `scripts/build_site_data.py` | Works out rankings and growth, and writes the files the website reads (`site/data.json` and `site/players/`). |
| `scripts/make_sample_data.py` | Makes fake numbers so you can preview the design. Not used once real data exists. |
| `site/index.html` | The website itself. |
| `.github/workflows/daily.yml` | Tells GitHub to run everything every morning at about 7:17am Sydney time. |

You don't need to install anything on your computer. Everything happens on the GitHub and Meta websites.

## Setting up GitHub (about 20 minutes)

### 1. Create a repository
1. Sign up at github.com if you haven't already.
2. Click **+** (top right) → **New repository**, give it a name, choose **Public**, and click **Create repository**. (Public repositories get the website hosting and daily runs for free.)

### 2. Upload the files
1. On the new repository page, click **uploading an existing file**.
2. Drag in everything from this folder **except** the `.github` folder, then click **Commit changes**.
3. The `.github` folder is hidden on most computers, so add the schedule by hand: click **Add file** → **Create new file**, type exactly `.github/workflows/daily.yml` in the name box, paste in the contents of `daily.yml` from this folder, and click **Commit changes**.

### 3. Turn on the website and give the schedule permission to save
1. **Settings** → **Pages**. Under **Source**, choose **GitHub Actions**.
2. **Settings** → **Actions** → **General**. Under **Workflow permissions**, choose **Read and write permissions** and click **Save**.

## Connecting Meta (one evening, once)

Meta's Business Discovery API lets your own Instagram account look up public numbers for other Creator and Business accounts. It's free and permitted, and doesn't get blocked. Meta's screens change from time to time, so the wording may differ slightly from below.

### 1. Make your Instagram account a professional account
In the Instagram app: **Settings** → **Account type and tools** → **Switch to professional account**. Choose **Creator** or **Business**. This is free and can be switched back. A new account just for the site is fine.

### 2. Link it to a Facebook Page
1. Create a Facebook Page at facebook.com/pages/create. It can be empty, for example named after your site.
2. On the Page, go to **Settings** → **Linked accounts** (or **Instagram**), and connect your Instagram account.

### 3. Create a Meta developer app
1. Go to developers.facebook.com, log in with Facebook, and click **Get started** to register as a developer.
2. Click **My Apps** → **Create app**. When asked about a use case or app type, choose the options for **Business** and for managing Instagram content. Name it anything.
3. In the app dashboard, add the **Instagram** product and choose the **API setup with Facebook login** option (not "Instagram login": only the Facebook login version can look up other accounts).

The app can stay in development mode. You don't need App Review, because you're the only person using it.

### 4. Get an access token
1. Go to developers.facebook.com/tools/explorer (the **Graph API Explorer**).
2. On the right, choose your app under **Meta App**.
3. Under **Permissions**, add: `instagram_basic`, `pages_show_list`, `pages_read_engagement` and `business_management`.
4. Click **Generate Access Token**. In the pop-up, choose your Facebook Page and Instagram account and approve.

### 5. Make the token permanent
The token you just made lasts about an hour. To get one that doesn't expire:
1. Click the blue **i** next to the token, then **Open in Access Token Tool**. Click **Extend Access Token** at the bottom, and copy the new token it shows (this one lasts 60 days).
2. Back in the Graph API Explorer, paste that token into the **Access Token** box. In the query box, enter `me/accounts?fields=name,access_token,instagram_business_account` and click **Submit**.
3. In the result, find your Page and copy its `access_token` value (the long string, without the quote marks). This is a Page token, and because it came from an extended token, it doesn't expire.
4. Optional check: paste it into developers.facebook.com/tools/debug/accesstoken and click **Debug**. **Expires** should say **Never**.

### 6. Give the token to GitHub
1. In your repository: **Settings** → **Secrets and variables** → **Actions** → **New repository secret**.
2. Name: `META_ACCESS_TOKEN`. Secret: paste the Page token. Click **Add secret**.

Keep this token private. Secrets are hidden even in a public repository, but never paste the token into a file.

### 7. Run it
**Actions** → **Daily update** → **Run workflow** (leave "Collect from Instagram?" ticked) → **Run workflow**. When it finishes, open `data/last_run.json` to see how it went.

### Renewing the token
If `last_run.json` or the run summary says the token has expired or been rejected (for example, after you change your Facebook password), repeat steps 4–6 and replace the secret.

## Checking the results

Open `data/last_run.json` after each run. It lists who was collected, who wasn't and why, and any handles that were filled in automatically.

| Message | What to do |
|---|---|
| **not found, or it's a personal account** | Check the athlete's real handle on Instagram and fix it in `athletes.csv`. If the handle is right, they're on a personal account, which Meta's API can't see. You can leave them out, or add Apify (below). |
| **belongs to '…', not [athlete]** | The handle belongs to someone else, so their numbers weren't saved. Fix the handle. If the account really is the athlete (some show only a first name or nickname), set `handle_checked` to `yes`. |
| **no Instagram handle yet** | Add the handle to `athletes.csv`. |
| **token** messages | See "Renewing the token". |

**About `handle_checked` in `athletes.csv`:**
- `no`: a best guess. The script only saves numbers if the name on the Instagram account matches the athlete.
- `wikidata`: filled in automatically from Wikidata (the free database behind Wikipedia). Same name check applies.
- `yes`: you've checked it yourself, so the name check is skipped.

Blank handles, and handles that fail, are looked up on Wikidata automatically, so some fix themselves by the next run.

To edit `athletes.csv` on GitHub: open the file, click the pencil icon (or press **E**), make the change, and click **Commit changes**.

Profile photos appear after the first successful run and are refreshed weekly. Until then each athlete shows their initials. Player details (height, draft and so on) appear after the first weekly update; the NBA's stats site sometimes doesn't answer GitHub's servers, in which case it simply tries again the next day.

Growth figures fill in over time: weekly after 7 days, monthly after 30, yearly after a year.

## Videos on profiles

Each profile features the athlete's latest reel as a large thumbnail that plays on the page when clicked (a small copy of the preview image is saved in `site/img/hl/`, refreshed only when they post a new one). Below that are their other recent reels and posts, fetched in the same daily lookup as their follower count, so it costs nothing extra. The posts are shown with Instagram's own embed and only load when a visitor scrolls to them. Athletes on personal accounts don't get this strip unless Apify is set up.

Every profile also has a **Watch highlights** button, which opens a YouTube search for that player's highlights in their league.

## Optional: Apify for personal accounts

Meta's API only sees Creator and Business accounts. For athletes on personal accounts, sign up at apify.com, copy your API token, and add it as a repository secret named `APIFY_TOKEN`. The script then uses Apify for just those athletes. Check Apify's pricing first; a handful of profiles a day costs very little.

## Phase 2: engagement

When you're ready to show likes, comments and posts, change `const SHOW_ENGAGEMENT = false;` to `true` near the top of the script in `site/index.html`. The collector will also need updating to fetch recent posts, which Meta's API supports.

## Adding athletes, leagues or sports

Each row in `data/athletes.csv` is one athlete in one league:

| Column | What goes in it |
|---|---|
| `name` | Full name, spelled the same on every row for that athlete |
| `sport`, `league`, `team`, `position` | e.g. `Basketball`, `NBL`, `Sydney Kings`, `G` |
| `import` | `yes` for import players, otherwise blank. The site has an "Aussies only" switch that hides them. |
| `instagram` | Their handle without the @, or blank to let Wikidata try to find it |
| `handle_checked` | `no`, `wikidata` or `yes` (see "Checking the results") |

Someone who plays in two leagues (e.g. the WNBA in the northern summer and the WNBL in the Australian summer) gets one row per league with the same name. They appear once on the site, under both leagues, sharing one Instagram account.

**Keeping rosters current:** NBL and WNBL squads change between seasons and with injury replacements during them. Rosters aren't updated automatically yet, so add and remove rows as players come and go. The current lists come from basketball.com.au's NBL27 and WNBL27 roster pages (October 2026). With more than one league, the site shows league filters above the rankings. With more than one sport, the headline changes to cover all sports. Each profile shows the athlete's rank within their own league. Player details are only fetched for NBA and WNBA athletes for now.

## Changing the site's name

The name appears in three places in `site/index.html`: the line `const SITE_NAME = "..."` near the top of the script, the `<title>` tag, and the `og:title` tag. The image shown when the site is shared on social media is `site/og.png`; replace it with your own 1200 × 630 image whenever you like. Some sites (Facebook, LinkedIn) need its full address, so once your site is live, change `content="og.png"` in the `og:image` tag to `content="https://YOUR-USERNAME.github.io/YOUR-REPO/og.png"`.
