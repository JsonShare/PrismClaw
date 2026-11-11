# Route B: FILL

Fill form fields in an existing PDF without altering layout or design.

---

## Pipeline

```bash
# Step 1: inspect — discover all form fields
python3 scripts/fill_pdf/fill_inspect.py --input form.pdf

# Step 2: fill — write values into fields
python3 scripts/fill_pdf/fill_write.py --input form.pdf --out filled.pdf \
  --values '{"FirstName": "Jane", "Agree": "true", "Country": "US"}'
```

---

## Field type value format

| Field type | Value format |
|---|---|
| `text` | Any string |
| `checkbox` | `"true"` or `"false"` |
| `dropdown` | Must match a choice value from inspect output |
| `radio` | Must match a radio value (often starts with `/`) |

---

## Rules

- Always run `fill_inspect.py` first to get exact field names.
- The inspector reports: field name, type, current value, allowed values, and page number.
- Dropdown values must exactly match one of the allowed choices reported by the inspector.
- The writer sets `/NeedAppearances` flag so PDF viewers regenerate field appearances automatically.
