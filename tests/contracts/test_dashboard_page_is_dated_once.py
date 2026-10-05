"""F7 — nothing on the page depends on the day it is READ.

WHY THIS EXISTS. ``page_inputs.py``'s "WHAT THIS CANNOT CATCH" item 4 named this defect and
left it standing: the forecast's finish date and the "last run N days ago" pill were computed
from *today*, so they were wrong the next calendar morning with zero input drift, and
``--check-fresh`` reported the page fresh the whole time. A relative date is a sentence that
rewrites itself, which is the one thing a content digest structurally cannot see.

So the page is dated ONCE, at build, from ``m["generated_at"]`` — and the render path is not
allowed to ask what day it is.
"""

from __future__ import annotations

import ast
import re
from pathlib import Path

from gtm_core import email_campaign_dashboard as gd
from tests.contracts.test_dashboard_ps20_trust import _stats, _strip
from tests.test_email_campaign_dashboard import _seed

PKG = Path(gd.__file__).parent

#: Every module ``render_html`` reaches while composing HTML. The MODEL may read the clock —
#: it is built at a moment and stamps it — but a view may not, or two readers of one file see
#: two different pages. ``provenance.py`` and ``banner.py`` are on the list because both write
#: into the page (the sources table, the stale-banner card) and the banner is the one place whose
#: whole job is "depends on the day it is read", which makes it the likeliest place for a clock
#: call to look innocent. ``health.py`` and ``section_sources.py`` are imported by the views.
_RENDER_PATH = (
    "render.py",
    "freshness.py",
    "forecast.py",
    "format.py",
    "styles.py",
    "filters.py",
    "provenance.py",
    "banner.py",
    "health.py",
    "section_sources.py",
    *(p.name for p in sorted(PKG.glob("views_*.py"))),
)

#: The ONE exemption, named rather than left as a silent hole: ``forecast._page_day`` falls
#: back to ``date.today()`` for a model carrying no readable ``generated_at``. ``build_model``
#: always sets one, so this is unreachable through any real caller — but removing it would
#: make a malformed clock crash the page instead of dating it, which is a worse trade.
_CLOCK_EXEMPT = {
    # ``forecast._page_day`` falls back to ``date.today()`` for a model carrying no readable
    # ``generated_at``. ``build_model`` always sets one, so this is unreachable through any real
    # caller — and removing it would make a malformed clock crash the page instead of dating it,
    # which is a worse trade.
    ("forecast.py", "_page_day"),
}

#: Every spelling of "what time is it" the standard library offers, by the name it has AFTER
#: imports are resolved: ``from datetime import datetime as dt; dt.now()`` and
#: ``import datetime as d; d.datetime.now()`` both land on ``datetime.datetime.now``.
_CLOCK_CALLS = {
    "datetime.datetime.now",
    "datetime.datetime.utcnow",
    "datetime.datetime.today",
    "datetime.date.today",
    "time.time",
    "time.time_ns",
    "time.monotonic",
    "time.monotonic_ns",
    "time.perf_counter",
    "time.perf_counter_ns",
    "time.process_time",
    "time.process_time_ns",
    "time.thread_time",
    "time.thread_time_ns",
    "time.clock_gettime",
    "time.clock_gettime_ns",
    "os.times",
}

#: Functions that read the clock ONLY when they are not handed a time: ``time.gmtime()`` is "now",
#: ``time.gmtime(0)`` is a conversion. The value is how many arguments they can take and still be
#: asking the clock (``time.strftime(fmt)`` has the format; ``time.strftime(fmt, t)`` has a time).
#: A call with a literal ``None`` in the time slot is "now" as well. A reference that is not called
#: here (``now = time.gmtime``) is reported, because it can only be called later without one.
_CLOCK_WHEN_NO_TIME_GIVEN = {
    "time.gmtime": 0,
    "time.localtime": 0,
    "time.ctime": 0,
    "time.asctime": 0,
    "time.strftime": 1,
}

