# End-to-end tests

These drive the real client against the real API against a real database. Nothing is mocked, and
that is the point: the unit suites prove each layer in isolation, and this suite proves they are
wired together. If `position_builder` changes how a partial exit is averaged, a number on a page
changes and a test here fails.

```bash
./scripts/e2e.sh                    # everything
./scripts/e2e.sh specs/replay.spec.ts
./scripts/e2e.sh --grep "look-ahead"
```

The script seeds a throwaway SQLite database, starts the API, builds the frontend against that
API's URL, and serves `.next/standalone` — the same artefact `docker/frontend.Dockerfile` runs.
Everything is torn down on exit.

## What is covered, and why

| Spec | The property it defends |
| --- | --- |
| `auth` | Sign-in works; the app is unreachable without it; the failure message cannot be used to enumerate accounts |
| `tenancy` | Another workspace's id returns **404, not 403**; a client-supplied `organization_id` cannot redirect a read; a request without the CSRF header is refused; revoking a session invalidates an open tab |
| `journal` | Displayed figures are the API's figures — not values recomputed in the browser; `null` renders as an em dash, never `0.00` |
| `dashboard` | Headline metrics match the analytics endpoint; changing the window refetches rather than filtering client-side |
| `imports` | A real broker CSV round-trips: upload → map → validate → commit → revert, with the journal returning to its exact starting count; re-importing the same file is skipped, not double-counted |
| `replay` | **The browser is never sent a bar past the cursor**; an order does not fill on the bar that created it |

The replay and tenancy specs assert on the payload the browser actually received rather than on
what the page drew. A UI can hide a future candle; only the response proves it was never sent.

## Two things worth knowing

**`localhost`, never `127.0.0.1`.** The CORS allow-list and the session cookie's host are matched
as strings, so mixing the two spellings makes the browser treat them as separate origins — the
login preflight fails and the cookie lands on a host the page is not on. Every part of
`scripts/e2e.sh` uses the same spelling.

**The stack runs with `TRADELOOM_ENV=test`,** which swaps object storage for an in-memory
implementation, silences email, disables rate limiting and makes the health probe verbose. Nothing
the suite asserts on — auth, tenancy, the position builder, the engine — takes a different path.

## It has already earned its keep

The first run of `expectNoFloatArtefacts` failed, and it was right to: several pages were
interpolating decimal strings straight into JSX, so a price stored as `50953.860589764` rendered
with its full tail instead of as `50,953.860590`. That produced `formatPrice`, and the guard now
fails on any value with more than six decimals — the length that means a formatter was bypassed.
