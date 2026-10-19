"""Benchmark tasks: a bug injected into a copy of standard-library packages.

Each task names a symptom, never the file. Success is judged by hidden CPython tests that the
agent cannot see (the workspace contains no tests).
"""

from __future__ import annotations

from dataclasses import dataclass, field

CORPUS_PACKAGES = ["email", "logging", "http", "asyncio"]


@dataclass(frozen=True)
class Task:
    id: str
    file: str  # file the bug is injected into (relative to the workspace)
    old: str  # exact text replaced; must occur once
    new: str
    symptom: str
    repro: str  # Python snippet printing the observable behaviour
    hidden_tests: list[str] = field(default_factory=list)  # unittest ids that must pass after the fix
    regression_tests: list[str] = field(default_factory=list)  # broader suites that must keep passing
    navigation: str = ""  # what finding the root cause takes (for the report)


TASKS: list[Task] = [
    Task(
        id="email-rfc2231-filename",
        file="email/utils.py",
        old="        return str(rawbytes, charset, errors)\n",
        new="        return str(rawbytes, fallback_charset, errors)\n",
        symptom=(
            "Attachment filenames encoded per RFC 2231 come back garbled. For a message part with the header\n"
            "`Content-Disposition: attachment; filename*=utf-8''%E2%82%AC%20rates.txt`\n"
            "`msg.get_filename()` returns '\\ufffd\\ufffd\\ufffd rates.txt' instead of '€ rates.txt'."
        ),
        repro=(
            "import email\n"
            "msg = email.message_from_string(\"Content-Type: text/plain\\n\"\n"
            "    \"Content-Disposition: attachment; filename*=utf-8''%E2%82%AC%20rates.txt\\n\\nx\\n\")\n"
            "print(repr(msg.get_filename()))\n"
        ),
        hidden_tests=["test_bench_rfc2231", "test.test_email.test_email.TestRFC2231"],
        regression_tests=["test.test_email"],
        navigation="public API in message.py, root cause in a helper in utils.py (2 files)",
    ),
    Task(
        id="email-address-quotes",
        file="email/_header_value_parser.py",
        old="    return str(value).replace('\\\\', '\\\\\\\\').replace('\"', '\\\\\"')\n",
        new="    return str(value).replace('\\\\', '\\\\\\\\')\n",
        symptom=(
            "Formatting an address whose display name contains double quotes produces an invalid header:\n"
            "`str(email.headerregistry.Address('John \"Jack\" Doe', 'jdoe', 'example.com'))` returns\n"
            "'\"John \"Jack\" Doe\" <jdoe@example.com>' - the inner quotes are not escaped."
        ),
        repro=(
            "from email.headerregistry import Address\n"
            "print(str(Address('John \"Jack\" Doe', 'jdoe', 'example.com')))\n"
        ),
        hidden_tests=["test.test_email.test_headerregistry", "test.test_email.test__header_value_parser"],
        regression_tests=["test.test_email"],
        navigation="headerregistry.Address -> quote_string -> make_quoted_pairs in _header_value_parser.py",
    ),
    Task(
        id="logging-rotating-backups",
        file="logging/handlers.py",
        old="            for i in range(self.backupCount - 1, 0, -1):\n",
        new="            for i in range(self.backupCount - 1, 1, -1):\n",
        symptom=(
            "RotatingFileHandler loses old log files. With backupCount=3, after several rollovers only\n"
            "app.log and app.log.1 exist; app.log.2 and app.log.3 are never kept."
        ),
        repro=(
            "import logging, logging.handlers, os, tempfile\n"
            "d = tempfile.mkdtemp(); p = os.path.join(d, 'app.log')\n"
            "h = logging.handlers.RotatingFileHandler(p, maxBytes=1, backupCount=3)\n"
            "lg = logging.getLogger('bench'); lg.addHandler(h); lg.propagate = False\n"
            "for i in range(6): lg.warning('record %d', i)\n"
            "h.close(); print(sorted(os.listdir(d)))\n"
        ),
        hidden_tests=["test.test_logging.RotatingFileHandlerTest"],
        regression_tests=["test.test_logging"],
        navigation="single class in a large module (handlers.py)",
    ),
    Task(
        id="logging-config-child-propagate",
        file="logging/config.py",
        old="                logger.handlers = []\n                logger.propagate = True\n",
        new="                logger.handlers = []\n                logger.propagate = False\n",
        symptom=(
            "After `logging.config.dictConfig()` configures the logger 'app', a logger 'app.db' that was\n"
            "created before the call stops propagating: its records no longer reach the handlers of 'app'\n"
            "(`logging.getLogger('app.db').propagate` is False)."
        ),
        repro=(
            "import logging, logging.config\n"
            "db = logging.getLogger('app.db')\n"
            "logging.config.dictConfig({'version': 1,\n"
            "    'handlers': {'s': {'class': 'logging.StreamHandler', 'stream': 'ext://sys.stdout'}},\n"
            "    'loggers': {'app': {'handlers': ['s'], 'level': 'INFO'}}})\n"
            "db.warning('hello from app.db')\n"
            "print('propagate =', db.propagate)\n"
        ),
        hidden_tests=["test.test_logging.ConfigDictTest", "test.test_logging.ConfigFileTest"],
        regression_tests=["test.test_logging"],
        navigation="dictConfig -> DictConfigurator.configure -> _handle_existing_loggers (one large file)",
    ),
    Task(
        id="http-cookie-octal",
        file="http/cookies.py",
        old="        return chr(int(m[1], 8))\n",
        new="        return chr(int(m[1], 10))\n",
        symptom=(
            "SimpleCookie decodes escape sequences in quoted cookie values wrongly: after\n"
            "`c = SimpleCookie(); c.load('k=\"a\\\\073b\"')`, `c['k'].value` is 'aIb' instead of 'a;b'."
        ),
        repro=(
            "from http.cookies import SimpleCookie\n"
            "c = SimpleCookie(); c.load('k=\"a\\\\073b\"'); print(repr(c['k'].value))\n"
        ),
        hidden_tests=["test.test_http_cookies"],
        regression_tests=["test.test_http_cookies"],
        navigation="control task: small single module",
    ),
    Task(
        id="asyncio-timeout-expired",
        file="asyncio/timeouts.py",
        old="        return self._state in (_State.EXPIRING, _State.EXPIRED)\n",
        new="        return self._state is _State.EXPIRING\n",
        symptom=(
            "After an `async with asyncio.timeout(0.01) as cm:` block times out (the TimeoutError is caught\n"
            "outside the block), `cm.expired()` returns False although the timeout did fire."
        ),
        repro=(
            "import asyncio\n"
            "async def main():\n"
            "    try:\n"
            "        async with asyncio.timeout(0.01) as cm:\n"
            "            await asyncio.sleep(1)\n"
            "    except TimeoutError:\n"
            "        pass\n"
            "    print('expired =', cm.expired())\n"
            "asyncio.run(main())\n"
        ),
        hidden_tests=["test.test_asyncio.test_timeouts"],
        regression_tests=["test.test_asyncio.test_timeouts", "test.test_asyncio.test_waitfor"],
        navigation="control task: API name points at a small module in a 35-file package",
    ),
]

TASKS_BY_ID = {t.id: t for t in TASKS}

PROMPT_TEMPLATE = """You are working in a repository that contains copies of several Python standard-library \
packages: email/, logging/, http/ and asyncio/. Run Python as `python3` from the repository root; the copies \
in this directory shadow the installed standard library.

Bug report:
{symptom}

Find the root cause in this repository's source code and fix it with a minimal change. Rules:
- Do not read, search or compare with the installed Python standard library or any other copy of these \
packages outside this directory.
- Do not add test files.
- When finished, reply with a one-sentence summary of the fix."""

MAP_SYSTEM_PROMPT = """This repository has a Method Project Map: a statically built index of its modules, classes, \
functions and the call/import relations between them. Using it is optional - use it when it saves you \
searching or reading files. Commands (run from the repository root):
- `pmap context . --max-chars 6000` - overview: modules with their top-level classes/functions and line ranges
- `pmap find <name>` - locate a class, function or method by name (also `Class.method`)
- `pmap show <node-id | file path>` - one node: file and line range, signature, what it depends on and who \
uses it (callers)
Edge confidence: `resolved` = statically proven; `inferred` = candidates, verify in code; `unresolved` = \
target unknown. The map is rebuilt automatically when files change."""