#: Calls that stand for "the module named by this string", for the evasions that skip an import
#: statement: ``__import__('time').time()``, ``importlib.import_module('time')``.
_MODULE_LOADERS = {"__import__", "importlib.import_module"}

#: The old rule — the last two parts of the written name — kept as a second net for the one case
#: import resolution cannot see: a name that arrives through ``from datetime import *`` or is
#: injected, where there is no import statement to resolve it by.
_CLOCK_TAILS = {
    ("datetime", "now"),
    ("datetime", "utcnow"),
    ("datetime", "today"),
    ("date", "today"),
    ("time", "time"),
    ("time", "monotonic"),
    ("time", "perf_counter"),
}


def _aliases(tree: ast.AST) -> dict[str, str]:
    """``{local name: what it stands for}`` from every import statement, at any depth."""
    out: dict[str, str] = {}
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for a in node.names:
                out[a.asname or a.name.split(".")[0]] = a.name if a.asname else a.name.split(".")[0]
        elif isinstance(node, ast.ImportFrom) and node.module:
            for a in node.names:
                if a.name != "*":
                    out[a.asname or a.name] = f"{node.module}.{a.name}"
    return out


def _star_modules(tree: ast.AST) -> list[str]:
    """The modules a ``from <module> import *`` pulls every public name from, so a bare ``time()``
    can be resolved to ``time.time`` when no import statement names it."""
    return [
        node.module
        for node in ast.walk(tree)
        if isinstance(node, ast.ImportFrom)
        and node.module
        and any(a.name == "*" for a in node.names)
    ]


def _str_arg(call: ast.Call, index: int) -> str | None:
    if len(call.args) > index and isinstance(call.args[index], ast.Constant):
        value = call.args[index].value
        return value if isinstance(value, str) else None
    return None


class _ClockVisitor(ast.NodeVisitor):
    """Every clock read in a module, with the innermost scope it sits in — module level, a class
    body, a lambda and a nested function included, which the function-only walk this replaced
    never entered."""

    def __init__(self, aliases: dict[str, str], stars: list[str] | None = None):
        self.aliases = aliases
        self.stars = stars or []
        self.scope = ["<module>"]
        self.called: dict[int, ast.Call] = {}
        self.hits: list[tuple[str, str]] = []

    def _scoped(self, name: str, node: ast.AST) -> None:
        self.scope.append(name)
        self.generic_visit(node)
        self.scope.pop()

    def visit_FunctionDef(self, node):
        self._scoped(node.name, node)

    visit_AsyncFunctionDef = visit_FunctionDef

    def visit_Lambda(self, node):
        self._scoped("<lambda>", node)

    def visit_ClassDef(self, node):
        self._scoped(f"<class {node.name}>", node)

    def _resolve(self, raw: str) -> str:
        head, _, rest = raw.partition(".")
        if head in self.aliases:
            head = self.aliases[head]
        else:
            # A name no import statement binds may come from `from <module> import *`.
            for module in self.stars:
                candidate = f"{module}.{raw}"
                if candidate in _CLOCK_CALLS or candidate in _CLOCK_WHEN_NO_TIME_GIVEN:
                    return candidate
        return head + (f".{rest}" if rest else "")

    def _is_getattr(self, node: ast.AST) -> bool:
        return (
            isinstance(node, ast.Call)
            and isinstance(node.func, ast.Name)
            and node.func.id == "getattr"
            and _str_arg(node, 1) is not None
        )

    def _loaded_module(self, node: ast.AST) -> str | None:
        """The module a ``__import__('m')`` / ``importlib.import_module('m')`` /
        ``sys.modules['m']`` expression names, or None."""
        if isinstance(node, ast.Call):
            callee = self._chain(node.func)
            if callee is not None and self._resolve(callee) in _MODULE_LOADERS:
                return _str_arg(node, 0)
        if isinstance(node, ast.Subscript) and self._chain(node.value) == "sys.modules":
            key = node.slice
            if isinstance(key, ast.Constant) and isinstance(key.value, str):
                return key.value
        return None

    def _chain(self, node: ast.AST) -> str | None:
        """The written name of an attribute chain — through ``getattr(x, 'name')`` and a module
        loaded by name, which a plain attribute walk cannot see — or None."""
        parts: list[str] = []
        while True:
            if isinstance(node, ast.Attribute):
                parts.append(node.attr)
                node = node.value
            elif self._is_getattr(node):
                parts.append(_str_arg(node, 1))
                node = node.args[0]
            else:
                break
        if isinstance(node, ast.Name):
            parts.append(node.id)
        elif (module := self._loaded_module(node)) is not None:
            parts.append(module)
        else:
            return None
        return ".".join(reversed(parts))

    def _reads_clock(self, node: ast.AST, resolved: str) -> bool:
        if resolved in _CLOCK_CALLS:
            return True
        limit = _CLOCK_WHEN_NO_TIME_GIVEN.get(resolved)
        if limit is None:
            return False
        call = self.called.get(id(node))
        if call is None:
            return True  # a bare reference: it can only be called later, and without the time
        given = len(call.args) + len(call.keywords)
        if given <= limit:
            return True
        # the time slot holds a literal None: that is "now" too
        slot = call.args[limit] if len(call.args) > limit else None
        return isinstance(slot, ast.Constant) and slot.value is None and given == limit + 1

    def _check(self, node: ast.AST) -> None:
        raw = self._chain(node)
        if raw is None:
            return
        resolved = self._resolve(raw)
        tail = tuple(raw.split(".")[-2:])
        if self._reads_clock(node, resolved) or tail in _CLOCK_TAILS:
            self.hits.append((self.scope[-1], raw + ("()" if id(node) in self.called else "")))

    def visit_Call(self, node):
        self.called[id(node.func)] = node
        if self._is_getattr(node):
            self._check(node)  # `getattr(datetime, 'now')` is itself the callee of an outer call
        self.generic_visit(node)

    def visit_Attribute(self, node):
        self._check(node)
        # A banned name is the OUTERMOST attribute chain; its inner prefixes are not re-checked
        # (`datetime.datetime` is not a clock). Descend only past a chain we did not report.
        if self._chain(node) is None:
            self.generic_visit(node)

    def visit_Name(self, node):
        self._check(node)


