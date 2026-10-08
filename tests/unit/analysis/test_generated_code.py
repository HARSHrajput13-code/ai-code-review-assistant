"""Generated-code validation: the bounded parse and public-interface preservation (CIS §14.3, D-60).

Generated code is parsed for inspection only. It is never executed, imported or compiled.
"""

import sys
from pathlib import Path

import pytest

from analysis.python.syntax import DOES_NOT_PARSE, INTERFACE_CHANGED, validate_generated_code
from shared.domain.models import CodeValidation, SourceText

ORIGINAL = SourceText.of(
    "import os\n\n\n"
    "def load(path, mode='r', *args, encoding=None, **options):\n"
    "    return open(path, mode)\n\n\n"
    "def only(a, b, /, c):\n"
    "    return a\n\n\n"
    "def _helper(x):\n"
    "    return x\n\n\n"
    "class Store:\n"
    "    def __init__(self, root, *, cache=False):\n"
    "        self.root = root\n\n"
    "    @staticmethod\n"
    "    def build(name):\n"
    "        return name\n\n"
    "    @property\n"
    "    def size(self):\n"
    "        return 0\n\n"
    "    async def fetch(self, key):\n"
    "        return key\n\n"
    "    def _internal(self):\n"
    "        return None\n"
)


def changed(old: str, new: str) -> CodeValidation:
    assert old in ORIGINAL.text
    return validate_generated_code(ORIGINAL, ORIGINAL.text.replace(old, new, 1))


VALID = CodeValidation(valid=True, reason=None)
REJECTED = CodeValidation(valid=False, reason=INTERFACE_CHANGED)


def test_unchanged_interface_with_new_bodies_is_valid() -> None:
    body = "    with open(path, mode) as f:\n        return f.read()"
    assert changed("    return open(path, mode)", body) == VALID
    assert validate_generated_code(ORIGINAL, "# Reviewed\n" + ORIGINAL.text) == VALID


@pytest.mark.parametrize(
    ("old", "new"),
    [
        ("def load(", "def read("),  # renamed public function
        ("def only(a, b, /, c)", "def only(b, a, /, c)"),  # reordered parameters
        ("def load(path, mode='r',", "def load(path, flags, mode='r',"),  # new, no default
        ("def only(a, b, /, c)", "def only(a, b, c)"),  # positional-only became keyword-able
        ("mode='r', *args", "mode, *args"),  # a default removed (f)
        ("*args, encoding=None", "encoding=None"),  # *args removed (e)
        (", **options)", ")"),  # **kwargs removed (e)
        ("encoding=None", "encoding2=None"),  # keyword-only renamed (d)
        ("encoding=None, **options", "encoding=None, strict, **options"),  # new kw-only, no default
        ("    @staticmethod\n", ""),  # @staticmethod removed (b)
        ("    @property\n", ""),  # @property removed (b)
        ("    async def fetch(", "    def fetch("),  # async def became def (a)
        ("def only(", "async def only("),  # def became async def (a)
        ("class Store:", "def Store():\n    pass\n\n\nclass Other:"),  # class became function
        ("def __init__(self, root, *, cache=False)", "def __init__(self, root, cache)"),
        ("    def build(name):\n        return name\n", ""),  # a public method removed
    ],
)
def test_each_interface_rule_rejects_a_change(old: str, new: str) -> None:
    assert changed(old, new) == REJECTED


@pytest.mark.parametrize(
    ("old", "new"),
    [
        ("def load(path, mode='r', *args", "def load(path, mode='r', strict=False, *args"),
        ("mode='r'", "mode='rb'"),  # a changed default value
        ("encoding=None, **options", "encoding=None, strict=False, **options"),
        ("def _helper(x):", "def _helper(x, y, z):"),  # private helpers are not compared
        ("    def _internal(self):", "    def _internal(self, other):"),
        ("def only(a, b, /, c)", "def only(a, b, /, c, d=1)"),
        ("import os\n", "import os\nimport sys\n"),  # module-level statements are not compared
        ("    @staticmethod\n", "    @builtins.staticmethod\n"),  # matched by simple name
        # Other decorators, in any expression form, are not compared.
        ("    @property\n", "    @registry[0]\n    @cache(maxsize=1)\n    @property\n"),
    ],
)
def test_compatible_changes_are_accepted(old: str, new: str) -> None:
    assert changed(old, new) == VALID


@pytest.mark.parametrize(
    "generated",
    [
        "def broken(:\n    pass\n",
        "x = (\n",
        "if True:\npass\n",  # IndentationError
        "x = 1\x00\n",  # NUL
        "x = " + "-" * 200_000 + "1\n",  # too deeply nested to parse
    ],
    ids=["syntax-error", "unclosed", "indentation", "nul", "too-deep"],
)
def test_generated_code_that_does_not_parse_is_rejected(generated: str) -> None:
    assert validate_generated_code(ORIGINAL, generated) == CodeValidation(
        valid=False, reason=DOES_NOT_PARSE
    )


def test_an_unparseable_original_skips_the_interface_check() -> None:
    broken = SourceText.of("def broken(:\n    pass\n")
    assert validate_generated_code(broken, "def fixed():\n    pass\n") == VALID


def test_generated_code_is_never_executed(tmp_path: Path) -> None:
    marker = tmp_path / "ran.txt"
    generated = (
        "import sys\n"
        "sys.modules['generated_code_ran'] = sys\n"
        f"open({str(marker)!r}, 'w').write('ran')\n"
        "raise SystemExit(7)\n"
    )
    plain = SourceText.of("x = 1\n")
    assert validate_generated_code(plain, generated) == VALID
    assert "generated_code_ran" not in sys.modules
    assert not marker.exists()


def test_code_validation_invariant() -> None:
    with pytest.raises(ValueError):
        CodeValidation(valid=True, reason=DOES_NOT_PARSE)
    with pytest.raises(ValueError):
        CodeValidation(valid=False, reason=None)
