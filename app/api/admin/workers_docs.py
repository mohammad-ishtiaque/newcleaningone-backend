"""
OpenAPI descriptions for the manager worker-management endpoints.

Kept out of `workers.py` so the route handlers stay readable and that module
stays inside the project's per-file size limit. Each constant documents every
request field, its supported values, and the errors the endpoint can return.
"""

CREATE_WORKER_DESCRIPTION = """
### Add New Worker
Creates a worker account, emails the worker a temporary password, and adds them to the active roster.
The account is created pre-approved (`approval_status: "approved"`) and flagged `is_temporary_password`
so the worker is forced to set their own password at first login.

#### Request Body

| Field | Type | Required | Default | Supported values |
| :--- | :--- | :--- | :--- | :--- |
| `full_name` | string | **yes** | — | Any name, e.g. `"Rahim Ahmed"` |
| `name` | string | no | mirrors `full_name` | Alias of `full_name`; send either one |
| `email` | string | **yes** | — | Valid address, must not already exist (else `400`) |
| `phone` | string | **yes** | — | E.164 preferred, e.g. `"+8801700000000"` |
| `worker_type` | enum | no | `"employee"` | `"employee"`, `"freelancer"` |
| `position` | string | no | `"Cleaner"` | Free text, e.g. `"Cleaner"`, `"Senior Cleaner"` |
| `status` | enum | no | `"active"` | `"active"`, `"suspended"`, `"banned"` |
| `base_location` | string | no | `"Amsterdam-Centrum"` | Free text location label |
| `hourly_rate` | number | no | `25.0` | Greater than `0`, at most `1000`. **Decimals allowed** (`25.5` = EUR 25.50/hour), stored to 2 decimals |
| `languages` | string[] | no | `["Nederlands","English"]` | Any labels, e.g. `["Nederlands","English","Deutsch"]` |
| `national_id` | string | no | `null` | e.g. `"NID-12345678"` |
| `certificates` | string[] | no | `[]` | e.g. `["Certificate in Professional Cleaning"]` |
| `national_id_front` | string | no | `null` | Uploaded file URL |
| `national_id_back` | string | no | `null` | Uploaded file URL |
| `employee_contract_pdf` | string | no | `null` | Uploaded file URL |

#### Example Request
```json
{
  "full_name": "Rahim Ahmed",
  "email": "rahim.worker@yopmail.com",
  "phone": "+8801700000000",
  "worker_type": "employee",
  "position": "Cleaner",
  "status": "active",
  "base_location": "Amsterdam-Centrum",
  "hourly_rate": 25.5,
  "languages": ["Nederlands", "English"],
  "national_id": "NID-12345678",
  "certificates": ["Certificate in Professional Cleaning"]
}
```

#### Salary field
`hourly_rate` is the **only** pay field. The former `per_hour_salary` was removed because it duplicated
this value as a rounded integer — a rate of `25.5` was shown as `26` while payroll still paid `25.50`.
Sending `per_hour_salary` now returns a `422` naming the replacement rather than silently
creating the worker at the default rate.

#### Errors
- `400` — a user with this email already exists
- `422` — `hourly_rate` is not a number, is `<= 0`, exceeds `1000`, or the removed `per_hour_salary` was sent
- `403` — caller is not a manager
"""

UPDATE_WORKER_DESCRIPTION = """
### Update Worker Details
Partial update — **every field is optional, and any field you omit is left unchanged.**
Changes are mirrored into the `admin_workers` collection.

#### Request Body

| Field | Type | Supported values | Omitted means |
| :--- | :--- | :--- | :--- |
| `full_name` | string | Any name | unchanged |
| `name` | string | Alias of `full_name` | unchanged |
| `email` | string | Valid address, must be unused (else `400`) | unchanged |
| `phone` | string | e.g. `"+8801700000000"` | unchanged |
| `worker_type` | enum | `"employee"`, `"freelancer"` | unchanged |
| `position` | string | e.g. `"Senior Cleaner"` | unchanged |
| `base_location` | string | Free text; also updates `location` | unchanged |
| `hourly_rate` | number | `> 0` and `<= 1000`, decimals allowed (`30.5`) | rate unchanged |
| `languages` | string[] | e.g. `["Nederlands","English"]` | unchanged |
| `status` | enum | `"active"`, `"suspended"`, `"banned"`, `"on_shift"`, `"off_duty"` | unchanged |
| `national_id` | string | e.g. `"NID-12345678"` | unchanged |
| `certificates` | string[] | e.g. `["Advanced Cleaning Cert"]` | unchanged |
| `national_id_front` / `national_id_back` / `employee_contract_pdf` | string | Uploaded file URL | unchanged |

Setting `status` to anything other than `"active"` also sets `is_active: false`.

#### Example Request (raise a worker's rate only)
```json
{ "hourly_rate": 30.5 }
```

#### Errors
- `400` — another user already uses the supplied email
- `404` — no worker with this `worker_id`
- `422` — invalid `hourly_rate`, or the removed `per_hour_salary` was sent (use `hourly_rate`)
- `403` — caller is not a manager
"""

AVAILABLE_WORKERS_DESCRIPTION = """
### List Available Workers
Returns a paginated list of active, approved workers (either approved by a manager or created by an admin).
Excludes unapproved or pending self-signup workers.
"""