def _clock_reads(path: Path) -> list[str]:
    """``<scope>: <expression>`` for every wall-clock read in ``path``, by AST — not a grep, so a
    call inside a docstring or a comment cannot trip it — with import aliases RESOLVED, so a
    renamed import cannot hide it, and including a bare REFERENCE (``default_factory=datetime.now``,
    ``now = time.monotonic``), which reads the clock later but reads it all the same.

    Not caught, stated rather than implied: ``getattr(datetime, "now")()`` and a clock handed in
    as an argument. The first is a deliberate evasion no gate here is meant to stop; the second is
    the SANCTIONED shape (``now`` is a parameter)."""
    tree = ast.parse(path.read_text(encoding="utf-8"))
    walker = _ClockVisitor(_aliases(tree), _star_modules(tree))
    walker.visit(tree)
    return [
        f"{scope}: {expr}" for scope, expr in walker.hits if (path.name, scope) not in _CLOCK_EXEMPT
    ]


def test_no_view_asks_what_day_it_is(tmp_path):
    """T36's structural half, which is the half that can discriminate: a render that reads the
    wall clock only shows it on the day the two disagree, so a value test would pass almost
    always. ``views_ops`` is named in the plan; every sibling on the render path is held to
    the same rule, because the next relative date will not be written in the module that had
    the last one."""
    offenders = {name: _clock_reads(PKG / name) for name in _RENDER_PATH}
    assert {k: v for k, v in offenders.items() if v} == {}


def test_the_checker_catches_a_clock_read(tmp_path):
    """NEGATIVE CONTROL. Without this the AST walk above could match nothing at all — a
    renamed constant or a wrong node type — and pass on every module forever."""
    probe = tmp_path / "views_probe.py"
    probe.write_text(
        "from datetime import UTC, datetime\n\n\ndef _block(m):\n    return datetime.now(UTC)\n",
        encoding="utf-8",
    )
    assert _clock_reads(probe) == ["_block: datetime.now()"]


