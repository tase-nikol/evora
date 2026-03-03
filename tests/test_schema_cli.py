import json
import subprocess
import sys
from pathlib import Path
import textwrap


def write_file(path: Path, content: str):
    path.write_text(textwrap.dedent(content).strip() + "\n", encoding="utf-8")

# ---------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------

def run_cli(args, cwd):
    cmd = [sys.executable, "-m", "evora.cli"] + args
    return subprocess.run(
        cmd,
        cwd=cwd,
        capture_output=True,
        text=True,
    )


# ---------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------

def test_breaking_change_requires_version_bump(tmp_path):
    v1 = tmp_path / "v1.py"
    v2 = tmp_path / "v2.py"

    write_file(v1, """
    from evora.core import Event
    from pydantic import BaseModel
    
    class MyEvent(Event):
        __version__ = 1
    
        class Data(BaseModel):
            user_id: int
    
        data: Data
    
        @classmethod
        def event_type(cls):
            return "users.events"
    """)

    write_file(v2, """
    from evora.core import Event
    from pydantic import BaseModel
    
    class MyEvent(Event):
        __version__ = 1  # NOT incremented
    
        class Data(BaseModel):
            user_id: str  # breaking change
    
        data: Data
    
        @classmethod
        def event_type(cls):
            return "users.events"
    """)

    result = run_cli(
        ["schema", "check", f"{v1}:MyEvent", f"{v2}:MyEvent", "--format", "json"],
        cwd=tmp_path,
    )

    assert result.returncode == 1
    payload = json.loads(result.stdout)

    assert payload["ok"] is False
    assert "requires __version__ bump" in payload["error"]


def test_baseline_export_and_check(tmp_path):
    v1 = tmp_path / "v1.py"
    baseline = tmp_path / "baseline.json"

    write_file(v1, """
    from evora.core import Event
    from pydantic import BaseModel
    
    class MyEvent(Event):
        __version__ = 1
    
        class Data(BaseModel):
            user_id: int
    
        data: Data
    
        @classmethod
        def event_type(cls):
            return "users.events"
    """)

    # Export baseline
    export = run_cli(
        ["schema", "export", f"{v1}:MyEvent", "-o", str(baseline)],
        cwd=tmp_path,
    )

    assert export.returncode == 0
    assert baseline.exists()

    # Check against itself → compatible
    check = run_cli(
        ["schema", "check", str(baseline), f"{v1}:MyEvent", "--format", "json"],
        cwd=tmp_path,
    )

    assert check.returncode == 0
    payload = json.loads(check.stdout)
    assert payload["ok"] is True


def test_event_type_mismatch_fails(tmp_path):
    v1 = tmp_path / "v1.py"
    v2 = tmp_path / "v2.py"

    write_file(v1, """
    from evora.core import Event
    from pydantic import BaseModel
    
    class A(Event):
        __version__ = 1
    
        class Data(BaseModel):
            x: int
    
        data: Data
    
        @classmethod
        def event_type(cls):
            return "a.events"
    """)

    write_file(v2, """
    from evora.core import Event
    from pydantic import BaseModel
    
    class B(Event):
        __version__ = 1
    
        class Data(BaseModel):
            x: int
    
        data: Data
    
        @classmethod
        def event_type(cls):
            return "b.events"
    """)

    result = run_cli(
        ["schema", "check", f"{v1}:A", f"{v2}:B", "--format", "json"],
        cwd=tmp_path,
    )

    assert result.returncode == 1
    payload = json.loads(result.stdout)
    assert "event_type mismatch" in payload["error"]


def test_non_breaking_change_is_ok(tmp_path):
    v1 = tmp_path / "v1.py"
    v2 = tmp_path / "v2.py"

    write_file(v1, """
    from evora.core import Event
    from pydantic import BaseModel
    
    class MyEvent(Event):
        __version__ = 1
    
        class Data(BaseModel):
            user_id: int
    
        data: Data
    
        @classmethod
        def event_type(cls):
            return "users.events"
    """)

    write_file(v2, """
    from evora.core import Event
    from pydantic import BaseModel
    
    class MyEvent(Event):
        __version__ = 2  # bumped
    
        class Data(BaseModel):
            user_id: int
            nickname: str | None = None
    
        data: Data
    
        @classmethod
        def event_type(cls):
            return "users.events"
    """)

    result = run_cli(
        ["schema", "check", f"{v1}:MyEvent", f"{v2}:MyEvent", "--format", "json"],
        cwd=tmp_path,
    )

    assert result.returncode == 0
    payload = json.loads(result.stdout)
    assert payload["ok"] is True