# Privacy and Data-Sharing Model

FingerprintHub is a multi-tenant moderation-data replication service. Operators
and participating communities must understand that a perceptual hash is a
stable derived identifier for an image even though the service never receives
or stores image bytes.

## Shared with peer consumers

The sync feed and non-owner detail response expose only the fields required to
replicate, compare, and retire a fingerprint:

- `id` and monotonic `sync_seq`;
- `phash_hex`;
- `algorithm`, `algorithm_version`, and `normalization_version`;
- `category` and recommended `action`;
- `status` (`active`, `hidden`, or `deleted`).

They do **not** expose the contributing consumer, source community, source URL,
reason, timestamps, hit activity, provenance, automation status, or flag data.
A normal read-scoped consumer can browse only its own records. Deployment-wide
browse and statistics require `admin`.

## Stored owner/operator metadata

| Data | Storage | API visibility | Default retention |
|---|---|---|---|
| API key | SHA-256 hash only | never returned | consumer lifetime |
| consumer name, scopes, enabled state | plaintext | authentication/operator access | consumer lifetime/offboarding |
| consumer creation and last-seen times | plaintext | operator database access | consumer lifetime/offboarding |
| pHash and compatibility fields | plaintext | peers, owner, admin | record/tombstone lifetime |
| category, action, status | plaintext | peers, owner, admin | record/tombstone lifetime |
| contributing consumer id | plaintext foreign key | owner/admin | consumer and record lifetime |
| fingerprint timestamps | plaintext | owner/admin | record/tombstone lifetime |
| provenance and automation flag | plaintext | owner/admin | record/tombstone lifetime |
| source community id | AES-GCM envelope | owner/admin | cleared 30 days after deletion |
| source reason and URL | AES-GCM envelope | owner/admin | cleared 30 days after deletion |
| hit reporting consumer id | plaintext foreign key | operator database access | hit row deleted after 90 days |
| hit community id | AES-GCM envelope | no API read route | hit row deleted after 90 days |
| hit distance and timestamp | plaintext | operator database access | hit row deleted after 90 days |
| aggregate hit counters | plaintext | owner/admin | record lifetime |
| flag reason | AES-GCM envelope | no API read route | cleared after 90 days |
| flag consumer and timestamp | plaintext | operator database access | retained for trust-count integrity |
| aggregate flag counter | plaintext | owner/admin | record lifetime |
| deleted tombstone | mixed, metadata minimized after 30 days | peer sync until purge | physically deleted after 180 days |

Encryption is application-level AES-GCM. Database operators and anyone holding
both a dump and the field keys can decrypt encrypted metadata. Encryption does
not make timestamps, hashes, tenant ids, status, or aggregate counters private.

## Collection and permitted use

Collect optional community ids, source URLs, reasons, hit locations, and flag
reasons only when the deployment has a documented moderation need. Do not place
user names, message contents, credentials, or unrelated personal data in free
text. Participants must have authority to contribute the hashes and metadata
they submit and must use peer data only for the agreed community-safety purpose.

Operators should obtain explicit agreement from each participating community
before issuing credentials. That agreement should cover the peer fields above,
local client caching, the retention schedule, moderation appeals, and how a
participant exits the network.

## Deletion, corrections, and offboarding

A contributor can soft-delete its own fingerprint; an admin can delete any
fingerprint. Soft deletion creates a tombstone so offline peers learn to remove
their local copy. Peers must durably process tombstones and should provide their
own local purge and correction mechanism.

Run `tools/purge_retention.py` on a schedule. It previews by default and commits
only with `--apply`. A participant requesting correction or removal should
contact the deployment operator without posting private evidence publicly. The
operator should tombstone affected records, notify participants if necessary,
and account for client caches and backup expiry.

Disabled consumers remain as referential audit records. Revoke access by setting
`enabled=FALSE`; anonymize or remove the row only after its fingerprints and
audit references have been handled under an explicit offboarding plan.

## Backups and incidents

Backups are sensitive even without encryption keys and must have access control,
a fixed expiry, integrity checks, and deletion enforcement. Restoring an older
backup can restore data that had already aged out; run retention immediately
after restoration and reconcile tombstones before serving clients.

FingerprintHub disables aiohttp access logging, so the application does not
retain client IP addresses, request lines, referrers, or user-agent headers.
Reverse proxies and hosting platforms may collect that network metadata outside
the application; operators must document, secure, and expire those logs under
their own policy.

Report security vulnerabilities privately through GitHub Security Advisories.
Do not include credentials, community identifiers, source material, or exploit
details in a public issue. Deployment operators remain responsible for breach
notification, legal requirements, abuse handling, and responding to participant
or data-removal requests in their jurisdiction.