#: Each spelling the red team planted (and the ones it did not think of), as the SOURCE of a
#: module the gate must convict. Mutation caught for every row: delete the import-alias
#: resolution, the scope handling for that row, or the clock name from ``_CLOCK_CALLS`` and the
#: row's module reads clean.
_EVASIONS = {
    "aliased class import": (
        "from datetime import datetime as dt\n\n\ndef f():\n    return dt.now()\n",
        ["f: dt.now()"],
    ),
    "module-qualified": (
        "import datetime\n\n\ndef f():\n    return datetime.datetime.now()\n",
        ["f: datetime.datetime.now()"],
    ),
    "module alias": (
        "import datetime as d\n\n\ndef f():\n    return d.datetime.now()\n",
        ["f: d.datetime.now()"],
    ),
    "utcnow": (
        "from datetime import datetime\n\n\ndef f():\n    return datetime.utcnow()\n",
        ["f: datetime.utcnow()"],
    ),
    "today on the class": (
        "from datetime import datetime\n\n\ndef f():\n    return datetime.today()\n",
        ["f: datetime.today()"],
    ),
    "date today": (
        "from datetime import date as day\n\n\ndef f():\n    return day.today()\n",
        ["f: day.today()"],
    ),
    "time.time": (
        "import time\n\n\ndef f():\n    return time.time()\n",
        ["f: time.time()"],
    ),
    "time.monotonic": (
        "import time\n\n\ndef f():\n    return time.monotonic()\n",
        ["f: time.monotonic()"],
    ),
    "time.perf_counter": (
        "import time\n\n\ndef f():\n    return time.perf_counter()\n",
        ["f: time.perf_counter()"],
    ),
    "from-imported and renamed function": (
        "from time import time as clock\n\n\ndef f():\n    return clock()\n",
        ["f: clock()"],
    ),
    "from-imported function": (
        "from time import monotonic\n\n\ndef f():\n    return monotonic()\n",
        ["f: monotonic()"],
    ),
    "module level": (
        "from datetime import datetime\n\nBUILT = datetime.now()\n",
        ["<module>: datetime.now()"],
    ),
    "class body": (
        "from datetime import datetime\n\n\nclass Page:\n    built = datetime.now()\n",
        ["<class Page>: datetime.now()"],
    ),
    "lambda": (
        "from datetime import datetime\n\nf = lambda: datetime.now()\n",
        ["<lambda>: datetime.now()"],
    ),
    "nested function reports the inner scope": (
        "from datetime import datetime\n\n\ndef outer():\n    def inner():\n"
        "        return datetime.now()\n    return inner\n",
        ["inner: datetime.now()"],
    ),
    "a reference, called later": (
        "from datetime import datetime\nfrom dataclasses import field\n\n\n"
        "def f():\n    return field(default_factory=datetime.now)\n",
        ["f: datetime.now"],
    ),
    "a reference bound to a name": (
        "import time\n\n\ndef f():\n    now = time.monotonic\n    return now()\n",
        ["f: time.monotonic"],
    ),
    "star import falls back to the written name": (
        "from datetime import *\n\n\ndef f():\n    return datetime.now()\n",
        ["f: datetime.now()"],
    ),
    # --- round 2 (2026-10-02): what the first gate still let through --------------------------
    "time.gmtime with no argument": (
        "import time\n\n\ndef f():\n    return time.gmtime()\n",
        ["f: time.gmtime()"],
    ),
    "time.localtime with no argument": (
        "import time\n\n\ndef f():\n    return time.localtime()\n",
        ["f: time.localtime()"],
    ),
    "time.localtime(None)": (
        "import time\n\n\ndef f():\n    return time.localtime(None)\n",
        ["f: time.localtime()"],
    ),
    "time.strftime with a format only": (
        "import time\n\n\ndef f():\n    return time.strftime('%Y-%m-%d')\n",
        ["f: time.strftime()"],
    ),
    "time.strftime with a keyword format only": (
        "import time\n\n\ndef f():\n    return time.strftime(format='%Y')\n",
        ["f: time.strftime()"],
    ),
    "time.ctime with no argument": (
        "import time\n\n\ndef f():\n    return time.ctime()\n",
        ["f: time.ctime()"],
    ),
    "time.asctime with no argument": (
        "import time\n\n\ndef f():\n    return time.asctime()\n",
        ["f: time.asctime()"],
    ),
    "time.process_time": (
        "import time\n\n\ndef f():\n    return time.process_time()\n",
        ["f: time.process_time()"],
    ),
    "a conditional reader bound to a name": (
        "import time\n\n\ndef f():\n    now = time.gmtime\n    return now()\n",
        ["f: time.gmtime"],
    ),
    "os.times": (
        "import os\n\n\ndef f():\n    return os.times()\n",
        ["f: os.times()"],
    ),
    "os.times from-imported": (
        "from os import times\n\n\ndef f():\n    return times()\n",
        ["f: times()"],
    ),
    "__import__ of the module": (
        "def f():\n    return __import__('time').time()\n",
        ["f: time.time()"],
    ),
    "__import__ of datetime": (
        "def f():\n    return __import__('datetime').datetime.now()\n",
        ["f: datetime.datetime.now()"],
    ),
    "importlib.import_module": (
        "import importlib\n\n\ndef f():\n"
        "    return importlib.import_module('datetime').datetime.now()\n",
        ["f: datetime.datetime.now()"],
    ),
    "import_module from-imported and renamed": (
        "from importlib import import_module as im\n\n\ndef f():\n    return im('time').time()\n",
        ["f: time.time()"],
    ),
    "sys.modules lookup": (
        "import sys\n\n\ndef f():\n    return sys.modules['time'].time()\n",
        ["f: time.time()"],
    ),
    "getattr on the class": (
        "from datetime import datetime\n\n\ndef f():\n    return getattr(datetime, 'now')()\n",
        ["f: datetime.now()"],
    ),
    "getattr on a module": (
        "import time\n\n\ndef f():\n    return getattr(time, 'monotonic')()\n",
        ["f: time.monotonic()"],
    ),
    "getattr on an imported module": (
        "def f():\n    return getattr(__import__('time'), 'time')()\n",
        ["f: time.time()"],
    ),
    "from time import *": (
        "from time import *\n\n\ndef f():\n    return time()\n",
        ["f: time()"],
    ),
    "from time import * then a conditional reader": (
        "from time import *\n\n\ndef f():\n    return gmtime()\n",
        ["f: gmtime()"],
    ),
}


