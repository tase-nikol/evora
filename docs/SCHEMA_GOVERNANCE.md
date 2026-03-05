# Schema Governance CLI

**Evora's built-in CLI for managing event schema evolution and ensuring backward compatibility.**

---

## 🎯 Overview

The Schema Governance CLI helps teams maintain event contract stability across service deployments. It provides automated schema validation, compatibility checking, and baseline management to prevent breaking changes from reaching production.

**Key Features:**
- ✅ **Backward compatibility validation** - Detect breaking vs non-breaking changes
- ✅ **Version enforcement** - Require `__version__` bumps for breaking changes
- ✅ **Event type validation** - Prevent accidental event identity changes
- ✅ **Baseline exports** - Create schema snapshots for CI/CD pipelines
- ✅ **JSON & text output** - Machine-readable and human-friendly formats
- ✅ **File and module support** - Load events from `.py` files or installed packages

**Prerequisites:**

Events must follow the **nested Data pattern** to work with schema governance:

```python
class YourEvent(Event):
    __version__ = 1
    
    class Data(BaseModel):  # ← Inner Data class required
        your_field: type
    
    data: Data  # ← Field using inner Data
```

See [Design Pattern: Why Nested Data Class?](#design-pattern-why-nested-data-class) for rationale.

---

## 📋 Commands

### `evora schema check`

Compare two event schemas for compatibility.

**Syntax:**
```bash
evora schema check <old> <new> [options]
```

**Arguments:**
- `old` - Reference to the baseline schema (see [Reference Formats](#reference-formats))
- `new` - Reference to the new schema to validate

**Options:**
- `--format {text|json}` - Output format (default: `text`)
- `--require-version-bump` - Fail if breaking changes without version increase (default: `true`)
- `--no-require-version-bump` - Disable version bump requirement
- `--enforce-event-type` - Fail if `event_type` differs (default: `true`)
- `--no-enforce-event-type` - Allow event type changes

**Exit Codes:**
- `0` - Schemas are backward compatible
- `1` - Breaking changes detected or validation error

**Examples:**

```bash
# Check module-to-module compatibility
evora schema check myapp.events:UserCreated myapp.events:UserCreatedV2

# Check file-to-file compatibility
evora schema check ./v1/events.py:OrderEvent ./v2/events.py:OrderEvent

# Check against baseline JSON
evora schema check baselines/user_created.json myapp.events:UserCreated

# Get machine-readable output
evora schema check old.json new.py:Event --format json

# Allow event type changes (not recommended)
evora schema check old:A new:B --no-enforce-event-type
```

---

### `evora schema export`

Export a normalized schema to JSON for baseline management.

**Syntax:**
```bash
evora schema export <ref> [options]
```

**Arguments:**
- `ref` - Reference to the event class (see [Reference Formats](#reference-formats))

**Options:**
- `--out`, `-o` - Output file path (default: stdout, use `-` for explicit stdout)

**Exit Codes:**
- `0` - Export successful
- `1` - Export failed (class not found, invalid schema, etc.)

**Examples:**

```bash
# Export to stdout
evora schema export myapp.events:UserCreated

# Export to file
evora schema export myapp.events:UserCreated --out baselines/user_created.json

# Export from file
evora schema export ./events.py:OrderPlaced -o order_placed_baseline.json
```

**Output Format:**
```json
{
  "event_type": "users.created.v1",
  "kind": "evora.schema",
  "schema": {
    "properties": {
      "email": {
        "type": "string"
      },
      "user_id": {
        "type": "integer"
      }
    },
    "required": [
      "email",
      "user_id"
    ],
    "type": "object"
  },
  "version": 1
}
```

---

## 🔗 Reference Formats

Schema references can point to event classes in multiple ways:

### Module Path
```bash
module.path:ClassName
```

Load a class from an installed Python package:
```bash
evora schema check myapp.events:UserCreated myapp.events:UserCreatedV2
```

### File Path
```bash
path/to/file.py:ClassName
```

Load a class directly from a Python file:
```bash
evora schema check ./old/events.py:MyEvent ./new/events.py:MyEvent
```

### Baseline JSON
```bash
path/to/baseline.json
```

Load a previously exported schema baseline (only valid as `old` reference in `check`):
```bash
evora schema check baselines/user_event.json myapp.events:UserCreated
```

---

## 🏗️ Design Pattern: Why Nested `Data` Class?

### Required Pattern

The Schema Governance CLI **requires** events to follow this pattern:

```python
class UserCreated(Event):
    __version__ = 1
    
    class Data(BaseModel):  # ← Inner Data class (REQUIRED)
        user_id: int
        email: str
    
    data: Data  # ← Field using inner Data
    
    @classmethod
    def event_type(cls):
        return "users.created"
```

**Note:** Simple events with fields directly on the Event class will **not work** with schema governance:

```python
# ❌ This pattern does NOT work with schema CLI
class UserCreated(Event):
    __version__ = 1
    user_id: int  # ← Fields directly on Event
    email: str
```

### Why This Design?

This pattern provides several architectural benefits:

#### 1. **Clear Separation of Concerns**

```python
class OrderPlaced(Event):
    # Event metadata (framework-level)
    __version__ = 2
    __event_name__ = "orders.placed.v2"
    
    # Business data (application-level)
    class Data(BaseModel):
        order_id: str
        customer_id: str
        items: list[OrderItem]
    
    data: Data
```

- **Event class**: Framework concerns (versioning, routing, envelope generation)
- **Data class**: Business schema (the actual payload that gets validated and evolved)

This separation makes it obvious what's framework metadata vs. business data.

#### 2. **Schema Evolution Isolation**

The nested `Data` class acts as a **stable contract boundary**:

```python
# Version 1
class UserCreated(Event):
    __version__ = 1
    
    class Data(BaseModel):
        user_id: int
        email: str
    
    data: Data

# Version 2 - only Data changes, Event structure stays stable
class UserCreated(Event):
    __version__ = 2  # ← Bumped
    
    class Data(BaseModel):
        user_id: str  # ← Changed to UUID
        email: str
        created_at: str  # ← Added
    
    data: Data  # ← Same field name, updated schema
```

The Event class structure remains constant—only the nested Data schema evolves. This makes schema diffing straightforward and reliable.

#### 3. **Namespace Clarity**

Prevents naming conflicts between framework and business fields:

```python
class PaymentReceived(Event):
    # Framework could add these without conflicts
    __version__ = 1
    __retry_count__ = 0
    __received_at__ = None
    
    # Business data isolated in its own namespace
    class Data(BaseModel):
        payment_id: str
        amount: Decimal
        received_at: datetime  # ← No conflict with __received_at__
    
    data: Data
```

#### 4. **Envelope Compatibility**

Matches the CloudEvents-inspired envelope structure:

```json
{
  "id": "123",
  "type": "users.created",
  "source": "users-service",
  "dataschema": "evora://UserCreated/1",
  "data": {           # ← Matches the nested Data pattern
    "user_id": 123,
    "email": "user@example.com"
  }
}
```

The `data` field in the envelope directly corresponds to the `Data` class, making serialization/deserialization clean.

#### 5. **Type Safety for Schema Extraction**

The extractor explicitly looks for the `Data` class:

```python
def extract_schema(event_cls: Type[Event]) -> dict[str, Any]:
    if not hasattr(event_cls, "Data"):
        raise ValueError(f"{event_cls.__name__} does not define inner Data model")
    
    return event_cls.Data.model_json_schema()  # Extract schema from Data
```

This explicit requirement:
- ✅ Makes intent clear (this event supports schema governance)
- ✅ Provides better error messages
- ✅ Prevents accidental extraction of framework fields
- ✅ Enables consistent schema comparison

#### 6. **Future-Proofing**

Allows the framework to evolve without breaking schema governance:

```python
# Framework can add new features without schema impact
class Event(BaseModel):
    # Future: Add tracing, metrics, etc.
    __trace_id__: Optional[str] = None
    __span_id__: Optional[str] = None
    
    # Data schema remains unaffected
```

### Trade-offs

**Pros:**
- ✅ Clear separation between framework and business concerns
- ✅ Schema evolution is isolated and predictable
- ✅ Better type safety and error messages
- ✅ Matches envelope structure
- ✅ Prevents namespace collisions

**Cons:**
- ❌ Slightly more verbose (extra nesting level)
- ❌ Requires discipline (developers must follow pattern)
- ❌ Two patterns exist (simple events vs. schema-governed events)

### When to Use Each Pattern

**Use Nested Data (Schema Governance):**
```python
# ✅ For production events that need:
# - Schema validation
# - Compatibility checking
# - Multi-service contracts
# - Long-term evolution

class UserCreated(Event):
    __version__ = 1
    class Data(BaseModel):
        user_id: int
    data: Data
```

**Use Simple Pattern:**
```python
# ✅ For internal/temporary events:
# - Testing
# - Proof of concepts
# - Single-service internal events
# - Events that won't evolve

class DebugEvent(Event):
    message: str
    timestamp: float
```

### Migration Path

If you have simple events and want to add schema governance:

```python
# Before (simple)
class UserCreated(Event):
    __version__ = 1
    user_id: int
    email: str

# After (schema-governed)
class UserCreated(Event):
    __version__ = 1
    
    class Data(BaseModel):
        user_id: int
        email: str
    
    data: Data
    
    # Optional: Keep backward compatibility
    def __init__(self, **kwargs):
        if 'data' not in kwargs:
            # Allow old-style initialization
            kwargs = {'data': kwargs}
        super().__init__(**kwargs)
```

---

## 🔍 Compatibility Rules

### Breaking Changes ❌

These changes will **fail** compatibility checks:

1. **Field removed**
   ```python
   # Old
   class MyEvent(Event):
       class Data(BaseModel):
           user_id: int
           email: str  # ← removed in new version
       data: Data
   
   # New
   class MyEvent(Event):
       class Data(BaseModel):
           user_id: int
       data: Data
   ```
   Error: `email removed`

2. **Field made required**
   ```python
   # Old
   class MyEvent(Event):
       class Data(BaseModel):
           user_id: int
           nickname: str | None = None
       data: Data
   
   # New
   class MyEvent(Event):
       class Data(BaseModel):
           user_id: int
           nickname: str  # ← now required
       data: Data
   ```
   Error: `nickname made required`

3. **Type changed**
   ```python
   # Old
   class MyEvent(Event):
       class Data(BaseModel):
           user_id: int
       data: Data
   
   # New
   class MyEvent(Event):
       class Data(BaseModel):
           user_id: str  # ← type changed
       data: Data
   ```
   Error: `user_id: type changed integer → string`

4. **Enum value removed**
   ```python
   from enum import Enum
   
   class Status(str, Enum):
       ACTIVE = "active"
       PENDING = "pending"
       DELETED = "deleted"
   
   # Old
   class MyEvent(Event):
       class Data(BaseModel):
           status: Status
       data: Data
   
   # New (DELETED removed from Status enum)
   class Status(str, Enum):
       ACTIVE = "active"
       PENDING = "pending"
       # DELETED removed
   ```
   Error: `status: enum value removed 'deleted'`

5. **Required field added**
   ```python
   # Old
   class MyEvent(Event):
       class Data(BaseModel):
           user_id: int
       data: Data
   
   # New
   class MyEvent(Event):
       class Data(BaseModel):
           user_id: int
           email: str  # ← new required field
       data: Data
   ```
   Error: `email added as required`

### Non-Breaking Changes ✅

These changes are **allowed**:

1. **Optional field added**
   ```python
   # Old
   class MyEvent(Event):
       class Data(BaseModel):
           user_id: int
       data: Data
   
   # New
   class MyEvent(Event):
       class Data(BaseModel):
           user_id: int
           nickname: str | None = None  # ← optional field
       data: Data
   ```

2. **Field made optional**
   ```python
   # Old
   class MyEvent(Event):
       class Data(BaseModel):
           user_id: int
           email: str
       data: Data
   
   # New
   class MyEvent(Event):
       class Data(BaseModel):
           user_id: int
           email: str | None = None  # ← now optional
       data: Data
   ```

3. **Enum value added**
   ```python
   from enum import Enum
   
   class Status(str, Enum):
       ACTIVE = "active"
       PENDING = "pending"
   
   # Old
   class MyEvent(Event):
       class Data(BaseModel):
           status: Status
       data: Data
   
   # New (ARCHIVED added to Status enum)
   class Status(str, Enum):
       ACTIVE = "active"
       PENDING = "pending"
       ARCHIVED = "archived"  # ← new value
   ```

---

## 🔢 Version Management

### The `__version__` Attribute

Every event class should define a `__version__` attribute:

```python
class UserCreated(Event):
    __version__ = 1  # ← Schema version
    
    class Data(BaseModel):  # ← Inner Data class (this is what gets extracted)
        user_id: int
        email: str
    
    data: Data  # ← Field using the inner Data class
    
    @classmethod
    def event_type(cls):
        return "users.created.v1"
```

### Version Bump Rules

By default (with `--require-version-bump`), the CLI enforces:

- **Breaking change** → `__version__` MUST increase
- **Non-breaking change** → `__version__` increase is optional

**Example - Breaking change without version bump (FAILS):**
```bash
# v1.py: __version__ = 1
# v2.py: __version__ = 1 (removed field)

$ evora schema check v1.py:Event v2.py:Event
❌ breaking change requires __version__ bump: old=1 new=1
```

**Example - Breaking change with version bump (PASSES):**
```bash
# v1.py: __version__ = 1
# v2.py: __version__ = 2 (removed field)

$ evora schema check v1.py:Event v2.py:Event
❌ BREAKING CHANGES DETECTED

  - email removed

Compatibility: NOT BACKWARD COMPATIBLE
```

The check still reports breaking changes but exits with code 1 only if the version wasn't bumped.

---

## 📝 Output Formats

### Text Format (default)

Human-readable output for terminal use:

```bash
$ evora schema check old.py:Event new.py:Event

✅ BACKWARD COMPATIBLE

Non-breaking changes:
  - nickname added as optional
  - status made optional
```

```bash
$ evora schema check old.py:Event new.py:Event

❌ breaking change requires __version__ bump: old=1 new=1

❌ BREAKING CHANGES DETECTED

  - email removed
  - user_id: type changed integer → string

Compatibility: NOT BACKWARD COMPATIBLE
```

### JSON Format

Machine-readable output for CI/CD integration:

```bash
$ evora schema check old.py:Event new.py:Event --format json
```

```json
{
  "breaking_changes": [
    "email removed",
    "user_id: type changed integer → string"
  ],
  "error": "breaking change requires __version__ bump: old=1 new=1",
  "new": {
    "event_type": "users.created.v1",
    "schema": null,
    "source": "new.py:Event",
    "version": 1
  },
  "non_breaking_changes": [],
  "ok": false,
  "old": {
    "event_type": "users.created.v1",
    "schema": null,
    "source": "old.py:Event",
    "version": 1
  }
}
```

**JSON Schema:**
```typescript
{
  ok: boolean;                    // Overall compatibility result
  error: string | null;           // Error message (version/event_type)
  breaking_changes: string[];     // List of breaking changes
  non_breaking_changes: string[]; // List of non-breaking changes
  old: {
    event_type: string;
    version: number;
    source: string;
    schema: object | null;
  } | null;
  new: {
    event_type: string;
    version: number;
    source: string;
    schema: object | null;
  } | null;
}
```

---

## 🚀 CI/CD Integration

### GitHub Actions Example

```yaml
name: Schema Validation

on:
  pull_request:
    paths:
      - 'myapp/events/**'

jobs:
  schema-check:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v3
        with:
          fetch-depth: 0  # Need full history for comparison
      
      - name: Set up Python
        uses: actions/setup-python@v4
        with:
          python-version: '3.11'
      
      - name: Install dependencies
        run: |
          pip install -e .
          pip install evora
      
      - name: Export production baselines
        run: |
          git checkout main
          evora schema export myapp.events:UserCreated -o /tmp/user_created_main.json
          evora schema export myapp.events:OrderPlaced -o /tmp/order_placed_main.json
      
      - name: Checkout PR branch
        run: git checkout ${{ github.head_ref }}
      
      - name: Check schema compatibility
        run: |
          evora schema check /tmp/user_created_main.json myapp.events:UserCreated --format json
          evora schema check /tmp/order_placed_main.json myapp.events:OrderPlaced --format json
```

### GitLab CI Example

```yaml
schema_validation:
  stage: test
  image: python:3.11
  before_script:
    - pip install -e .
  script:
    # Export baseline from main branch
    - git fetch origin main
    - git show origin/main:myapp/events.py > /tmp/main_events.py
    
    # Check compatibility
    - evora schema check /tmp/main_events.py:UserCreated myapp/events.py:UserCreated
    - evora schema check /tmp/main_events.py:OrderPlaced myapp/events.py:OrderPlaced
  only:
    - merge_requests
```

### Pre-commit Hook

Create `.git/hooks/pre-commit`:

```bash
#!/bin/bash

echo "🔍 Checking event schema compatibility..."

# Export current main baseline
git stash -q --keep-index
git checkout main -- myapp/events.py
evora schema export myapp/events.py:UserCreated -o /tmp/baseline.json
git checkout - -- myapp/events.py
git stash pop -q

# Check staged changes
if ! evora schema check /tmp/baseline.json myapp/events.py:UserCreated; then
    echo "❌ Schema compatibility check failed!"
    echo "   Fix breaking changes or bump __version__"
    exit 1
fi

echo "✅ Schema check passed"
```

---

## 🏗️ Workflow Patterns

### Pattern 1: Baseline Files in Repository

Store schema baselines alongside code for version control:

```
myapp/
├── events.py
└── schemas/
    ├── user_created_v1.json
    ├── user_created_v2.json
    └── order_placed_v1.json
```

**Update workflow:**
```bash
# 1. Check compatibility
evora schema check schemas/user_created_v1.json events.py:UserCreated

# 2. If breaking, bump version in events.py
#    class UserCreated(Event):
#        __version__ = 2  # ← increment

# 3. Export new baseline
evora schema export events.py:UserCreated -o schemas/user_created_v2.json

# 4. Commit both
git add events.py schemas/user_created_v2.json
git commit -m "feat: add optional nickname field to UserCreated (v2)"
```

### Pattern 2: Dynamic Baseline from Main Branch

Compare against the deployed version without storing baselines:

```bash
# In CI pipeline
git fetch origin main
git show origin/main:myapp/events.py > /tmp/main_events.py

evora schema check /tmp/main_events.py:UserCreated myapp/events.py:UserCreated
```

### Pattern 3: Registry-based Validation

Store baselines in a schema registry service:

```bash
# In CI
curl -o /tmp/baseline.json https://schema-registry.company.com/events/user_created/latest

evora schema check /tmp/baseline.json myapp/events.py:UserCreated

# On success, register new version
if [ $? -eq 0 ]; then
    evora schema export myapp/events.py:UserCreated | \
        curl -X POST https://schema-registry.company.com/events/user_created \
             -H "Content-Type: application/json" \
             -d @-
fi
```

---

## 🐛 Troubleshooting

### Error: "Not an Evora schema baseline JSON"

**Problem:** Trying to use a non-baseline JSON file in `evora schema check`

**Solution:** Ensure the JSON was created with `evora schema export` and has `"kind": "evora.schema"`

```bash
# Correct baseline format
{
  "kind": "evora.schema",  # ← Required
  "event_type": "...",
  "version": 1,
  "schema": { ... }
}
```

---

### Error: "Event reference must be module.path:ClassName"

**Problem:** Missing `:` separator in reference

**Solution:** Use correct format:
```bash
# ❌ Wrong
evora schema check myapp.events.UserCreated

# ✅ Correct
evora schema check myapp.events:UserCreated
```

---

### Error: "ClassName not found in module"

**Problem:** Class doesn't exist or isn't imported

**Solution:** Verify the class is defined and exported:
```python
# myapp/events.py
class UserCreated(Event):  # ← Must be at module level
    ...

# Not nested in functions or other classes
```

---

### Error: "ModuleNotFoundError: No module named 'myapp'"

**Problem:** Module not installed or not in PYTHONPATH

**Solution:**
```bash
# Install package in development mode
pip install -e .

# Or set PYTHONPATH
export PYTHONPATH="${PYTHONPATH}:/path/to/project"
evora schema check myapp.events:UserCreated ...
```

---

### False Positive Breaking Changes

**Problem:** Schema shows breaking change but you believe it's compatible

**Possible causes:**

1. **Pydantic version differences** - Different Pydantic versions may generate different schemas
   ```bash
   # Pin Pydantic version
   pip install 'pydantic==2.x.x'
   ```

2. **Complex types not normalized** - Custom types may not be fully resolved
   ```python
   # Prefer explicit types over complex unions
   # ❌ Problematic
   data: dict | CustomType | None
   
   # ✅ Better
   data: dict[str, Any] | None
   ```

---

## 📚 Advanced Usage

### Nested Object Changes

The CLI handles nested schema changes:

```python
# Nested model
class UserInfo(BaseModel):
    id: int
    name: str

# Old
class MyEvent(Event):
    class Data(BaseModel):
        user: UserInfo
    data: Data

# New - added optional field to nested object
class UserInfo(BaseModel):
    id: int
    name: str
    email: str | None = None  # ← nested optional field added

class MyEvent(Event):
    class Data(BaseModel):
        user: UserInfo
    data: Data
```

Output:
```
✅ BACKWARD COMPATIBLE

Non-breaking changes:
  - user.email added as optional
```

---

### Array Item Changes

The CLI validates array item schemas:

```python
# Old
class MyEvent(Event):
    class Data(BaseModel):
        tags: list[str]
    data: Data

# New
class MyEvent(Event):
    class Data(BaseModel):
        tags: list[int]  # ← item type changed
    data: Data
```

Output:
```
❌ BREAKING CHANGES DETECTED

  - tags[]: type changed string → integer
```

---

### Multiple Event Validation Script

Check multiple events in one script:

```python
# check_all_schemas.py
import sys
import subprocess

events = [
    ("myapp.events:UserCreated", "schemas/user_created.json"),
    ("myapp.events:OrderPlaced", "schemas/order_placed.json"),
    ("myapp.events:PaymentReceived", "schemas/payment_received.json"),
]

failed = []
for event_ref, baseline in events:
    result = subprocess.run(
        ["evora", "schema", "check", baseline, event_ref],
        capture_output=True,
    )
    if result.returncode != 0:
        failed.append(event_ref)
        print(f"❌ {event_ref}")
    else:
        print(f"✅ {event_ref}")

if failed:
    print(f"\n❌ {len(failed)} schema(s) failed compatibility check")
    sys.exit(1)
else:
    print(f"\n✅ All schemas are backward compatible")
```

---

## 🎓 Best Practices

### 1. Always Use Version Bump Enforcement

Keep the default `--require-version-bump` enabled:
```bash
# ✅ Good - enforces discipline
evora schema check old new

# ❌ Risky - allows silent breaking changes
evora schema check old new --no-require-version-bump
```

### 2. Store Baselines for Major Versions

Keep baselines for all production versions:
```
schemas/
├── user_created_v1.json  # Production version 1
├── user_created_v2.json  # Production version 2
└── user_created_v3.json  # Production version 3
```

This allows:
- Rollback compatibility checks
- Multi-version consumer validation
- Historical schema tracking

### 3. Check Before Every Deployment

Integrate into CI/CD:
```yaml
# In your deployment pipeline
- name: Validate schemas
  run: make check-schemas
  
# Makefile
check-schemas:
	evora schema check schemas/user_created.json myapp/events.py:UserCreated
	evora schema check schemas/order_placed.json myapp/events.py:OrderPlaced
```

### 4. Document Breaking Changes in Commit Messages

```bash
git commit -m "feat(events)!: change user_id from int to UUID

BREAKING CHANGE: user_id type changed from integer to string (UUID)
- Bumped UserCreated.__version__ from 1 to 2
- Updated schema baseline
- Consumers must handle both formats during migration"
```

### 5. Use Semantic Versioning for Events

```python
class UserCreated(Event):
    __version__ = 2  # ← Semantic: increment for breaking change
    
    class Data(BaseModel):
        user_id: str  # v1: was int, v2: now str (UUID)
        email: str
    
    data: Data
    
    # v1: user_id was int
    # v2: user_id is now str (UUID)
```

### 6. Validate Event Type Consistency

Keep `--enforce-event-type` enabled (default) to prevent accidental renaming:
```python
# Old
@classmethod
def event_type(cls):
    return "users.created"  # ← Changing this is ALWAYS breaking

# New - DO NOT change event_type
@classmethod
def event_type(cls):
    return "users.created"  # ← Must stay the same
```

---

## 🔗 Related Documentation

- **[QUICK_REFERENCE.md](QUICK_REFERENCE.md)** - Event definition and handler patterns
- **[TROUBLESHOOTING.md](TROUBLESHOOTING.md)** - Runtime debugging and diagnostics
- **[CONTRIBUTING.md](../CONTRIBUTING.md)** - How to contribute to Evora

---

## 🎯 Summary

The Schema Governance CLI provides:

✅ **Automated compatibility checking** - No manual schema review needed  
✅ **Version enforcement** - Breaking changes require explicit version bumps  
✅ **CI/CD integration** - Block incompatible changes before deployment  
✅ **Baseline management** - Export and track schema evolution  
✅ **Multiple input formats** - Modules, files, and JSON baselines  

**Next Steps:**
1. Add schema checks to your CI pipeline
2. Export baselines for your production events
3. Require schema validation before merging PRs
4. Document your schema versioning policy

---

**Questions?** Open an issue on GitHub!
