# Driftwood

Driftwood überwacht Repository-Änderungen und hält Dokumentation und Code synchron: Ein Webhook-Receiver auf Cloud Run nimmt GitHub-Events entgegen und veröffentlicht sie auf Pub/Sub; ein Cloud-Run-Job mit einem ADK-Agenten analysiert Diffs, gleicht sie gegen die Doku ab und erstellt bei Bedarf automatisch PRs oder Issues.

## Architektur

```
GitHub Webhook → receiver (Cloud Run Service) → Pub/Sub → agent (Cloud Run Job)
                                                              ├── analysis.py  (Diff → Symbole → Doku-Abschnitte)
                                                              ├── state.py     (Firestore: Fingerprints/Idempotenz)
                                                              └── actions.py   (GitHub: PR/Issue/Eskalation)
```

## Struktur

- `receiver/` – Cloud Run Service: nimmt GitHub-Webhooks entgegen, validiert sie und veröffentlicht sie auf Pub/Sub.
- `agent/` – Cloud Run Job: ADK-Agent, der Diffs analysiert und Aktionen auf GitHub auslöst.
- `deploy/` – Deployment-Skripte (gcloud).
- `docs/` – Architektur- und weitere Dokumentation.

## Setup

1. `.env.example` nach `.env` kopieren und Werte eintragen.
2. `pip install -r requirements.txt`
3. Deployment über `deploy/deploy.sh`.