def test_every_evasion_the_red_team_found_is_now_caught(tmp_path):
    for label, (src, want) in _EVASIONS.items():
        probe = tmp_path / "views_evasion.py"
        probe.write_text(src, encoding="utf-8")
        assert _clock_reads(probe) == want, label


def test_the_checker_does_not_convict_what_is_not_a_clock_read(tmp_path):
    """The other half: a gate that flags everything is a gate nobody reads. A clock passed IN as
    an argument is the sanctioned shape, and arithmetic or parsing of a datetime is not a read."""
    probe = tmp_path / "views_clean.py"
    probe.write_text(
        '"""Mentions datetime.now() and time.time() in prose only."""\n'
        "from datetime import UTC, date, datetime, timedelta\nimport time\n\n\n"
        "# datetime.now() in a comment\n"
        "def f(now: datetime, text: str):\n"
        '    "datetime.utcnow()"\n'
        "    when = datetime.fromisoformat(text)\n"
        "    day = date.fromisoformat(text[:10])\n"
        "    gap = now - when + timedelta(days=1)\n"
        "    stamp = time.strftime('%Y', time.gmtime(0))\n"
        "    return gap, day, stamp, datetime(2026, 1, 1, tzinfo=UTC)\n",
        encoding="utf-8",
    )
    assert _clock_reads(probe) == []


