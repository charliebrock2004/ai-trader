# Workspace helper contracts

These helpers also run from a plain Git checkout. Sandbox-provided `.grok/`
files, agent prompts and `public/__grok/` assets are not shipped with this repo.
Tests create their own workspace configuration instead of relying on those files.

## Brand checks

The optional in-progress marker is `.grok/og-pending`. It is stale after
10 minutes; a marker is not a permanent exemption from asset checks.

```sh
node scripts/brand-check.mjs --root /workspace
node scripts/brand-check.mjs --game --root /workspace
node scripts/brand-check.mjs --placeholder-ok --root /workspace
```

## Atomic asset hand-over

Stage outside `public/`, then rename into place. The target must be a supported
asset or site identity file; a failed hand-over leaves the previous file intact.

```sh
node scripts/write-atomic.mjs .grok/og.tmp public/og.jpg
node scripts/write-atomic.mjs .grok/banner.tmp public/x-banner.jpg
node scripts/write-atomic.mjs .grok/site.tmp src/lib/og/site.json
```
