# Run your internship tracker

This repository is intended for a NEW GitHub repository. The source startup database and SimplifyJobs example are references and are never modified.

## Activate scheduled checks

1. Create a GitHub repository named `semiconductor-internships` and initialize it with a README. Public repositories make the log easy to browse; private repositories keep it private and may consume included Actions minutes.
2. Upload this project's files, including `.github/workflows/update.yml`, to its default `main` branch. If your default branch has a different name, adjust the workflow's push branch filter.
3. In **Settings → Actions → General → Workflow permissions**, permit read and write access if organizational policy allows it. The workflow requests only `contents: write`.
4. Open **Actions → Update internship log → Run workflow**. A successful run updates the Markdown tables and data files with a bot commit.
5. The schedule requests checks every six hours, at minute 23 UTC. GitHub schedules can be delayed. Scheduled workflows run only from the default branch. If GitHub disables a schedule due to repository inactivity, re-enable it from Actions.

No paid API, resume upload, application submission, or external secrets are required. Uploading code alone does not prove polling is active: confirm a successful Actions run and a later scheduled run. Protected branches or restricted Actions permissions can prevent the bot from committing; inspect the run logs.

## Local usage

Python 3.11+ and standard library only:

```sh
python -m unittest discover -s tests -v
python scripts/tracker.py --refresh-companies --workers 12
```

Career discovery refreshes every seven days; configured job boards are checked each run. To force rediscovery:

```sh
python scripts/tracker.py --rediscover --workers 12
```

## Add or repair a career source

Edit `data/source_overrides.json` using a board linked by the employer or verified from the official job-board company name. Never guess board slugs. Example schema:

```json
{
  "company-id-from-data-companies": {
    "sources": [
      {"type": "greenhouse", "token": "verified-board-token", "url": "https://job-boards.greenhouse.io/verified-board-token"}
    ],
    "career_pages": ["https://employer.example/careers"],
    "evidence": "Official page used to verify the source"
  }
}
```

Supported complete-board adapters: Greenhouse, Lever (global and EU), Ashby, Recruitee. Other pages can supply structured JobPosting data; unstructured internship links become leads needing review. Workday, Workable, custom apps and JavaScript-only sources are not fully supported. Their companies remain visible in coverage. No CAPTCHA, sign-in wall or robots restriction is bypassed.

## How history works

- `data/jobs.json`: durable identities, employer dates, first/last observations, statuses and relevance tags.
- `data/events.json`: append-only new, updated, closed and reopened events.
- `data/coverage.json`: every company's sources, checks, leads and errors.
- `data/companies.json`: registry of all upstream companies, including categories outside the requested set as additional coverage.
- `data/source_overrides.json`: manually verified source mappings.
- `data/internships.csv`: export for spreadsheet filtering.

Keep data files when updating the code. Deleting them resets first-found dates and history. Job board migration may produce a new identity because board IDs differ. Generic pages cannot establish company-wide closure, so absent generic jobs become unconfirmed. Relevance is a keyword hint, not a hiring prediction; degree, sponsorship, citizenship and dates must be checked in the employer's requirements.

The scanner detects internship titles and structured internship employment types, excludes obvious nontechnical departments, and keeps other technical internships. Some multilingual titles and unconventional descriptions may be missed. The coverage report is the authoritative record of what the scan did and did not inspect.