def test_the_new_spellings_do_not_convict_their_innocent_forms(tmp_path):
    """No-false-positive pair for every round-2 evasion: the same function WITH its time argument
    reads no clock (``gmtime(0)`` is a conversion), a ``getattr`` on something that is not a clock
    is not one, a star import with no clock call is fine, and a module imported by name for a
    reason that is not the time is fine. A gate that convicted these would be disabled by the
    first person it got wrong."""
    clean = {
        "gmtime with an argument": "import time\n\n\ndef f(t):\n    return time.gmtime(t)\n",
        "localtime with an argument": "import time\n\n\ndef f(t):\n    return time.localtime(t)\n",
        "ctime with an argument": "import time\n\n\ndef f(t):\n    return time.ctime(t)\n",
        "asctime with an argument": "import time\n\n\ndef f(t):\n    return time.asctime(t)\n",
        "strftime with a time tuple": (
            "import time\n\n\ndef f(t):\n    return time.strftime('%Y', t)\n"
        ),
        "strftime with a keyword time tuple": (
            "import time\n\n\ndef f(t):\n    return time.strftime('%Y', t=t)\n"
        ),
        "getattr on a non-clock": ("def f(obj):\n    return getattr(obj, 'now')()\n"),
        "getattr with a dynamic name": (
            "from datetime import datetime\n\n\ndef f(name):\n    return getattr(datetime, name)\n"
        ),
        "__import__ of a non-clock module": ("def f(s):\n    return __import__('json').loads(s)\n"),
        "import_module of a non-clock module": (
            "import importlib\n\n\ndef f():\n    return importlib.import_module('json').dumps({})\n"
        ),
        "sys.modules of a non-clock module": (
            "import sys\n\n\ndef f():\n    return sys.modules['json']\n"
        ),
        "a star import that only sleeps": ("from time import *\n\n\ndef f():\n    sleep(1)\n"),
        "a local named like a time function": (
            "def time_of(x):\n    return x\n\n\ndef f(x):\n    return time_of(x)\n"
        ),
        "os without the clock": "import os\n\n\ndef f(p):\n    return os.path.join(p, 'x')\n",
    }
    for label, src in clean.items():
        probe = tmp_path / "views_clean.py"
        probe.write_text(src, encoding="utf-8")
        assert _clock_reads(probe) == [], label


def test_the_exemptions_are_scoped_to_the_named_function_only(tmp_path):
    """An exempt name does not exempt its module: a second clock read beside the sanctioned one
    is still convicted, because the exemption is (module, function), not module."""
    probe = tmp_path / "forecast.py"
    probe.write_text(
        "from datetime import date, datetime\n\n\n"
        "def _page_day(m):\n    return date.today()\n\n\n"
        "def _other(m):\n    return datetime.now()\n",
        encoding="utf-8",
    )
    assert _clock_reads(probe) == ["_other: datetime.now()"]


def test_the_render_path_names_the_modules_that_write_into_the_page():
    """Mutation caught: dropping ``provenance.py`` or ``banner.py`` from the list — the gate
    would stop reading the two modules whose job is to date things."""
    for name in ("provenance.py", "banner.py", "views_ops.py", "render.py", "forecast.py"):
        assert name in _RENDER_PATH, name
        assert (PKG / name).is_file(), name


def test_rendering_the_same_model_twice_is_byte_identical(tmp_path):
    """T36's value half. Weak on its own (it only convicts a clock read that straddles a
    boundary between the two calls) and kept anyway as the positive control that
    ``render_html`` is a pure function of the model — the property the AST check above is
    only evidence FOR."""
    profile = _seed(tmp_path)
    m = gd.build_model(profile, tmp_path)
    assert gd.render_html(m) == gd.render_html(m)


