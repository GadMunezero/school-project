# Running a beta, step by step

From nothing to testers using it. Four stages: get it running for yourself, look around, let other
people in, then collect what they find.

Stage 3 is where the decisions are. Stages 1 and 2 take about ten minutes.

---

## 1. Run it on your own machine

You need [Docker](https://docs.docker.com/get-docker/). Nothing else — no Python, no Node, no
database to install.

These commands are identical on Windows, macOS and Linux, and work in `cmd` and PowerShell as
well as a shell:

```
git clone https://github.com/GadMunezero/school-project.git tradeloom
cd tradeloom
docker compose --env-file .env.demo up -d --build
docker compose --env-file .env.demo exec api python -m tradeloom.cli seed --demo
```

The first run pulls images and builds two of its own, so it takes a few minutes. Then open
**http://localhost:8080** and sign in:

```
demo@example.com
DemoTrader!2024
```

If it does not come up, `docker compose logs api` is the first place to look.

`scripts/demo.sh` does all of the above in one step, plus a generated `SECRET_KEY` and a seed only
when the database is empty — but it is a bash script, so on Windows run it from Git Bash or WSL,
not `cmd`.

To stop, keeping your data:

```
docker compose --env-file .env.demo down
```

| Also running | |
| --- | --- |
| `http://localhost:8080/docs` | the API, interactively — every endpoint, callable |
| `http://localhost:8025` | every email the app sends. Nothing leaves your machine |
| `http://localhost:9001` | uploaded files (`tradeloom` / `tradeloom-secret`) |

```bash
scripts/demo.sh --down     # stop, keep the data
scripts/demo.sh --fresh    # wipe and start over
```

---

## 2. Look around before anyone else does

The seeded workspace has three accounts, hundreds of trades, candles, strategies and one backtest
that has already been run. Worth walking before you invite anyone:

1. **Dashboard** — equity curve, win rate, expectancy off the seeded trades.
2. **Journal** — open a trade, add notes and tags. Try a CSV import.
3. **Analytics** and **Reports** — a headline number and every session behind it.
4. **Backtester** — open the completed run first. Then submit a new one: the worker picks it up
   and executes it, so you are watching the real engine, not a fixture.
5. **Replay** — step a chart forward bar by bar without look-ahead.
6. **Settings** — profile, workspace, members, billing (Stripe is off; the UI still works).
7. **Administration** — users, audit log, and **Feedback**, which is your bug tracker in a beta.

Find the things that are wrong now, while the only person affected is you.

---

## 3. Let other people in

Your testers need an address that is not `localhost`, and the app has to be **built for that
address** — the frontend bakes its API URL in at build time, so putting a tunnel in front of a
stack built for localhost hands every tester a page that tries to call their own machine. That is
what `PUBLIC_URL` is for, and why the URL has to be known before the build rather than after.

### Option A — a tunnel from your machine

Fastest way to get a link into someone's hands. Your machine has to stay awake, and the free
quick-tunnel URL changes every restart, which means a rebuild each time.

```bash
# 1. Start a tunnel and copy the https URL it prints.
cloudflared tunnel --url http://localhost:8080

# 2. In another shell, rebuild for that address with signup closed.
SIGNUP_MODE=invite PUBLIC_URL=https://<the-url-it-printed> scripts/demo.sh
```

Good for a demo, a weekend, a handful of people you are talking to directly. Not where a beta
should live for a month.

### Option B — a small server

One VPS, a domain, TLS, and backups you have actually restored. Follow
[RUNBOOK.md](RUNBOOK.md) — it is the same stack with the pieces a beta actually needs. Use this
once people are relying on it.

Either way, **run it with `SIGNUP_MODE=invite`**. Otherwise the address is open to anyone who
finds it.

### Invite your testers

```bash
scripts/demo.sh --invite "Sam"
```

Prints a code like `74FZPBEZ45`. Send it with the URL — they enter it when they sign up. One use
by default; `scripts/demo.sh --invite "Trading group" 10` issues one good for ten.

Make yourself staff so you can see the admin screens:

```bash
docker compose exec api python -m tradeloom.cli create-admin --email you@example.com
```

---

## 4. Collect what they find

- **Feedback** in Administration. The in-app widget writes here. Read it daily — in a beta this is
  your bug tracker, and it is the only channel most testers will bother with.
- **Errors.** Set `SENTRY_DSN` in `.env` and an unhandled exception reaches you instead of waiting
  to be reported. Off unless configured; it never sends personal data.
- **Email.** Locally everything lands in Mailpit. On a real server, set `SMTP_*` — without it,
  verification and password reset do not work, and a tester locked out is a tester gone.

---

## Before real people rely on it

- **Write the legal documents.** `content/legal/terms.md` and `content/legal/privacy.md` ship as
  placeholders that say so, and production refuses to boot while the `UNWRITTEN-PLACEHOLDER`
  marker is on the first line. Recording that users accepted repository boilerplate would be worse
  than having no terms at all. Nobody can write these for you.
- **Take backups, and restore one.** `scripts/backup.sh` nightly, `scripts/restore-check.sh`
  weekly. An untested backup is a hypothesis — the restore drill is what found a foreign key the
  models declared and the migration never created.
- **Copy the dumps off the machine.** A dump holds every user's complete trading history.

---

## When it will not work

Three failures account for nearly all of them, and all three look like a rejected password:

**"Could not reach the server."** That is the network error, not a credentials error — a wrong
password says so on the password field. The browser could not reach the API. Either it is not
running (`curl http://localhost:8080/api/v1/health/ready`), or the page was built for a different
address than the one you are visiting. Rebuild with the right `PUBLIC_URL`.

**Signed in, immediately signed out.** Cookies are marked `Secure` over HTTPS and are discarded by
the browser over plain HTTP. `scripts/demo.sh` sets this from the scheme of `PUBLIC_URL`; if you
are editing `.env` by hand, keep `COOKIE_SECURE` matching it.

**The right password is refused.** Eight failed attempts locks the account for fifteen minutes.
Wait it out, or reset the password.