def test_the_last_run_pill_shows_a_date_not_an_age(tmp_path):
    """T37 — "last run 7 days ago" was the live example in ``page_inputs``'s docstring. The
    run's own date is on the row beside it; the pill restating it as an age was the only thing
    on the page that changed overnight."""
    import json

    profile = _seed(tmp_path)
    with (tmp_path / profile / "history.jsonl").open("a", encoding="utf-8") as fh:
        fh.write(
            json.dumps(
                {"event": "prospect_run", "ts": "2026-08-01T09:00:00Z", "market": "SG", "total": 12}
            )
            + "\n"
        )
    page = gd.render_html(gd.build_model(profile, tmp_path))
    assert '<span class="pill">last run 2026-08-01</span>' in page


def test_no_relative_day_count_survives_outside_the_header(tmp_path):
    """T37's sweep. The header's own "(N days old)" is the one sanctioned age on the page: it
    is explicitly frozen at build and the sources table says so. Anything else that counts
    days from today is a sentence that will be wrong tomorrow."""
    profile = _seed(tmp_path)
    _stats(tmp_path, profile, '{"fetched": "2026-09-01", "sequences": [{"id": "S1"}]}')
    page = gd.render_html(gd.build_model(profile, tmp_path))
    header = _strip(page).split('style="margin:0">', 1)[1].split("</p>", 1)[0]
    body = page.replace(header, "")
    assert re.search(r"\d+ days? old", header), "the header's own sanctioned age is missing"
    leaks = re.findall(r"[^<>]{0,40}\b\d+ (?:days?|weeks?|months?) ago\b", body)
    assert leaks == []
    assert not re.search(r"\b\d+ days? old\b", body)


def test_the_forecast_names_the_start_date_it_assumes(tmp_path):
    """T37 — "Dates assume it starts on the next working day" is a relative date wearing a
    sentence: which working day depends on when you read it. Every "Done by" in the table is
    arithmetic over that start, so the start is what has to be printed (§R14: the date is
    derived from the model's clock, never typed)."""
    from datetime import date, timedelta

    from gtm_core.email_campaign_dashboard.forecast import _schedule, when_done
    from tests.contracts.test_dashboard_ps20_forecast import _clocked
    from tests.test_email_campaign_dashboard import _seed_operational

    # 25 Sep 2026 is a Friday, so the first sending day is Monday 28 Sep — the same fixture
    # clock tests/contracts/test_dashboard_ps20_forecast.py pins its "Done by" dates against.
    m = _clocked(gd.build_model(_seed_operational(tmp_path), tmp_path))
    built = date.fromisoformat(str(m["generated_at"])[:10])
    expected = built + timedelta(days=1)
    while expected.weekday() >= 5:
        expected += timedelta(days=1)
    assert expected.isoformat() == "2026-09-28"  # the fixture clock, stated so a drift is loud

    s = _schedule(m)
    assert s["starts"] == expected.isoformat()
    sentence, why = when_done(m)
    assert why is None
    assert expected.isoformat() in sentence
    from gtm_core.email_campaign_dashboard.forecast import _forecast_block

    assert expected.isoformat() in _forecast_block(m)


# --- T36's value half, the one the plan named and nobody wrote -------------------------------


def _freeze(monkeypatch, when):
    """Pin EVERY clock the package can reach to ``when[0]`` (a one-item list the caller moves
    between builds), not only the one ``build_model`` reads:
    ``datetime.now``/``utcnow``/``today`` and ``date.today`` in every ``gtm_core`` module that
    imported them by name, and ``time.time``/``monotonic``. Freezing only the model's clock would
    make a view that reads ``date.today()`` look innocent, because the real clock does not move
    between the two builds of one test run — the property would hold for the wrong reason."""
    import sys
    import time as time_mod
    from datetime import date as real_date
    from datetime import datetime as real_dt

    class FrozenDate(real_date):
        @classmethod
        def today(cls):
            return cls(when[0].year, when[0].month, when[0].day)

    class FrozenDatetime(real_dt):
        @classmethod
        def now(cls, tz=None):
            return when[0] if tz else when[0].replace(tzinfo=None)

        @classmethod
        def utcnow(cls):
            return when[0].replace(tzinfo=None)

        @classmethod
        def today(cls):
            return when[0].replace(tzinfo=None)

    for name, mod in list(sys.modules.items()):
        if not name.startswith("gtm_core") or mod is None:
            continue
        if getattr(mod, "datetime", None) is real_dt:
            monkeypatch.setattr(mod, "datetime", FrozenDatetime)
        if getattr(mod, "date", None) is real_date:
            monkeypatch.setattr(mod, "date", FrozenDate)
    # A function-local `import datetime` reaches the stdlib module itself, not a name already bound
    # in a gtm_core module, so the module's own attributes are pinned too.
    import datetime as dt_mod

    monkeypatch.setattr(dt_mod, "datetime", FrozenDatetime)
    monkeypatch.setattr(dt_mod, "date", FrozenDate)
    monkeypatch.setattr(time_mod, "time", lambda: when[0].timestamp())
    monkeypatch.setattr(time_mod, "monotonic", lambda: when[0].timestamp())


def _build_at(clock, tmp_path, profile, when):
    """The model as built at the frozen instant ``when``."""
    clock[0] = when
    return gd.build_model(profile, tmp_path)


def test_a_page_built_on_two_different_days_differs_only_in_what_the_clock_feeds(
    tmp_path, monkeypatch
):
    """T36's value half: same inputs, two build days, frozen clocks. The pages may differ only in
    the header — "Page built <stamp>" and the "(N days old)" derived from it — and everything
    else is byte-identical, so no sentence in the body reads a clock the model did not hand it.

    The AST gate above is the discriminating half (a read shows only on the day two reads
    disagree); this is the positive control that the property it protects actually holds on a
    real page, not just on the modules it can see. Mutation caught: any view computing a date or
    an age from today instead of ``m["generated_at"]`` — the body diff is then non-empty.
    """
    from datetime import UTC, datetime

    profile = _seed(tmp_path)
    _stats(tmp_path, profile, '{"fetched": "2026-09-25", "sequences": [{"id": "S1", "sent": 3}]}')
    clock = [datetime(2026, 9, 26, 9, 1, tzinfo=UTC)]
    _freeze(monkeypatch, clock)
    day_one = _build_at(clock, tmp_path, profile, datetime(2026, 9, 26, 9, 1, tzinfo=UTC))
    one = gd.render_html(day_one)
    day_two = _build_at(clock, tmp_path, profile, datetime(2026, 9, 28, 17, 45, tzinfo=UTC))
    assert day_one["generated_at"] != day_two["generated_at"], "the clock did not freeze"
    two = gd.render_html(day_two)
    assert one != two

    def header(page: str) -> str:
        return page.split('<p class="muted" style="margin:0">', 1)[1].split("</p>", 1)[0]

    h1, h2 = header(one), header(two)
    assert "Page built 2026-09-26 09:01 UTC" in h1 and "(1 day old" in h1
    assert "Page built 2026-09-28 17:45 UTC" in h2 and "(3 days old" in h2
    body_one, body_two = one.replace(h1, "@@HEADER@@"), two.replace(h2, "@@HEADER@@")
    if body_one != body_two:
        import difflib

        diff = [
            ln
            for ln in difflib.unified_diff(
                body_one.splitlines(), body_two.splitlines(), lineterm="", n=0
            )
            if ln[:1] in "+-" and ln[:3] not in ("+++", "---")
        ]
        raise AssertionError(
            "the page body differs between two build days:\n" + "\n".join(diff[:20])
        )


def test_every_exemption_still_matches_a_real_clock_read():
    """An exemption that matches nothing is a hole left open for the next person: it would let a
    NEW clock read in that function through unseen. Each (module, function) named here must
    currently contain a read the gate would otherwise convict."""
    for module, func in _CLOCK_EXEMPT:
        tree = ast.parse((PKG / module).read_text(encoding="utf-8"))
        walker = _ClockVisitor(_aliases(tree), _star_modules(tree))
        walker.visit(tree)
        assert any(scope == func for scope, _expr in walker.hits), (module, func)
    assert _CLOCK_EXEMPT, "an empty list should delete the machinery, not keep it"
